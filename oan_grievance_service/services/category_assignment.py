# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Category assignment workflow: one category-only Grievance RBAC Assignment per (department, category).

The desk is the source of truth for officers. Field and link checks (officer roles, active
links, duplicate desks) live in the desk's own `validate()`, so they hold for every writer.
This module holds what spans entities: projecting a rule onto the desk's officer rows,
reading the department's role levels, and writing the category's Grievance SLA Configuration.

The SLA row is per category, not per desk. If departments A and B both serve category X,
changing `sla_days` through A's rule changes B's too.
"""

import frappe
from frappe import _
from frappe.utils import today

from oan_grievance_service.services.resolvers import resolve_department, resolve_service_category

DOCTYPE = "Grievance RBAC Assignment"
SLA_DOCTYPE = "Grievance SLA Configuration"
SLA_ORDER = "modified desc, name desc"
# Request fields that change the desk itself. The rest change the category's SLA row.
DESK_FIELDS = {"department", "l1_officer", "l2_officer", "active"}


def desk_filters(**extra) -> dict:
	"""Filters that select category-only desks. Extra keys narrow or override them."""
	return {
		"category_scope": ["is", "set"],
		"grievance_type_scope": ["is", "not set"],
		"service_provider_scope": ["is", "not set"],
		**extra,
	}


def get_desk(name: str):
	"""The category-only desk with this name, or DoesNotExistError."""
	if not name or not frappe.db.exists(DOCTYPE, desk_filters(name=name)):
		frappe.throw(
			_("Category assignment '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	return frappe.get_doc(DOCTYPE, name)


def role_levels(departments) -> dict:
	"""(L1 role level, L2 role level) for each department, in one query."""
	departments = [name for name in departments if name]
	if not departments:
		return {}
	return {
		row.name: (row.l1_role_level, row.l2_role_level)
		for row in frappe.get_all(
			"Grievance Department",
			filters={"name": ["in", departments]},
			fields=["name", "l1_role_level", "l2_role_level"],
		)
	}


def split_officers(rows: list, l1_level: str | None, l2_level: str | None):
	"""The L1 and L2 seats of a desk, found by role level and not by position.

	A desk can hold many officers, so list order says nothing about who is L2. The secondary
	is the active officer at the department's L2 role level. The primary is an active officer
	at its L1 role level, an is_primary one first, else the first. Anyone else is left alone.
	"""
	active = [row for row in rows if row.active]
	secondary = next((row for row in active if l2_level and row.role_level == l2_level), None)
	rest = [row for row in active if row is not secondary]
	rest.sort(key=lambda row: (row.role_level != l1_level, not row.is_primary))
	return (rest[0] if rest else None), secondary


def desk_seats(desk):
	"""The (primary, secondary) officer rows of a desk, read with its department's role levels."""
	levels = role_levels([desk.department_scope]).get(desk.department_scope, (None, None))
	return split_officers(desk.officers, *levels)


def active_sla_rows(categories) -> dict:
	"""The SLA row runtime uses for each category: active, latest modified first.

	`sla.resolve_policy` reads the same way, so what the API shows and edits is what routing
	enforces even if legacy data holds more than one active row for a category.
	"""
	rows = frappe.get_all(
		SLA_DOCTYPE,
		filters={"service_category": ["in", list(categories)], "active": 1},
		fields=["name", "service_category", "sla_days", "auto_escalate"],
		order_by=SLA_ORDER,
	)
	by_category = {}
	for row in rows:
		by_category.setdefault(row.service_category, row)
	return by_category


def create(
	*,
	service_category: str,
	department: str,
	l1_officer: str,
	l2_officer: str | None,
	sla_days: int,
	auto_escalate: bool,
	active: bool,
):
	"""Create the desk and set the category's SLA. Returns the desk."""
	state = {
		"service_category": resolve_service_category(service_category),
		"department": resolve_department(department),
		"l1_officer": l1_officer,
		"l2_officer": l2_officer,
		"sla_days": sla_days,
		"auto_escalate": auto_escalate,
		"active": active,
	}
	prefs = _department_prefs(state)
	desk = frappe.new_doc(DOCTYPE)
	desk.effective_from = today()
	desk.category_scope = state["service_category"]
	_write_desk(desk, state, prefs)
	desk.insert()
	_save_sla(state["service_category"], sla_days, auto_escalate)
	return desk


