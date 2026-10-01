# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GrievanceType(Document):
	pass


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
