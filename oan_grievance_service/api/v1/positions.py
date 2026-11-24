# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer Position API endpoints.

A position is a role (Crop DA, Woreda Crop SMS). Officers hold it through their rows on
desks, each with an area. The list is read-only here: positions are managed in the desk,
and who holds one is read from GET /api/v1/officers?position=.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_permission,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, blank_to_none

POSITION = "Grievance Officer Position"
ROW = "Grievance RBAC Assignment Officer"

route = prefixed("/api/v1")


class PositionRecord(BaseModel):
	position_id: str = Field(..., description="Position ID (e.g. POS-00200)")
	position_title: str = Field(..., description="Display title")
	designation: str | None = Field(default=None, description="Designation, e.g. Crop DA")
	role_level: str | None = Field(default=None, description="Role level code")
	reports_to_position: str | None = Field(default=None, description="Position this one reports to")
	active: bool = Field(..., description="Whether the position is in use")
	holders: int = Field(..., description="Officers holding this position on an active desk row")


class PositionListData(BaseModel):
	positions: list[PositionRecord]
	total: int
	pagination: dict


class ListPositions(PageParams, Body):
	"""Filters and pagination for listing positions."""

	role_level: str | None = Field(default=None, description="Role level code")
	active: bool | None = Field(default=None, description="Filter by active status")
	search: str | None = Field(default=None, description="Matches title, designation or ID")

	_blank = field_validator("role_level", "active", "search", mode="before")(blank_to_none)


@route("/positions", methods=("GET",), summary="List officer positions", tier="officer-core")
@handle_api_errors
@require_permission(POSITION, "read")
@validate_request(ListPositions)
@api_doc(
	summary="List officer positions",
	description="Positions are roles such as Crop DA or Woreda Crop SMS, each with a role level and "
	+ "the position it reports to. `holders` counts the people holding one. Filterable by role "
	+ "level, active status and a search string.",
	tags=["Administration"],
	response_model=PositionListData,
)
def list_positions(**kwargs):
	params = ListPositions(**kwargs)
	filters = {}
	if params.role_level:
		filters["role_level"] = params.role_level
	if params.active is not None:
		filters["active"] = int(params.active)
	or_filters = None
	if params.search:
		s = f"%{params.search.strip()}%"
		or_filters = [["name", "like", s], ["position_title", "like", s], ["designation", "like", s]]

	total = frappe.get_all(
		POSITION,
		filters=filters,
		or_filters=or_filters,
		fields=[{"COUNT": "*", "as": "total"}],
		order_by=None,
	)[0].total
	rows = frappe.get_all(
		POSITION,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "position_title", "designation", "role_level", "reports_to_position", "active"],
		order_by="position_title asc, name asc",
		start=params.start,
		limit=params.page_size,
	)
	R = frappe.qb.DocType(ROW)
	holders = dict(
		frappe.qb.from_(R)
		.select(R.position, Count(R.user).distinct())
		.where(R.position.isin([r.name for r in rows] or [""]) & (R.active == 1) & R.user.notnull())
		.groupby(R.position)
		.run()
	)
	return success_response(
		data={
			"positions": [
				{
					"position_id": r.name,
					"position_title": r.position_title or r.designation or r.name,
					"designation": r.designation,
					"role_level": r.role_level,
					"reports_to_position": r.reports_to_position,
					"active": bool(r.active),
					"holders": holders.get(r.name, 0),
				}
				for r in rows
			],
			"total": total,
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Positions retrieved successfully"),
	)
