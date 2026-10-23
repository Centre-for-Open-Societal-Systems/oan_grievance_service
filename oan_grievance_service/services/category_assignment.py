# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Category assignment workflow: one category-only Grievance RBAC Assignment per (department, category).

The desk is the source of truth for officers. Field and link checks (officer roles, active
links, duplicate desks) live in the desk's own `validate()`, so they hold for every writer.
This module holds what spans entities: projecting a rule onto the desk's officer rows
and writing the category's Grievance SLA Configuration.

The SLA row is per category, not per desk. If departments A and B both serve category X,
changing `sla_days` through A's rule changes B's too.
"""

import frappe
from frappe import _
from frappe.utils import today

from oan_grievance_service.grievance_sla.doctype.grievance_sla_configuration.grievance_sla_configuration import (
	save_sla_configuration,
)
from oan_grievance_service.services.constants import NON_ASSIGNEE_LEVELS
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


def serving_filters(**extra) -> dict:
	"""Filters that select any active desk serving a category or department. Extra keys narrow or override them."""
	return {
		"active": 1,
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


L1_ROLE_LEVEL = "nodal_officer"
L2_ROLE_LEVEL = "senior_nodal_officer"


def split_officers(rows: list, l1_level: str = L1_ROLE_LEVEL, l2_level: str = L2_ROLE_LEVEL):
	"""The L1 and L2 seats of a desk, found by role level and not by position.

	A desk can hold many officers, so list order says nothing about who is L2. The primary is
	an active officer below the L2 level, one at the L1 level and an is_primary one first. The
	secondary is an active officer at the L2 level: the one the primary reports to, else a
	non-primary one, else the first. Anyone else is left alone.
	"""

	def is_l2(row) -> bool:
		return bool(l2_level) and row.role_level == l2_level

	active = [row for row in rows if row.active and row.role_level not in NON_ASSIGNEE_LEVELS]
	firsts = sorted(
		(row for row in active if not is_l2(row)),
		key=lambda row: (row.role_level != l1_level, not row.is_primary),
	)
	primary = firsts[0] if firsts else None
	supervisor = primary.reports_to if primary else None
	seconds = sorted(
		(row for row in active if is_l2(row)), key=lambda row: (row.user != supervisor, bool(row.is_primary))
	)
	return primary, (seconds[0] if seconds else None)


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
	department: str | None = None,
	l1_officer: str | None = None,
	l2_officer: str | None = None,
	sla_days: int | None = None,
	auto_escalate: bool = True,
	active: bool = True,
):
	"""Create the desk, and set the category's SLA when `sla_days` is given. Returns the desk."""
	state = {
		"service_category": resolve_service_category(service_category),
		"department": resolve_department(department) if department else None,
		"l1_officer": l1_officer,
		"l2_officer": l2_officer,
		"sla_days": sla_days,
		"auto_escalate": auto_escalate,
		"active": active,
	}
	_validate_officers(state)
	desk = frappe.new_doc(DOCTYPE)
	desk.effective_from = today()
	desk.category_scope = state["service_category"]
	_write_desk(desk, state, seats=(None, None))
	desk.insert()
	if sla_days is not None:
		save_sla_configuration(state["service_category"], sla_days, auto_escalate)
	return desk


def open_desk(service_category: str, department: str | None, officer: dict):
	"""Create the category-only desk of `department` for `service_category` with one officer row on it."""
	desk = frappe.new_doc(DOCTYPE)
	desk.update(
		{
			"category_scope": service_category,
			"department_scope": department,
			"effective_from": today(),
			"routing_strategy": "Primary First",
			"assigned_by": frappe.session.user,
			"active": 1,
		}
	)
	desk.append("officers", officer)
	desk.insert()
	return desk


