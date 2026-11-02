"""Administrative Area cascading lookup and search endpoints for mobile apps, web wizards and public registration."""

import frappe
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	handle_api_errors,
	parse_multi_value,
	success_response,
)

from oan_grievance_service.services.administrative_area import get_ancestors

route = prefixed("/api/v1/administrative-areas")


@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"", methods=("GET",), allow_guest=True, summary="Fetch administrative areas", tier="public-reference"
)
@handle_api_errors
def get_areas(
	parent: str | list[str] | None = None,
	level_name: str | None = None,
	search: str | None = None,
	ancestors_of: str | None = None,
	limit: int = 100,
	**kwargs,
):
	"""Public endpoint to fetch administrative areas for cascading dropdowns and searches.

	Query Modes:
	    1. Cascading Drill-Down:
	       - No parent provided: returns all top-level Regions (level_name='Region').
	       - parent provided (e.g. 'region-ET14', ['region-ET14', 'region-ET07'], or 'region-ET14,region-ET07'): returns immediate child nodes or tier descendants.
	    2. Level Filter:
	       - level_name provided (e.g. 'Region', 'Zone', 'Woreda', 'Kebele').
	    3. Free-Text Search:
	       - search provided: searches across area_name, code, or path_code.
	    4. Ancestor Breadcrumbs:
	       - ancestors_of provided (area ID or path_code): returns the chain from Country down to the node.

	Args:
	    parent (str | list[str], optional): Name, path_code, or comma-separated list of parent area(s).
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

	parents = parse_multi_value(parent or kwargs.get("parents"))
	if level_name:
		level_name = str(level_name).strip()

	from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
		search_areas,
	)

	areas = search_areas(
		parents=parents,
		level_name=level_name,
		search=search,
		limit=limit,
	)

	return success_response(
		data={
			"areas": areas,
			"count": len(areas),
			"parent": parent
			if parent is not None
			else (parents if len(parents) > 1 else (parents[0] if parents else None)),
			"level_name": level_name,
		}
	)


@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"/<path:area_id_or_path>/ancestors",
	methods=("GET",),
	allow_guest=True,
	summary="Fetch ancestor hierarchy",
	tier="public-reference",
)
@handle_api_errors
def get_area_ancestors(area_id_or_path: str):
	"""Public endpoint to fetch ancestor breadcrumbs for an administrative area."""
	return success_response(data=get_ancestors(area_id_or_path))
