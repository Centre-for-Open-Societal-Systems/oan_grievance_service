"""Administrative Area cascading lookup and search endpoints for mobile apps, web wizards and public registration."""

import frappe
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, success_response

route = prefixed("/api/v1/administrative-areas")


@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"", methods=("GET",), allow_guest=True, summary="Fetch administrative areas"
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def get_areas(
	parent: str | None = None,
	level_name: str | None = None,
	search: str | None = None,
	ancestors_of: str | None = None,
	limit: int = 100,
):
	"""Public endpoint to fetch administrative areas for cascading dropdowns and searches.

	Query Modes:
	    1. Cascading Drill-Down:
	       - No parent provided: returns all top-level Regions (level_name='Region').
	       - parent provided (e.g. 'region-ET14' or path_code 'ET.ET14'): returns immediate child nodes.
	    2. Level Filter:
	       - level_name provided (e.g. 'Region', 'Zone', 'Woreda', 'Kebele').
	    3. Free-Text Search:
	       - search provided: searches across area_name, code, or path_code.
	    4. Ancestor Breadcrumbs:
	       - ancestors_of provided (area ID or path_code): returns the chain from Country down to the node.

	Args:
	    parent (str, optional): Name or path_code of parent area.
	    level_name (str, optional): Hierarchy tier (Region, Zone, Woreda, Kebele).
	    search (str, optional): Search query matching area name or code.
	    ancestors_of (str, optional): Area name or path_code to fetch ancestor hierarchy for.
	    limit (int, optional): Max records returned (default 100, max 500).

	Returns:
	    List of administrative area dictionaries with ID, area_name, code, path_code, level_name, is_group.
	"""
	limit = min(int(limit or 100), 500)

	# Mode 4: Ancestor chain for breadcrumb rendering
	if ancestors_of:
		return success_response(data=get_ancestors(ancestors_of))

	filters = []

	# Resolve parent ID if a path_code, code, or area_name was passed
	if parent:
		parent_doc = None
		if frappe.db.exists("Grievance Administrative Area", parent):
			parent_doc = frappe.get_doc("Grievance Administrative Area", parent)
		else:
			name = (
				frappe.db.get_value("Grievance Administrative Area", {"path_code": parent}, "name")
				or frappe.db.get_value("Grievance Administrative Area", {"code": parent}, "name")
				or frappe.db.get_value("Grievance Administrative Area", {"area_name": parent}, "name")
			)
			if name:
				parent_doc = frappe.get_doc("Grievance Administrative Area", name)
				parent = name

		if parent_doc:
			if level_name and parent_doc.level_name != level_name:
				filters.append(["lft", ">", parent_doc.lft])
				filters.append(["rgt", "<", parent_doc.rgt])
			else:
				filters.append(["parent_administrative_area", "=", parent_doc.name])
		else:
			filters.append(["parent_administrative_area", "=", parent])
	elif not search and not level_name:
		# Default root view: Top-level Regions
		filters.append(["level_name", "=", "Region"])

	if level_name:
		filters.append(["level_name", "=", level_name])

	if search:
		search_term = f"%{search.strip()}%"
		filters.append(["area_name", "like", search_term])

	areas = frappe.get_all(
		"Grievance Administrative Area",
		filters=filters,
		fields=[
			"name as area_id",
			"area_name",
			"code",
			"path_code",
			"level_name",
			"parent_administrative_area",
			"is_group",
			"depth",
		],
		order_by="area_name asc",
		limit=limit,
		ignore_permissions=True,
	)

	return success_response(
		data={
			"areas": areas,
			"count": len(areas),
			"parent": parent,
			"level_name": level_name,
		}
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


@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"/<path:area_id_or_path>/ancestors",
	methods=("GET",),
	allow_guest=True,
	summary="Fetch ancestor hierarchy",
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def get_area_ancestors(area_id_or_path: str):
	"""Public endpoint to fetch ancestor breadcrumbs for an administrative area."""
	return success_response(data=get_ancestors(area_id_or_path))


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