def update(desk, changes: dict):
	"""Apply a partial update. Deactivation alone skips the officer checks so a broken desk can be retired."""
	if changes == {"active": False}:
		desk.active = 0
		desk.assigned_by = frappe.session.user
		desk.save()
		return
	# An SLA-only change leaves the officer rows alone: the desk may hold more officers than
	# the one L1 and one L2 this API shows, and they belong to the Nodal Officers tab.
	if DESK_FIELDS & changes.keys():
		_update_desk(desk, changes)
	else:
		desk.assigned_by = frappe.session.user
	desk.save()
	if {"sla_days", "auto_escalate"} & changes.keys():
		sla = active_sla_rows([desk.category_scope]).get(desk.category_scope)
		sla_days = changes.get("sla_days", sla.sla_days if sla else None)
		# A category with no SLA yet gets one only once sla_days is given.
		if sla_days is not None:
			save_sla_configuration(
				desk.category_scope,
				sla_days,
				changes.get("auto_escalate", bool(sla.auto_escalate) if sla else True),
			)


def _update_desk(desk, changes: dict):
	"""Merge the changes into the desk's current L1 and L2 seats and write them back."""
	if "department" in changes:
		department = resolve_department(changes["department"]) if changes["department"] else None
	else:
		department = desk.department_scope
	seats = split_officers(desk.officers)
	primary, secondary = seats
	state = {
		"l1_officer": primary.user if primary else None,
		"l2_officer": secondary.user if secondary else None,
		"active": bool(desk.active),
		**changes,
		"department": department,
	}
	_validate_officers(state)
	_write_desk(desk, state, seats)


def _validate_officers(state: dict):
	"""Check the request against itself."""
	l1, l2 = state.get("l1_officer"), state.get("l2_officer")
	if l2 and l1 == l2:
		frappe.throw(_("L1 and L2 officers must be different users."), frappe.ValidationError)


def _write_desk(desk, state: dict, seats: tuple):
	"""Copy state onto the desk. Existing officer rows are updated in place.

	A desk can hold many officers, added on the Nodal Officers tab. This API names one L1 and
	one L2, so it replaces only those two seats (`seats`, as they stood before this change):
	an officer in neither seat is never removed.
	"""
	desk.department_scope = state["department"]
	desk.assigned_by = frappe.session.user
	desk.active = 1 if state["active"] else 0
	if not getattr(desk, "routing_strategy", None):
		desk.routing_strategy = "Primary First"
	# `reports_to` names an officer's supervisor: escalation hands a case up to it, and an
	# officer sees the cases of everyone who reports to them. So L1 reports to L2, never the
	# reverse. L2 is the escalation tier and has no supervisor on this desk.
	l1, l2 = state.get("l1_officer"), state.get("l2_officer")
	wanted = {l1: {"role_level": L1_ROLE_LEVEL, "is_primary": 1, "reports_to": l2}} if l1 else {}
	if l2:
		wanted[l2] = {"role_level": L2_ROLE_LEVEL, "is_primary": 0, "reports_to": None}
	vacated = {seat.user for seat in seats if seat and seat.user not in wanted}
	_assert_nobody_reports_to(desk, vacated, keep=wanted)
	for row in list(desk.officers):
		if row.user in vacated:
			desk.remove(row)
	rows = {row.user: row for row in desk.officers}
	for user, values in wanted.items():
		row = rows.get(user) or desk.append("officers", {"user": user})
		row.update({**values, "active": 1})


def _assert_nobody_reports_to(desk, users: set, keep: dict):
	"""Refuse to vacate a seat whose officer still has active officers reporting to them on this desk.

	An inactive officer is ignored: they no longer take cases, and keeping them from a
	replacement would leave the seat stuck for good.
	"""
	stranded = [
		row.user
		for row in desk.officers
		if row.active and row.reports_to in users and row.user not in users | keep.keys()
	]
	if stranded:
		frappe.throw(
			_("Officers {0} report to an officer this change removes. Move them first.").format(
				", ".join(stranded)
			),
			frappe.ValidationError,
		)
