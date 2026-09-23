# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""STG-330 Dashboard Statistics API (FR-09 / FR-11.2).

Returns aggregated grievance counts for KPI cards and charts from the reporting
projection, scoped to the caller's RBAC region/department/category.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, require_role, success_response

from oan_grievance_service.services import dashboard_stats

route = prefixed("/api/v1/dashboard-statistics")

# Officer desk and admin dashboards - not submitter-facing.
DASHBOARD_ROLES = [
	"Grievance Officer",
	"Grievance Admin",
	"System Manager",
	"Administrator",
]

# Immediate projection rebuild — admin / system only.
REFRESH_ROLES = [
	"Grievance Admin",
	"System Manager",
	"Administrator",
]


@route("", methods=("GET",), summary="Dashboard statistics (KPIs and chart series)")
@frappe.whitelist()
@handle_api_errors
@require_role(DASHBOARD_ROLES)
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
	try:
		months_n = int(months)
	except (TypeError, ValueError):
		frappe.throw(_("months must be an integer between 1 and 36."), exc=frappe.ValidationError)

	if months_n < 1 or months_n > 36:
		frappe.throw(_("months must be an integer between 1 and 36."), exc=frappe.ValidationError)

	data = dashboard_stats.get_statistics(user=frappe.session.user, months=months_n)
	return success_response(
		data=data,
		message=_("Dashboard statistics fetched successfully"),
	)


@route("/refresh", methods=("POST",), summary="Rebuild dashboard reporting projection now")
@frappe.whitelist()
@handle_api_errors
@require_role(REFRESH_ROLES)
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
