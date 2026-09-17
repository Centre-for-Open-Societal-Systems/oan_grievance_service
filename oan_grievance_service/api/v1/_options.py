"""Shared dropdown and reference data helpers for API options endpoints."""

import frappe


def get_service_categories() -> list[dict]:
	"""Retrieve active grievance service categories."""
	return frappe.get_all(
		"Grievance Service Category",
		filters={"is_active": 1},
		fields=["name as category_name", "code", "sort_order"],
		order_by="sort_order asc, name asc",
		ignore_permissions=True,
	)


def get_grievance_types(service_category: str | None = None) -> list[dict]:
	"""Retrieve active grievance types, optionally filtered by service_category."""
	gtype_filters = [["is_active", "=", 1]]
	if service_category:
		gtype_filters.append(["service_category", "=", service_category])

	return frappe.get_all(
		"Grievance Type",
		filters=gtype_filters,
		fields=["name as grievance_type_id", "type_name", "service_category"],
		order_by="type_name asc",
		ignore_permissions=True,
	)
