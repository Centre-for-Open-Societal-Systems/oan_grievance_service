# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer Position API endpoints.

A position is a role (Crop DA, Woreda Crop SMS). Officers hold it through their rows on
desks, each with an area. Both lists are read-only here: positions and desk rows are
managed in the desk and through the officer API.
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
DESK = "Grievance RBAC Assignment"

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


class HolderRecord(BaseModel):
	row_id: str = Field(..., description="Desk row ID")
	desk_id: str = Field(..., description="Desk (Grievance RBAC Assignment) the row is on")
	category: str | None = Field(default=None, description="Service category of the desk")
	department: str | None = Field(default=None, description="Department of the desk")
	position_id: str | None = Field(default=None, description="Position held")
	position_title: str | None = Field(default=None, description="Title of the position held")
	role_level: str | None = Field(default=None, description="Role level code of the row")
	administrative_area: str | None = Field(default=None, description="Area served; empty is everywhere")
	user: str | None = Field(default=None, description="Holder; empty until their first sign-in")
	email: str | None = Field(default=None, description="Email their first sign-in is matched on")
	active: bool
	on_leave: bool


class HolderListData(BaseModel):
	holders: list[HolderRecord]
	total: int
	pagination: dict


class ListPositions(PageParams, Body):
	"""Filters and pagination for listing positions."""

	role_level: str | None = Field(default=None, description="Role level code")
	active: bool | None = Field(default=None, description="Filter by active status")
	search: str | None = Field(default=None, description="Matches title, designation or ID")

	_blank = field_validator("role_level", "active", "search", mode="before")(blank_to_none)


class ListHolders(PageParams, Body):
	"""Filters and pagination for listing position holders."""

	position: str | None = Field(default=None, description="Position ID")
	role_level: str | None = Field(default=None, description="Role level code")
	administrative_area: str | None = Field(
		default=None, description="Area; also matches rows in the areas under it"
	)
	department: str | None = Field(default=None, description="Department of the desk")
	category: str | None = Field(default=None, description="Service category of the desk")
	email: str | None = Field(default=None, description="Exact email, case-insensitive")
	active: bool | None = Field(default=None, description="Filter by active status")
	unclaimed: bool | None = Field(
		default=None, description="True: waiting on a first sign-in. False: held by a user"
	)
	mine: bool | None = Field(default=None, description="True: only the caller's own rows")

	_blank = field_validator(
		"position",
		"role_level",
		"administrative_area",
		"department",
		"category",
		"email",
		"active",
		"unclaimed",
		"mine",
		mode="before",
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


@route("/position-holders", methods=("GET",), summary="List position holders", tier="officer-core")
@handle_api_errors
@require_permission(DESK, "read")
@validate_request(ListHolders)
@api_doc(
	summary="List position holders",
	description="Who holds which position, on which desk and where. A row with no user is waiting on "
	+ "the first sign-in of its email. Filterable by position, role level, area (including the areas "
	+ "under it), department, category, email, active status, unclaimed, and `mine` for the caller's own.",
	tags=["Administration"],
	response_model=HolderListData,
)
def list_position_holders(**kwargs):
	params = ListHolders(**kwargs)
	R = frappe.qb.DocType(ROW)
	D = frappe.qb.DocType(DESK)
	P = frappe.qb.DocType(POSITION)
	query = frappe.qb.from_(R).join(D).on(D.name == R.parent).left_join(P).on(P.name == R.position)
	if params.position:
		query = query.where(R.position == params.position)
	if params.role_level:
		query = query.where(R.role_level == params.role_level)
	if params.department:
		query = query.where(D.department_scope == params.department)
	if params.category:
		query = query.where(D.category_scope == params.category)
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
			R.administrative_area.isin(
				frappe.qb.from_(Area)
				.select(Area.name)
				.where((Area.lft >= bounds[0]) & (Area.rgt <= bounds[1]))
			)
		)
	if params.email:
		query = query.where(R.email == params.email.strip().lower())
	if params.active is not None:
		query = query.where(R.active == int(params.active))
	if params.unclaimed is not None:
		unclaimed = R.user.isnull() | (R.user == "")
		query = query.where(unclaimed if params.unclaimed else ~unclaimed)
	if params.mine:
		query = query.where(R.user == frappe.session.user)

	total = query.select(Count("*")).run()[0][0]
	rows = (
		query.select(
			R.name,
			R.parent,
			D.category_scope,
			D.department_scope,
			R.position,
			P.position_title,
			R.role_level,
			R.administrative_area,
			R.user,
			R.email,
			R.active,
			R.on_leave,
		)
		.orderby(R.parent, order=frappe.qb.asc)
		.orderby(R.name, order=frappe.qb.asc)
		.offset(params.start)
		.limit(params.page_size)
		.run(as_dict=True)
	)
	return success_response(
		data={
			"holders": [
				{
					"row_id": r.name,
					"desk_id": r.parent,
					"category": r.category_scope,
					"department": r.department_scope,
					"position_id": r.position,
					"position_title": r.position_title,
					"role_level": r.role_level,
					"administrative_area": r.administrative_area,
					"user": r.user,
					"email": r.email,
					"active": bool(r.active),
					"on_leave": bool(r.on_leave),
				}
				for r in rows
			],
			"total": total,
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Position holders retrieved successfully"),
	)
