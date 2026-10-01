# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Resolve a client-supplied identifier (doc name, code, or display name) to a canonical doc name."""

import frappe
from frappe import _


def resolve_administrative_area(area_identifier):
	"""Resolve an area identifier (ID, path_code, or unique code) to canonical doc name.

	Note: area_name is intentionally excluded for lower-level tiers because display names
	recur across regions/woredas (e.g. over 100 kebeles named '1' or '2'). For Region tier,
	display names are unique across the country and safe to match.
	"""
	if not area_identifier:
		return None
	if frappe.db.exists("Grievance Administrative Area", area_identifier):
		return area_identifier
	return (
		frappe.db.get_value("Grievance Administrative Area", {"path_code": area_identifier}, "name")
		or frappe.db.get_value("Grievance Administrative Area", {"code": area_identifier}, "name")
		or frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": area_identifier, "level_name": "Region"}, "name"
		)
	)


def resolve_grievance_type(type_identifier: str | None, category: str | None = None) -> str | None:
	"""Resolve a grievance type identifier (DocType name or display type_name) to canonical doc name.

	None when nothing matches. The caller decides whether that is an error; handing
	back the raw input instead would let an unknown string reach a Link field.
	"""
	if not type_identifier:
		return None
	type_identifier = str(type_identifier).strip()
	if frappe.db.exists("Grievance Type", type_identifier):
		return type_identifier
	filters = {"type_name": type_identifier}
	if category:
		filters["service_category"] = category
	resolved = frappe.db.get_value("Grievance Type", filters, "name")
	if resolved:
		return resolved
	return frappe.db.get_value("Grievance Type", {"type_name": type_identifier}, "name")


def resolve_service_category(value: str) -> str:
	"""Canonical Grievance Service Category name by id, category_name, or code. Throws when none match."""
	if frappe.db.exists("Grievance Service Category", value):
		return value
	name = frappe.db.get_value(
		"Grievance Service Category", {"category_name": value}, "name"
	) or frappe.db.get_value("Grievance Service Category", {"code": value}, "name")
	if not name:
		frappe.throw(_("Service category '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def resolve_department(value: str) -> str:
	"""Canonical Grievance Department name by id, dept_name, or short_name. Throws when none match."""
	if frappe.db.exists("Grievance Department", value):
		return value
	name = frappe.db.get_value("Grievance Department", {"dept_name": value}, "name") or frappe.db.get_value(
		"Grievance Department", {"short_name": value}, "name"
	)
	if not name:
		frappe.throw(_("Department '{0}' does not exist.").format(value), frappe.ValidationError)
	return name
