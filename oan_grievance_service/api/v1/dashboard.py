# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""STG-330 Dashboard Statistics API (FR-09 / FR-11.2).

Returns aggregated grievance counts for KPI cards and charts from the reporting
projection, scoped to the caller's RBAC region/department/category.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1._schemas import Body
from oan_grievance_service.services import dashboard_stats
from oan_grievance_service.services.constants import ADMIN_ROLES, ROLE_OFFICER

route = prefixed("/api/v1/dashboard-statistics")

# Officer desk and admin dashboards - not submitter-facing.
DASHBOARD_ROLES = [ROLE_OFFICER, *ADMIN_ROLES]

# Immediate projection rebuild - admin / system only.
REFRESH_ROLES = ADMIN_ROLES


class DashboardKpis(BaseModel):
	total: int
	open: int
	pending: int
	in_progress: int
	under_review: int
	resolved: int
	rejected: int
	sla_breached: int
	escalated: int


class StatusCount(BaseModel):
	status: str
	count: int


class CategoryCount(BaseModel):
	category: str
	count: int


class SlaBreach(BaseModel):
	breached: int
	within_sla: int
	open_breached: int


class MonthlyPoint(BaseModel):
	month: str
	label: str
	submitted: int
	resolved: int


class DashboardStatisticsData(BaseModel):
	kpis: DashboardKpis
	by_status: list[StatusCount]
	by_category: list[CategoryCount]
	sla_breach: SlaBreach
	monthly_trend: list[MonthlyPoint]
	scope: dict
	meta: dict


class RefreshProjectionData(BaseModel):
	snapshot_at: str | None = None
	stock_rows: int
	monthly_rows: int


class DashboardStatisticsQuery(Body):
	"""Unknown query parameters are rejected, so a mistyped one cannot be silently ignored."""

	months: int = Field(12, ge=1, le=36, description="Length of monthly_trend, in calendar months")


@route("", methods=("GET",), summary="Dashboard statistics (KPIs and chart series)")
@frappe.whitelist()
@handle_api_errors
@require_role(DASHBOARD_ROLES)
@validate_request(DashboardStatisticsQuery)
@api_doc(
	summary="Dashboard statistics (KPIs and chart series)",
	description="KPI cards and chart series for the officer and admin dashboards, read from the "
	+ "Grievance Dashboard Projection that the scheduler rebuilds hourly, never from Grievance on "
	+ "the request. Officers see only the cases inside their active RBAC desks; Grievance Admin, "
	+ "System Manager and Administrator see every case. months sets the length of monthly_trend "
	+ "(1-36, default 12).",
	tags=["Dashboard"],
	response_model=DashboardStatisticsData,
)
def get_statistics(months: int | str = 12, **kwargs):
	"""Aggregate dashboard metrics from the FR-09 reporting projection.

	REST:
	    GET /api/v1/dashboard-statistics?months=12

	RPC:
	    GET /api/method/oan_grievance_service.api.v1.dashboard.get_statistics

	Query:
	    months - number of calendar months for ``monthly_trend`` (1-36, default 12).

	Response ``data`` keys feed:
	    - KpiCards.tsx -> ``kpis``
	    - StatusDistributionChart.tsx -> ``by_status``
	    - ServiceCategoryChart.tsx -> ``by_category``
	    - MonthlyTrendChart.tsx -> ``monthly_trend``
	    - SLA KPI / breach chart -> ``sla_breach``
	"""
	data = dashboard_stats.get_statistics(user=frappe.session.user, months=int(months))
	return success_response(
		data=data,
		message=_("Dashboard statistics fetched successfully"),
	)


@route("/refresh", methods=("POST",), summary="Rebuild dashboard reporting projection now")
@frappe.whitelist()
@handle_api_errors
@require_role(REFRESH_ROLES)
@api_doc(
	summary="Rebuild dashboard reporting projection now",
	description="Rebuilds the Grievance Dashboard Projection immediately instead of waiting for "
	+ "the hourly job. Grievance Admin, System Manager and Administrator only.",
	tags=["Dashboard"],
	response_model=RefreshProjectionData,
)
def refresh_projection(**kwargs):
	"""Admin-triggered rebuild of ``Grievance Dashboard Projection``.

	REST:
	    POST /api/v1/dashboard-statistics/refresh

	RPC:
	    POST /api/method/oan_grievance_service.api.v1.dashboard.refresh_projection

	The same rebuild also runs hourly via ``tasks.refresh_dashboard_projection``.
	"""
	result = dashboard_stats.refresh_projection()
	snapshot_at = result.get("snapshot_at")
	return success_response(
		data={
			"snapshot_at": snapshot_at.isoformat(sep=" ") if snapshot_at else None,
			"stock_rows": result.get("stock_rows", 0),
			"monthly_rows": result.get("monthly_rows", 0),
		},
		message=_("Dashboard projection refreshed successfully"),
	)
