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


# NOTE: Dynamic role level lookup via _default_role_levels is currently retained for backward compatibility.
# In a future refactor, prefer resolving seats structurally via the reporting chain (L1 via is_primary,
# L2 via primary.reports_to) and using static constants ("nodal_officer", "senior_nodal_officer")
# instead of querying Grievance Role Level on each call.
def _default_role_levels() -> tuple[str, str]:
	"""Returns the (L1, L2) role levels for category assignment desks."""
	l1 = "nodal_officer" if frappe.db.exists("Grievance Role Level", "nodal_officer") else None
	l2 = "senior_nodal_officer" if frappe.db.exists("Grievance Role Level", "senior_nodal_officer") else None
	if not l1 or not l2:
		levels = frappe.get_all(
			"Grievance Role Level",
			filters={"is_active": 1},
			fields=["name"],
			order_by="level_order asc",
			limit=2,
		)
		l1 = l1 or (levels[0].name if levels else "nodal_officer")
		l2 = l2 or (levels[1].name if len(levels) > 1 else "senior_nodal_officer")
	return l1, l2


def role_levels(departments) -> dict:
	"""(L1 role level, L2 role level) for category assignment desks."""
	l1, l2 = _default_role_levels()
	return {name: (l1, l2) for name in departments if name}


def split_officers(rows: list, l1_level: str | None = None, l2_level: str | None = None):
	"""The L1 and L2 seats of a desk, found by role level and not by position.

	A desk can hold many officers, so list order says nothing about who is L2. The primary is
	an active officer below the L2 level, one at the L1 level and an is_primary one first. The
	secondary is an active officer at the L2 level: the one the primary reports to, else a
	non-primary one, else the first. Anyone else is left alone.
	"""
	if not l1_level or not l2_level:
		def_l1, def_l2 = _default_role_levels()
		l1_level = l1_level or def_l1
		l2_level = l2_level or def_l2

	def is_l2(row) -> bool:
		return bool(l2_level) and row.role_level == l2_level

	active = [row for row in rows if row.active]
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
	_validate_officers(state)
	desk = frappe.new_doc(DOCTYPE)
	desk.effective_from = today()
	desk.category_scope = state["service_category"]
	_write_desk(desk, state, seats=(None, None))
	desk.insert()
	_save_sla(state["service_category"], sla_days, auto_escalate)
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
		_save_sla(
			desk.category_scope,
			changes.get("sla_days", sla.sla_days if sla else None),
			changes.get("auto_escalate", bool(sla.auto_escalate) if sla else None),
		)


def _update_desk(desk, changes: dict):
	"""Merge the changes into the desk's current L1 and L2 seats and write them back."""
	department = (
		resolve_department(changes["department"]) if "department" in changes else desk.department_scope
	)
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
	l1, l2 = state["l1_officer"], state.get("l2_officer")
	if not l1:
		frappe.throw(_("An L1 officer is required."), frappe.ValidationError)
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
	l1_role, l2_role = _default_role_levels()
	l1, l2 = state["l1_officer"], state.get("l2_officer")
	wanted = {l1: {"role_level": l1_role, "is_primary": 1, "reports_to": l2}}
	if l2:
		wanted[l2] = {"role_level": l2_role, "is_primary": 0, "reports_to": None}
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


def _save_sla(service_category: str, sla_days: int, auto_escalate: bool):
	row = active_sla_rows([service_category]).get(service_category)
	sla = frappe.get_doc(SLA_DOCTYPE, row.name) if row else frappe.new_doc(SLA_DOCTYPE)
	sla.service_category = service_category
	sla.sla_days = sla_days
	sla.auto_escalate = 1 if auto_escalate else 0
	sla.active = 1
	sla.save()
