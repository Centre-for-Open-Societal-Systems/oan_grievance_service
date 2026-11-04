# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Where an administrative area sits: its ancestors, and its region, zone, woreda and kebele.

These lived in the administrative area API module. The export service needs them too, and a
service must not depend on the handler layer, so they are here and the API imports them.
"""

import frappe

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	is_in_area_subtree,
)


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


def match_administrative_area(
	region: str | None, woreda: str | None = None, street_address: str | None = None
) -> str | None:
	"""The deepest active Grievance Administrative Area whose names match, walking region, woreda, kebele.

	Each level is looked up under the node matched above it, so a woreda name shared by two
	regions resolves to the right one. The kebele is whichever comma-separated part of
	`street_address` names one ("Kebele 01, House 23"). The walk stops at the first level with
	no match or more than one, such as a woreda name repeated in two zones of a region, and no
	region match returns None.
	"""
	kebeles = [part.strip() for part in (street_address or "").split(",") if part.strip()]
	node = None
	for level, names in (("Region", [region]), ("Woreda", [woreda]), ("Kebele", kebeles)):
		if not any(names):
			break
		filters = {"area_name": ["in", names], "level_name": level, "is_active": 1}
		if node:
			filters.update(lft=[">", node.lft], rgt=["<", node.rgt])
		found = frappe.get_all(
			"Grievance Administrative Area", filters=filters, fields=["name", "lft", "rgt"], limit=2
		)
		if len(found) != 1:
			break
		node = found[0]
	return node.name if node else None


def refreshed_administrative_area(profile: dict, matched: str | None) -> str | None:
	"""The area a submitter profile should hold once its provider address has matched `matched`.

	`administrative_area` is the source of truth for where the submitter is; `locality` and
	`administrative_unit` are text as given. An area the old address matched came from the
	provider, so it follows the provider, down to clearing. Any other area was chosen by the
	submitter: it stays on no match, or when it sits inside the new match.
	"""
	current = profile.get("administrative_area")
	if not current or current == matched:
		return matched
	old_match = match_administrative_area(
		profile.get("region"), profile.get("locality"), profile.get("street_address")
	)
	if current != old_match and (not matched or is_in_area_subtree(current, matched)):
		return current
	return matched
