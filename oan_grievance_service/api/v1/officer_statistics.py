# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer statistics for the Nodal Officers (L1) and Senior Officers (L2) admin tabs.

Read-only and separate from officer profile CRUD. Every figure is computed from live
Grievance data on each call (see services/officer_statistics.py), never stored.
"""

from typing import Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, field_validator

from oan_grievance_service.api.v1._schemas import Body, blank_to_none
from oan_grievance_service.services import officer_statistics as service
from oan_grievance_service.services.resolvers import resolve_department

route = prefixed("/api/v1/officers")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]


class OfficerStatisticsRecord(BaseModel):
	user: str
	full_name: str | None = None
	level: Literal["L1", "L2"]
	role_level: str
	assigned: int
	resolved: int
	avg_resolution_hours: float | None = None
	resolution_rate: float


class OfficerStatisticsListData(BaseModel):
	officers: list[OfficerStatisticsRecord]
	pagination: dict


class ListOfficerStatistics(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	level: Literal["L1", "L2"] | None = None
	department: str | None = None

	_blank = field_validator("level", "department", mode="before")(blank_to_none)


@route("/statistics", methods=("GET",), summary="List officer statistics")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListOfficerStatistics)
@api_doc(
	summary="List officer statistics",
	description="Per-officer grievances assigned, grievances resolved, average resolution time "
	+ "in hours and resolution rate (percent), for L1 and L2 officers staffing an active RBAC desk. "
	+ "Computed live from Grievance on every call. Assigned counts grievances past Draft currently "
	+ "assigned to the officer, scoped to the department when one is given. Resolved counts those at Resolved or Closed. Average resolution time "
	+ "runs from creation to resolution and is null until the officer has resolved a case.",
	tags=["Administration"],
	response_model=OfficerStatisticsListData,
)
def list_officer_statistics(
	level: str | None = None,
	department: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""Page of officer statistics, filterable by level (L1/L2) and department."""
	params = PageParams(page=page, page_size=page_size)
	department = resolve_department(department) if department else None
	officers, total = service.list_officers(
		level=level, department=department, start=params.start, limit=params.page_size
	)
	return success_response(
		data={
			"officers": service.build_records(officers, department),
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Officer statistics retrieved"),
	)
