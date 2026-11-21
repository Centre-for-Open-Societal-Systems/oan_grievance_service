# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer Position and Position Assignment API endpoints.

A position is a role (Crop DA, Woreda Crop SMS); an assignment is one person holding it
in an area. Both lists are read-only here: positions and assignments are managed in the
desk and through the officer API.
"""

import frappe
from frappe import _
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
ASSIGNMENT = "Grievance Position Assignment"

route = prefixed("/api/v1")


class PositionRecord(BaseModel):
	position_id: str = Field(..., description="Position ID (e.g. POS-00200)")
	position_title: str = Field(..., description="Display title")
	designation: str | None = Field(default=None, description="Designation, e.g. Crop DA")
	role_level: str | None = Field(default=None, description="Role level code")
	reports_to_position: str | None = Field(default=None, description="Position this one reports to")
	active: bool = Field(..., description="Whether the position is in use")
	holders: int = Field(..., description="Active assignments of this position")


class PositionListData(BaseModel):
	positions: list[PositionRecord]
	total: int
	pagination: dict


class AssignmentRecord(BaseModel):
	assignment_id: str = Field(..., description="Assignment ID (e.g. PA-00001)")
	position_id: str = Field(..., description="Position held")
	position_title: str | None = Field(default=None, description="Title of the position held")
	role_level: str | None = Field(default=None, description="Role level code of the position")
	administrative_area: str | None = Field(default=None, description="Area served; empty is everywhere")
	user: str | None = Field(default=None, description="Holder; empty until their first sign-in")
	email: str | None = Field(default=None, description="Email their first sign-in is matched on")
	active: bool
	on_leave: bool
	max_open_cases: int = Field(..., description="0 means no cap")


class AssignmentListData(BaseModel):
	assignments: list[AssignmentRecord]
	total: int
	pagination: dict


class ListPositions(PageParams, Body):
	"""Filters and pagination for listing positions."""

	role_level: str | None = Field(default=None, description="Role level code")
	active: bool | None = Field(default=None, description="Filter by active status")
	search: str | None = Field(default=None, description="Matches title, designation or ID")

	_blank = field_validator("role_level", "active", "search", mode="before")(blank_to_none)


class ListAssignments(PageParams, Body):
	"""Filters and pagination for listing position assignments."""

	position: str | None = Field(default=None, description="Position ID")
	role_level: str | None = Field(default=None, description="Role level code of the position")
	administrative_area: str | None = Field(
		default=None, description="Area; also matches assignments in the areas under it"
	)
	email: str | None = Field(default=None, description="Exact email, case-insensitive")
	active: bool | None = Field(default=None, description="Filter by active status")
	unclaimed: bool | None = Field(
		default=None, description="True: waiting on a first sign-in. False: held by a user"
	)
	mine: bool | None = Field(default=None, description="True: only the caller's own assignments")

	_blank = field_validator(
		"position", "role_level", "administrative_area", "email", "active", "unclaimed", "mine", mode="before"
	)(blank_to_none)


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
	holders = {
		r.position: r.n
		for r in frappe.get_all(
			ASSIGNMENT,
			filters={"position": ["in", [r.name for r in rows] or [""]], "active": 1},
			fields=["position", {"COUNT": "*", "as": "n"}],
			group_by="position",
			order_by=None,
		)
	}
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


@route("/position-assignments", methods=("GET",), summary="List position assignments", tier="officer-core")
@handle_api_errors
@require_permission(ASSIGNMENT, "read")
@validate_request(ListAssignments)
@api_doc(
	summary="List position assignments",
	description="Who holds which position, and where. An assignment with no user is waiting on the "
	+ "first sign-in of its email. Filterable by position, role level, area (including the areas "
	+ "under it), email, active status, unclaimed, and `mine` for the caller's own.",
	tags=["Administration"],
	response_model=AssignmentListData,
)
def list_position_assignments(**kwargs):
	params = ListAssignments(**kwargs)
	A = frappe.qb.DocType(ASSIGNMENT)
	P = frappe.qb.DocType(POSITION)
	query = frappe.qb.from_(A).join(P).on(P.name == A.position)
	if params.position:
		query = query.where(A.position == params.position)
	if params.role_level:
		query = query.where(P.role_level == params.role_level)
	if params.administrative_area:
		Area = frappe.qb.DocType("Grievance Administrative Area")
		bounds = frappe.db.get_value(
			"Grievance Administrative Area", params.administrative_area, ["lft", "rgt"]
		)
		if not bounds:
			frappe.throw(
				_("Administrative area '{0}' does not exist.").format(params.administrative_area),
				frappe.DoesNotExistError,
			)
		query = query.where(
			A.administrative_area.isin(
				frappe.qb.from_(Area)
				.select(Area.name)
				.where((Area.lft >= bounds[0]) & (Area.rgt <= bounds[1]))
			)
		)
	if params.email:
		query = query.where(A.email == params.email.strip().lower())
	if params.active is not None:
		query = query.where(A.active == int(params.active))
	if params.unclaimed is not None:
		query = query.where(A.user.isnull() if params.unclaimed else A.user.notnull())
	if params.mine:
		query = query.where(A.user == frappe.session.user)

	total = query.select(frappe.qb.functions.Count("*")).run()[0][0]
	rows = (
		query.select(
			A.name,
			A.position,
			P.position_title,
			P.role_level,
			A.administrative_area,
			A.user,
			A.email,
			A.active,
			A.on_leave,
			A.max_open_cases,
		)
		.orderby(A.modified, order=frappe.qb.desc)
		.orderby(A.name, order=frappe.qb.desc)
		.offset(params.start)
		.limit(params.page_size)
		.run(as_dict=True)
	)
	return success_response(
		data={
			"assignments": [
				{
					"assignment_id": r.name,
					"position_id": r.position,
					"position_title": r.position_title,
					"role_level": r.role_level,
					"administrative_area": r.administrative_area,
					"user": r.user,
					"email": r.email,
					"active": bool(r.active),
					"on_leave": bool(r.on_leave),
					"max_open_cases": r.max_open_cases or 0,
				}
				for r in rows
			],
			"total": total,
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Position assignments retrieved successfully"),
	)
