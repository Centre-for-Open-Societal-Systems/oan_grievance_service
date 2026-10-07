# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Department management for the Administration Departments screen.

A department is a Grievance Department. Its id is its name, so the id never changes once
the department exists: grievances, category assignments and templates all link to it.
Departments are never deleted. Retiring one clears `active`, and is refused while the
department still has open cases, so no case is stranded on a retired department.

Field checks (email address, head of department) live in the doctype's `validate()`, so
they hold for every writer. This module holds what spans entities: the open-case guard,
the department's uniqueness by short name, and projecting the head's display name.
"""

import frappe
from frappe import _

from oan_grievance_service.services import constants as C

DOCTYPE = "Grievance Department"
FIELDS = [
	"name",
	"dept_name",
	"short_name",
	"email_account",
	"phone",
	"head_of_dept",
	"active",
	"l1_role_level",
	"l2_role_level",
	"routing_strategy",
]
SEARCH_FIELDS = ("dept_name", "short_name", "email_account")


def get_department(name: str):
	"""The department with this id, or DoesNotExistError."""
	if not name or not frappe.db.exists(DOCTYPE, name):
		frappe.throw(_("Department '{0}' was not found.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(DOCTYPE, name)


def records(names: list[str]) -> list[dict]:
	"""Project departments to API records, in the order given, with one query for the heads' names."""
	if not names:
		return []
	rows = {row.name: row for row in frappe.get_all(DOCTYPE, filters={"name": ["in", names]}, fields=FIELDS)}
	heads = {row.head_of_dept for row in rows.values() if row.head_of_dept}
	full_names = {
		row.name: row.full_name
		for row in frappe.get_all("User", filters={"name": ["in", list(heads)]}, fields=["name", "full_name"])
	}
	return [_record(rows[name], full_names) for name in names if name in rows]


def record(name: str) -> dict:
	return records([name])[0]


def _record(row, full_names: dict) -> dict:
	return {
		"department_id": row.name,
		"department_name": row.dept_name,
		"short_name": row.short_name or None,
		"email_account": row.email_account,
		"phone": row.phone or None,
		"head_of_dept": row.head_of_dept or None,
		"head_of_dept_name": full_names.get(row.head_of_dept) if row.head_of_dept else None,
		"active": bool(row.active),
		"l1_role_level": row.l1_role_level or None,
		"l2_role_level": row.l2_role_level or None,
		"routing_strategy": row.routing_strategy or None,
	}


def list_departments(
	*,
	active: bool | None,
	head_of_dept: str | None,
	q: str | None,
	start: int,
	page_size: int,
) -> tuple[list[str], int]:
	"""One page of department ids, by name, and the total across all pages."""
	filters = {}
	if active is not None:
		filters["active"] = 1 if active else 0
	if head_of_dept:
		filters["head_of_dept"] = head_of_dept
	or_filters = [[field, "like", f"%{q}%"] for field in SEARCH_FIELDS] if q else None
	names = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		pluck="name",
		order_by="dept_name asc",
		offset=start,
		limit=page_size,
	)
	total = frappe.get_all(
		DOCTYPE, filters=filters, or_filters=or_filters, fields=[{"COUNT": "name", "as": "total"}]
	)[0].total
	return names, total


def create(
	*,
	department_name: str,
	email_account: str,
	short_name: str | None = None,
	phone: str | None = None,
	head_of_dept: str | None = None,
	active: bool = True,
	l1_role_level: str | None = None,
	l2_role_level: str | None = None,
	routing_strategy: str | None = None,
) -> str:
	"""Create a department. Returns its id."""
	if frappe.db.exists(DOCTYPE, department_name):
		frappe.throw(
			_("A department named '{0}' already exists.").format(department_name),
			frappe.DuplicateEntryError,
		)
	_assert_short_name_free(short_name)
	return (
		frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"dept_name": department_name,
				"email_account": email_account,
				"short_name": short_name,
				"phone": phone,
				"head_of_dept": head_of_dept,
				"active": 1 if active else 0,
				"l1_role_level": l1_role_level,
				"l2_role_level": l2_role_level,
				"routing_strategy": routing_strategy,
			}
		)
		.insert()
		.name
	)


def update(department, changes: dict):
	"""Apply a partial update. Retiring the department is refused while it has open cases."""
	changes = dict(changes)
	if "short_name" in changes and changes["short_name"] != department.short_name:
		_assert_short_name_free(changes["short_name"], excluding=department.name)
	if "active" in changes:
		changes["active"] = 1 if changes["active"] else 0
		if not changes["active"] and department.active:
			_assert_no_open_cases(department.name)
	department.update(changes)
	department.save()


def deactivate(department):
	"""Retire a department. Repeating the call on a retired department is a no-op."""
	if not department.active:
		return
	update(department, {"active": False})


def open_case_count(department: str) -> int:
	return frappe.db.count("Grievance", {"assigned_dept": department, "status": ["in", list(C.OPEN_STATES)]})


def _assert_no_open_cases(department: str):
	open_cases = open_case_count(department)
	if open_cases:
		frappe.throw(
			_(
				"Department '{0}' still has {1} open case(s). Reassign or close them before retiring it."
			).format(department, open_cases),
			frappe.ValidationError,
		)


def _assert_short_name_free(short_name: str | None, excluding: str | None = None):
	"""A short name is also accepted as a department identifier, so two departments cannot share one."""
	if not short_name:
		return
	filters = {"short_name": short_name}
	if excluding:
		filters["name"] = ["!=", excluding]
	existing = frappe.db.get_value(DOCTYPE, filters, "name")
	if existing:
		frappe.throw(
			_("Short name '{0}' is already used by department '{1}'.").format(short_name, existing),
			frappe.DuplicateEntryError,
		)
