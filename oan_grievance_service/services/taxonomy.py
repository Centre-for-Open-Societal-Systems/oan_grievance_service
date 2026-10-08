# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Service categories and grievance types: the write side of the taxonomy.

Categories and their types are reference data. The submission wizard offers the active
ones (`api/v1/_options.py`), a category assignment routes on a category, and a response
template may be scoped to one. Nothing here deletes: a category or type that has been
used stays on the grievances filed under it, so retiring one only clears its `is_active`
flag and every reader that filters on the flag follows.

The rules that keep the readers consistent live in the doctype controllers, so they hold
for the Desk as well as the API: a category's ticket code is Base32 and frozen once
tickets exist; retiring a category retires its types; a type is unique by name and code
within its category and cannot be created or reactivated under an inactive one. What
this module adds is what spans doctypes: resolving identifiers, renaming a category with
its links, and counting what refers to each record.
"""

import frappe
from frappe import _

from oan_grievance_service.services import constants as C
from oan_grievance_service.services.resolvers import resolve_service_category

CATEGORY = "Grievance Service Category"
TYPE = "Grievance Type"
ASSIGNMENT = "Grievance RBAC Assignment"
TEMPLATE = "Grievance Response Template"
GRIEVANCE = "Grievance"

CATEGORY_FIELDS = ["name", "category_name", "code", "sort_order", "is_active", "is_default"]
TYPE_FIELDS = ["name", "type_name", "code", "service_category", "is_active"]


# Lookups
# -------


def default_category() -> str | None:
	"""The category unclassified cases are filed under: the one flagged default.

	Sites installed before the flag existed get it from the `set_default_service_category`
	patch; until then the seeded catch-all stands in.
	"""
	return frappe.db.get_value(CATEGORY, {"is_default": 1}, "name") or (
		C.FALLBACK_SERVICE_CATEGORY if frappe.db.exists(CATEGORY, C.FALLBACK_SERVICE_CATEGORY) else None
	)


def fallback_type(category: str | None) -> str | None:
	"""The type an unclassified case gets inside `category`: its catch-all, else its first active type."""
	if not category:
		return None
	return frappe.db.get_value(
		TYPE,
		{"service_category": category, "type_name": C.FALLBACK_GRIEVANCE_TYPE, "is_active": 1},
		"name",
	) or frappe.db.get_value(
		TYPE, {"service_category": category, "is_active": 1}, "name", order_by="type_name asc"
	)


def get_category(identifier: str):
	"""The category with this name or ticket code, or DoesNotExistError."""
	name = frappe.db.exists(CATEGORY, identifier) or frappe.db.get_value(
		CATEGORY, {"code": str(identifier).strip().upper()}, "name"
	)
	if not name:
		frappe.throw(_("Service category '{0}' was not found.").format(identifier), frappe.DoesNotExistError)
	return frappe.get_doc(CATEGORY, name)


def get_type(grievance_type: str):
	"""The grievance type with this id, or DoesNotExistError."""
	if not frappe.db.exists(TYPE, grievance_type):
		frappe.throw(
			_("Grievance type '{0}' was not found.").format(grievance_type), frappe.DoesNotExistError
		)
	return frappe.get_doc(TYPE, grievance_type)


# Service categories
# ------------------


def list_categories(
	*, is_active: bool | None, search: str | None, start: int, page_size: int
) -> tuple[list, int]:
	"""One page of categories in display order, and how many match."""
	filters = {} if is_active is None else {"is_active": 1 if is_active else 0}
	or_filters = _search_filters(CATEGORY, search, ("category_name", "code"))
	rows = frappe.get_all(
		CATEGORY,
		filters=filters,
		or_filters=or_filters,
		fields=CATEGORY_FIELDS,
		order_by="sort_order asc, category_name asc",
		offset=start,
		limit_page_length=page_size,
	)
	return rows, _count(CATEGORY, filters, or_filters)


def create_category(
	*, category_name: str, code: str, sort_order: int | None, is_active: bool, is_default: bool = False
):
	"""Create a category. Without a sort order it goes to the end of the list.

	A new default takes the flag from the previous one.
	"""
	if sort_order is None:
		top = frappe.get_all(CATEGORY, fields=[{"MAX": "sort_order", "as": "top"}])
		sort_order = (top[0].top or 0) + 1
	return frappe.get_doc(
		{
			"doctype": CATEGORY,
			"category_name": category_name,
			"code": code,
			"sort_order": sort_order,
			"is_active": 1 if is_active else 0,
			"is_default": 1 if is_default else 0,
		}
	).insert()


def update_category(doc, changes: dict):
	"""Apply a partial update. A new name renames the record, and every link to it follows."""
	new_name = changes.pop("category_name", None)
	for fieldname, value in changes.items():
		doc.set(fieldname, int(bool(value)) if fieldname in ("is_active", "is_default") else value)
	doc.save()
	if new_name and new_name != doc.name:
		frappe.rename_doc(CATEGORY, doc.name, new_name)
		doc = frappe.get_doc(CATEGORY, new_name)
	return doc


def deactivate_category(doc):
	"""Retire a category and, with it, its types. Repeating the call changes nothing."""
	if doc.is_active:
		doc.is_active = 0
		doc.save()
	return doc


def category_records(rows: list) -> list[dict]:
	"""Project categories to API records, with one query per count for the whole page."""
	names = [row.name for row in rows]
	types = _counts(TYPE, "service_category", names, is_active=1)
	assignments = _counts(ASSIGNMENT, "category_scope", names, active=1)
	templates = _counts(TEMPLATE, "service_category", names, is_active=1)
	grievances = _counts(GRIEVANCE, "service_category", names)
	ticketed = _counts(GRIEVANCE, "service_category", names, ticket_number=["is", "set"])
	return [
		{
			"category_name": row.category_name,
			"code": row.code,
			"sort_order": row.sort_order or 0,
			"is_active": bool(row.is_active),
			"is_default": bool(row.is_default),
			"grievance_type_count": types.get(row.name, 0),
			"assignment_count": assignments.get(row.name, 0),
			"response_template_count": templates.get(row.name, 0),
			"grievance_count": grievances.get(row.name, 0),
			"code_locked": ticketed.get(row.name, 0) > 0,
		}
		for row in rows
	]


# Grievance types
# ---------------


def list_types(
	*,
	service_category: str | None,
	is_active: bool | None,
	search: str | None,
	start: int,
	page_size: int,
) -> tuple[list, int]:
	"""One page of types by name, and how many match."""
	filters = {} if is_active is None else {"is_active": 1 if is_active else 0}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	or_filters = _search_filters(TYPE, search, ("type_name", "code"))
	rows = frappe.get_all(
		TYPE,
		filters=filters,
		or_filters=or_filters,
		fields=TYPE_FIELDS,
		order_by="type_name asc, name asc",
		offset=start,
		limit_page_length=page_size,
	)
	return rows, _count(TYPE, filters, or_filters)


def create_type(*, service_category: str, type_name: str, code: str, is_active: bool):
	"""Create a type under a category."""
	return frappe.get_doc(
		{
			"doctype": TYPE,
			"service_category": resolve_service_category(service_category),
			"type_name": type_name,
			"code": code,
			"is_active": 1 if is_active else 0,
		}
	).insert()


def update_type(doc, changes: dict):
	"""Apply a partial update. The category is fixed once the type is made."""
	for fieldname, value in changes.items():
		doc.set(fieldname, int(bool(value)) if fieldname == "is_active" else value)
	doc.save()
	return doc


def deactivate_type(doc):
	"""Retire a type. Repeating the call changes nothing."""
	if doc.is_active:
		doc.is_active = 0
		doc.save()
	return doc


def type_records(rows: list) -> list[dict]:
	"""Project types to API records, with one query for the whole page's usage."""
	grievances = _counts(GRIEVANCE, "grievance_type", [row.name for row in rows])
	return [
		{
			"grievance_type_id": row.name,
			"type_name": row.type_name,
			"code": row.code,
			"service_category": row.service_category,
			"is_active": bool(row.is_active),
			"grievance_count": grievances.get(row.name, 0),
		}
		for row in rows
	]


# Helpers
# -------


def _search_filters(doctype: str, search: str | None, fieldnames: tuple[str, ...]) -> list | None:
	"""Match the text in any of the fields, or None when there is no text."""
	text = (search or "").strip()
	if not text:
		return None
	return [[doctype, name, "like", f"%{text}%"] for name in fieldnames]


def _count(doctype: str, filters: dict, or_filters: list | None) -> int:
	rows = frappe.get_all(
		doctype, filters=filters, or_filters=or_filters, fields=[{"COUNT": "*", "as": "total"}]
	)
	return int(rows[0].total) if rows else 0


def _counts(doctype: str, fieldname: str, names: list[str], **filters) -> dict[str, int]:
	"""Rows per value of `fieldname` among `names`, in one grouped query."""
	if not names:
		return {}
	rows = frappe.get_all(
		doctype,
		filters={fieldname: ["in", names], **filters},
		fields=[fieldname, {"COUNT": "*", "as": "total"}],
		group_by=fieldname,
	)
	return {row[fieldname]: int(row.total) for row in rows}
