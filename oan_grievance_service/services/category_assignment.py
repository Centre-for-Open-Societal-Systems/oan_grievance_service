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


def desk_filters(**extra) -> dict:
	"""Filters that select category-only desks. Extra keys narrow or override them."""
	return {
		"category_scope": ["is", "set"],
		"administrative_area_scope": ["is", "not set"],
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


def split_officers(rows: list):
	"""Primary is the active is_primary row, else the first active row. Secondary is the next non-primary."""
	active = [row for row in rows if row.active]
	primary = next((row for row in active if row.is_primary), active[0] if active else None)
	secondary = next((row for row in active if row is not primary and not row.is_primary), None)
	return primary, secondary


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
	prefs = _department_prefs(state)
	_write_desk(desk, state, prefs)
	desk.save()
	if {"sla_days", "auto_escalate"} & changes.keys():
		_save_sla(desk.category_scope, state["sla_days"], state["auto_escalate"])


def _current_state(desk) -> dict:
	primary, secondary = split_officers(desk.officers)
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
	"""Copy state onto the desk. Existing officer rows are updated in place."""
	desk.department_scope = state["department"]
	desk.assigned_by = frappe.session.user
	desk.active = 1 if state["active"] else 0
	if prefs.routing_strategy:
		desk.routing_strategy = prefs.routing_strategy
	# `reports_to` names an officer's supervisor: escalation hands a case up to it, and an
	# officer sees the cases of everyone who reports to them. So L1 reports to L2, never the
	# reverse. L2 is the escalation tier and has no supervisor on this desk.
	l2 = state.get("l2_officer")
	wanted = {state["l1_officer"]: {"role_level": prefs.l1_role_level, "is_primary": 1, "reports_to": l2}}
	if l2:
		wanted[l2] = {"role_level": prefs.l2_role_level, "is_primary": 0, "reports_to": None}
	for row in list(desk.officers):
		if row.user not in wanted:
			desk.remove(row)
	rows = {row.user: row for row in desk.officers}
	for user, values in wanted.items():
		row = rows.get(user) or desk.append("officers", {"user": user})
		row.update({**values, "active": 1})


def _save_sla(service_category: str, sla_days: int, auto_escalate: bool):
	row = active_sla_rows([service_category]).get(service_category)
	sla = frappe.get_doc(SLA_DOCTYPE, row.name) if row else frappe.new_doc(SLA_DOCTYPE)
	sla.service_category = service_category
	sla.sla_days = sla_days
	sla.auto_escalate = 1 if auto_escalate else 0
	sla.active = 1
	sla.save()
