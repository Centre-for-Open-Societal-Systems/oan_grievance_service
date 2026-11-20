# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Role Level API endpoints.

Exposes escalation hierarchy rungs (e.g. Kebele Nodal Officer, Woreda Nodal Officer,
Zonal Head, Regional Bureau Head), their codes, display names, escalation order,
escalation breach hours, and active statuses.
"""

import frappe
from frappe import N_, _
from frappe.model.document import Document
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

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none

DOCTYPE = "Grievance Role Level"

route = prefixed("/api/v1/role-levels")


class RoleLevelRecord(BaseModel):
	level_code: str = Field(..., description="Unique role level identifier/code")
	level_name: str = Field(..., description="Human-readable display name")
	level_order: int = Field(..., description="Escalation order rank (lower is more junior)")
	escalation_hours: int | None = Field(
		default=None,
		description="Hours a breached grievance rests before climbing upward; 0 or null is terminal",
	)
	is_active: bool = Field(..., description="Whether this level is active in the escalation chain")
	description: str | None = Field(
		default=None, description="Description of the role level responsibilities"
	)


class RoleLevelListData(BaseModel):
	role_levels: list[RoleLevelRecord]
	total: int
	pagination: dict


class RoleLevelDetailData(BaseModel):
	role_level: RoleLevelRecord


class ListRoleLevels(PageParams, Body):
	"""Filters and pagination for listing role levels."""

	is_active: bool | None = Field(default=None, description="Filter by active status")
	search: str | None = Field(
		default=None, description="Search term matching level code, name, or description"
	)

	_active_blank = field_validator("is_active", mode="before")(blank_to_none)


class RoleLevelRef(Body):
	level_code: NonBlank = Field(..., description="Role level code", title=N_("Level Code"))


def _to_record(doc: dict | Document) -> dict:
	if isinstance(doc, Document):
		d = doc.as_dict()
	else:
		d = dict(doc)
	return {
		"level_code": d.get("level_code") or d.get("name"),
		"level_name": d.get("level_name") or "",
		"level_order": int(d.get("level_order") or 0),
		"escalation_hours": d.get("escalation_hours"),
		"is_active": bool(d.get("is_active")),
		"description": d.get("description") or None,
	}


def _count(doctype: str, filters: dict, or_filters: list | None) -> int:
	rows = frappe.get_all(
		doctype,
		filters=filters,
		or_filters=or_filters,
		fields=[{"COUNT": "*", "as": "total"}],
		order_by=None,
	)
	return int(rows[0].total) if rows else 0


@route("", methods=("GET",), summary="List grievance role levels", tier="officer-core")
@handle_api_errors
@require_permission(DOCTYPE, "read")
@validate_request(ListRoleLevels)
@api_doc(
	summary="List grievance role levels",
	description="Lists escalation hierarchy role levels, ordered by rank (level_order asc), "
	+ "including their codes, names, escalation hours, and active statuses. Filterable by active "
	+ "status and search string.",
	tags=["Administration"],
	response_model=RoleLevelListData,
)
def list_role_levels(
	is_active: bool | str | None = None,
	search: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List role levels in escalation hierarchy order."""
	params = PageParams(page=page, page_size=page_size)
	filters: dict = {}
	if is_active is not None:
		if isinstance(is_active, str):
			filters["is_active"] = 1 if is_active.lower() in ("true", "1", "yes") else 0
		else:
			filters["is_active"] = 1 if is_active else 0

	or_filters = None
	if search and str(search).strip():
		s = f"%{str(search).strip()}%"
		or_filters = [
			["level_code", "like", s],
			["level_name", "like", s],
			["description", "like", s],
		]

	total = _count(DOCTYPE, filters, or_filters)
	docs = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"level_code",
			"level_name",
			"level_order",
			"escalation_hours",
			"is_active",
			"description",
		],
		order_by="level_order asc, name asc",
		start=params.start,
		limit=params.page_size,
	)

	records = [_to_record(d) for d in docs]
	return success_response(
		data={
			"role_levels": records,
			"total": total,
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Role levels retrieved successfully"),
	)


@route("/<level_code>", methods=("GET",), summary="Get a grievance role level", tier="officer-core")
@handle_api_errors
@require_permission(DOCTYPE, "read")
@validate_request(RoleLevelRef)
@api_doc(
	summary="Get a grievance role level",
	description="Fetch a single grievance role level by its code, including its display name, "
	+ "escalation rank, breach hours, active status, and description.",
	tags=["Administration"],
	response_model=RoleLevelDetailData,
)
def get_role_level(level_code: str, **kwargs):
	"""Fetch a single role level by its code."""
	if not frappe.db.exists(DOCTYPE, level_code):
		frappe.throw(_("Role level '{0}' does not exist.").format(level_code), frappe.DoesNotExistError)

	doc = frappe.get_doc(DOCTYPE, level_code)
	return success_response(
		data={"role_level": _to_record(doc)},
		message=_("Role level retrieved successfully"),
	)
