# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Where an administrative area sits: its ancestors, and its region, zone, woreda and kebele.

These lived in the administrative area API module. The export service needs them too, and a
service must not depend on the handler layer, so they are here and the API imports them.
"""

import frappe


def get_ancestors(area_id_or_path):
	"""Fetch ancestor chain from root down to the specified node."""
	node = None
	if frappe.db.exists("Grievance Administrative Area", area_id_or_path):
		node = frappe.get_doc("Grievance Administrative Area", area_id_or_path)
	else:
		name = frappe.db.get_value("Grievance Administrative Area", {"path_code": area_id_or_path}, "name")
		if name:
			node = frappe.get_doc("Grievance Administrative Area", name)

	if not node:
		return {"breadcrumbs": [], "current": None}

	ancestors = frappe.get_all(
		"Grievance Administrative Area",
		filters=[
			["lft", "<=", node.lft],
			["rgt", ">=", node.rgt],
			["depth", ">", 0],  # skip synthetic World root
		],
		fields=["name as area_id", "area_name", "code", "path_code", "level_name", "depth"],
		order_by="lft asc",
		ignore_permissions=True,
	)

	return {
		"current": {
			"area_id": node.name,
			"area_name": node.area_name,
			"path_code": node.path_code,
			"level_name": node.level_name,
		},
		"breadcrumbs": ancestors,
	}


def get_administrative_hierarchy(area_id_or_path: str | None) -> dict | None:
	"""Return a clean key-value mapping of region, zone, woreda, kebele for an area node."""
	if not area_id_or_path:
		return None
	try:
		ancestors_info = get_ancestors(area_id_or_path)
		breadcrumbs = ancestors_info.get("breadcrumbs") or []
		h = {}
		for b in breadcrumbs:
			lvl = (b.get("level_name") or "").lower()
			if lvl in ("region", "zone", "woreda", "kebele"):
				h[lvl] = b.get("area_name")
				h[f"{lvl}_id"] = b.get("area_id")
		return h if h else None
	except Exception:
		return None


def format_administrative_location(hierarchy_or_area_id: dict | str | None) -> str | None:
	"""Format administrative area into a comma-separated location string from leaf to root (kebele, woreda, zone, region)."""
	if not hierarchy_or_area_id:
		return None
	hierarchy = (
		get_administrative_hierarchy(hierarchy_or_area_id)
		if isinstance(hierarchy_or_area_id, str)
		else hierarchy_or_area_id
	)
	if not hierarchy or not isinstance(hierarchy, dict):
		return None
	parts = []
	for level in ("kebele", "woreda", "zone", "region"):
		name = hierarchy.get(level)
		if name and name not in parts:
			parts.append(name)
	return ", ".join(parts) if parts else None