def update(desk, changes: dict):
	"""Apply a partial update. Deactivation alone skips the department checks so a broken desk can be retired."""
	if changes == {"active": False}:
		desk.active = 0
		desk.assigned_by = frappe.session.user
		desk.save()
		return
	state = {**_current_state(desk), **changes}
	if "department" in changes:
		state["department"] = resolve_department(changes["department"])
	# An SLA-only change leaves the officer rows alone: the desk may hold more officers than
	# the one L1 and one L2 this API shows, and they belong to the Nodal Officers tab.
	if DESK_FIELDS & changes.keys():
		_write_desk(desk, state, _department_prefs(state))
	else:
		desk.assigned_by = frappe.session.user
	desk.save()
	if {"sla_days", "auto_escalate"} & changes.keys():
		_save_sla(desk.category_scope, state["sla_days"], state["auto_escalate"])


def _current_state(desk) -> dict:
	primary, secondary = desk_seats(desk)
	sla = active_sla_rows([desk.category_scope]).get(desk.category_scope)
	return {
		"service_category": desk.category_scope,
		"department": desk.department_scope,
		"l1_officer": primary.user if primary else None,
		"l2_officer": secondary.user if secondary else None,
		"sla_days": sla.sla_days if sla else None,
		"auto_escalate": bool(sla.auto_escalate) if sla else None,
		"active": bool(desk.active),
	}


def _department_prefs(state: dict):
	"""Check the request against itself and the department. Returns the department's routing preferences."""
	l1, l2 = state["l1_officer"], state.get("l2_officer")
	if not l1:
		frappe.throw(_("An L1 officer is required."), frappe.ValidationError)
	if l2 and l1 == l2:
		frappe.throw(_("L1 and L2 officers must be different users."), frappe.ValidationError)
	prefs = frappe.db.get_value(
		"Grievance Department",
		state["department"],
		["l1_role_level", "l2_role_level", "routing_strategy"],
		as_dict=True,
	)
	if not prefs.l1_role_level:
		frappe.throw(_("Department must set an L1 role level."), frappe.ValidationError)
	if l2 and not prefs.l2_role_level:
		frappe.throw(_("Department must set an L2 role level."), frappe.ValidationError)
	return prefs


def _write_desk(desk, state: dict, prefs):
	"""Copy state onto the desk. Existing officer rows are updated in place.

	A desk can hold many officers, added on the Nodal Officers tab. This API names one L1 and
	one L2, so it replaces only those two seats: an officer in neither seat is never removed.
	"""
	# Read the seats before the department changes: the rows carry the old department's levels.
	primary, secondary = desk_seats(desk)
	desk.department_scope = state["department"]
	desk.assigned_by = frappe.session.user
	desk.active = 1 if state["active"] else 0
	if prefs.routing_strategy:
		desk.routing_strategy = prefs.routing_strategy
	l1, l2 = state["l1_officer"], state.get("l2_officer")
	# `reports_to` names an officer's supervisor: escalation hands a case up to it, and an
	# officer sees the cases of everyone who reports to them. So L1 reports to L2, never the
	# reverse. L2 is the escalation tier and has no supervisor on this desk.
	wanted = {l1: {"role_level": prefs.l1_role_level, "is_primary": 1}}
	if l2:
		wanted[l2] = {"role_level": prefs.l2_role_level, "is_primary": 0, "reports_to": None}
	vacated = {seat.user for seat in (primary, secondary) if seat and seat.user not in wanted}
	_assert_nobody_reports_to(desk, vacated, keep=wanted)
	for row in list(desk.officers):
		if row.user in vacated:
			desk.remove(row)
	rows = {row.user: row for row in desk.officers}
	# The L1 seat must report to the L2 seat whenever either one changes, including when the
	# L1 seat moves to an officer who is already on the desk and may report to someone else.
	l1_changed = (primary.user if primary else None) != l1
	l2_changed = (secondary.user if secondary else None) != l2
	for user, values in wanted.items():
		row = rows.get(user)
		if not row:
			row = desk.append("officers", {"user": user})
			values = {**values, "reports_to": l2} if user == l1 else values
		elif user == l1 and (l1_changed or l2_changed):
			values = {**values, "reports_to": l2}
		row.update({**values, "active": 1})


def _assert_nobody_reports_to(desk, users: set, keep: dict):
	"""Refuse to vacate a seat whose officer still has others reporting to them on this desk."""
	stranded = [
		row.user for row in desk.officers if row.reports_to in users and row.user not in users | keep.keys()
	]
	if stranded:
		frappe.throw(
			_("Officers {0} report to an officer this change removes. Move them first.").format(
				", ".join(stranded)
			),
			frappe.ValidationError,
		)


def _save_sla(service_category: str, sla_days: int, auto_escalate: bool):
	row = active_sla_rows([service_category]).get(service_category)
	sla = frappe.get_doc(SLA_DOCTYPE, row.name) if row else frappe.new_doc(SLA_DOCTYPE)
	sla.service_category = service_category
	sla.sla_days = sla_days
	sla.auto_escalate = 1 if auto_escalate else 0
	sla.active = 1
	sla.save()
