# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""FR-09 / STG-330 dashboard statistics: refresh the reporting projection and read it.

The Dashboard Statistics API never scans ``tabGrievance`` on a request path. A
scheduled job (and admin refresh) call ``refresh_projection`` to rebuild
``Grievance Dashboard Projection``; ``get_statistics`` aggregates those rows
after applying the caller's RBAC scope (role / region / department / category).

Status labels mirror the Grievance Workflow DocType (source of truth after #19).
They are local to this module for KPI bucketing / draft exclusion only — not a
second transition table.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import frappe
from frappe.utils import add_months, get_datetime, now_datetime

from oan_grievance_service import permissions as perms

PROJECTION_DOCTYPE = "Grievance Dashboard Projection"
METRIC_STOCK = "stock"
METRIC_MONTHLY = "monthly"

DEFAULT_MONTHS = 12

STATUS_DRAFT = "Draft"
STATUS_SUBMITTED = "Submitted"
STATUS_ASSIGNED = "Assigned"
STATUS_IN_PROGRESS = "In Progress"
STATUS_MORE_INFO_NEEDED = "More Info Needed"
STATUS_PENDING_SUBMITTER = "Pending Submitter"
STATUS_RESOLVED = "Resolved"
STATUS_CLOSED = "Closed"
STATUS_REJECTED = "Rejected"

OPEN_STATUSES = (
	STATUS_SUBMITTED,
	STATUS_ASSIGNED,
	STATUS_IN_PROGRESS,
	STATUS_MORE_INFO_NEEDED,
	STATUS_PENDING_SUBMITTER,
)

# FSD 3.11.3 display groups mapped to KPI card keys.
KPI_STATUS_GROUPS = {
	"pending": (STATUS_SUBMITTED, STATUS_ASSIGNED),
	"in_progress": (STATUS_IN_PROGRESS, STATUS_MORE_INFO_NEEDED),
	"under_review": (STATUS_PENDING_SUBMITTER,),
	"resolved": (STATUS_RESOLVED, STATUS_CLOSED),
	"rejected": (STATUS_REJECTED,),
}


def refresh_projection() -> dict:
	"""Rebuild the reporting projection from current Grievance rows.

	Runs as a scheduled job so dashboard loads stay O(projection) rather than
	O(grievances). Returns counts of rows written for observability/tests.
	"""
	snapshot_at = now_datetime()
	# Table name is a module constant, not user input; hardcoded to satisfy
	# frappe-sql-format-injection (no f-string / .format in db.sql calls).
	frappe.db.sql("DELETE FROM `tabGrievance Dashboard Projection`")

	stock_rows = _build_stock_rows(snapshot_at)
	monthly_rows = _build_monthly_rows(snapshot_at)
	_bulk_insert(stock_rows + monthly_rows)

	return {
		"snapshot_at": snapshot_at,
		"stock_rows": len(stock_rows),
		"monthly_rows": len(monthly_rows),
	}


def get_statistics(user: str | None = None, months: int = DEFAULT_MONTHS) -> dict:
	"""Aggregate projection rows for the caller's scope into the dashboard payload.

	Shape matches KpiCards, StatusDistributionChart, ServiceCategoryChart and
	MonthlyTrendChart consumers: ``kpis``, ``by_status``, ``by_category``,
	``sla_breach``, ``monthly_trend``, plus ``scope`` and projection ``meta``.
	"""
	user = user or frappe.session.user
	months = max(1, min(36, int(months or DEFAULT_MONTHS)))
	scope = resolve_caller_scope(user)

	stock = _fetch_scoped_rows(METRIC_STOCK, scope)
	monthly = _fetch_scoped_rows(METRIC_MONTHLY, scope, months=months)

	by_status = _sum_by(stock, key="status", count_field="case_count")
	by_category = _sum_by(stock, key="service_category", count_field="case_count")

	total = sum(r.case_count or 0 for r in stock)
	sla_breached = sum(r.sla_breached_count or 0 for r in stock)
	escalated = sum(r.escalated_count or 0 for r in stock)
	within_sla = max(0, total - sla_breached)

	status_counts = {row["status"]: row["count"] for row in by_status if row["status"]}
	kpis = {
		"total": total,
		"open": _sum_statuses(status_counts, OPEN_STATUSES),
		"pending": _sum_statuses(status_counts, KPI_STATUS_GROUPS["pending"]),
		"in_progress": _sum_statuses(status_counts, KPI_STATUS_GROUPS["in_progress"]),
		"under_review": _sum_statuses(status_counts, KPI_STATUS_GROUPS["under_review"]),
		"resolved": _sum_statuses(status_counts, KPI_STATUS_GROUPS["resolved"]),
		"rejected": _sum_statuses(status_counts, KPI_STATUS_GROUPS["rejected"]),
		"sla_breached": sla_breached,
		"escalated": escalated,
	}

	snapshot_at = None
	if stock or monthly:
		candidates = [r.snapshot_at for r in (stock + monthly) if r.snapshot_at]
		if candidates:
			snapshot_at = max(get_datetime(c) for c in candidates)

	return {
		"kpis": kpis,
		"by_status": by_status,
		"by_category": [
			{"category": row["service_category"] or "Uncategorised", "count": row["count"]}
			for row in by_category
		],
		"sla_breach": {
			"breached": sla_breached,
			"within_sla": within_sla,
			"open_breached": sla_breached,
		},
		"monthly_trend": _monthly_trend(monthly, months=months),
		"scope": {
			"unrestricted": scope["unrestricted"],
			"administrative_areas": scope["administrative_areas"],
			"departments": scope["departments"],
			"categories": scope["categories"],
		},
		"meta": {
			"source": "projection",
			"snapshot_at": snapshot_at.isoformat(sep=" ") if snapshot_at else None,
			"months": months,
		},
	}


def resolve_caller_scope(user: str | None = None) -> dict:
	"""Mirror ``permissions.grievance_query_conditions`` as structured filters."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))

	if roles & perms.UNRESTRICTED_ROLES:
		return {
			"unrestricted": True,
			"administrative_areas": [],
			"area_bounds": [],
			"departments": [],
			"categories": [],
			"scope_clauses": [],
		}

	scopes = perms.active_scopes(user)
	bounds = perms.area_bounds(scopes)
	area_names = []
	departments = set()
	categories = set()
	scope_clauses = []

	for scope in scopes:
		clause = {}
		if scope.administrative_area_scope:
			area_names.append(scope.administrative_area_scope)
			lft_rgt = bounds.get(scope.administrative_area_scope)
			if lft_rgt:
				clause["area_bounds"] = lft_rgt
		if scope.department_scope:
			departments.add(scope.department_scope)
			clause["department"] = scope.department_scope
		if scope.category_scope:
			categories.add(scope.category_scope)
			clause["category"] = scope.category_scope
		if clause:
			scope_clauses.append(clause)

	return {
		"unrestricted": False,
		"administrative_areas": sorted(set(area_names)),
		"area_bounds": [bounds[a] for a in area_names if a in bounds],
		"departments": sorted(departments),
		"categories": sorted(categories),
		"scope_clauses": scope_clauses,
	}


def _build_stock_rows(snapshot_at: datetime) -> list[dict]:
	"""Current-status buckets for KPI / status / category / SLA cards."""
	now = snapshot_at
	rows = frappe.db.sql(
		"""
		SELECT
			g.administrative_area AS administrative_area,
			g.area_lft AS area_lft,
			a.rgt AS area_rgt,
			g.assigned_dept AS assigned_dept,
			g.service_category AS service_category,
			g.status AS status,
			COUNT(*) AS case_count,
			SUM(
				CASE
					WHEN g.status IN %(open_statuses)s
						AND g.sla_due_date IS NOT NULL
						AND g.sla_due_date < %(now)s
						AND g.on_hold_since IS NULL
					THEN 1 ELSE 0
				END
			) AS sla_breached_count,
			SUM(CASE WHEN IFNULL(g.escalated, 0) = 1 THEN 1 ELSE 0 END) AS escalated_count
		FROM `tabGrievance` g
		LEFT JOIN `tabGrievance Administrative Area` a
			ON a.name = g.administrative_area
		WHERE IFNULL(g.status, '') != %(draft)s
		GROUP BY
			g.administrative_area,
			g.area_lft,
			a.rgt,
			g.assigned_dept,
			g.service_category,
			g.status
		""",
		{"now": now, "draft": STATUS_DRAFT, "open_statuses": tuple(OPEN_STATUSES)},
		as_dict=True,
	)

	return [
		{
			"metric_type": METRIC_STOCK,
			"period_month": None,
			"snapshot_at": snapshot_at,
			"administrative_area": r.administrative_area,
			"area_lft": r.area_lft,
			"area_rgt": r.area_rgt,
			"assigned_dept": r.assigned_dept,
			"service_category": r.service_category,
			"status": r.status,
			"case_count": int(r.case_count or 0),
			"sla_breached_count": int(r.sla_breached_count or 0),
			"escalated_count": int(r.escalated_count or 0),
			"resolved_count": 0,
		}
		for r in rows
	]


def _build_monthly_rows(snapshot_at: datetime) -> list[dict]:
	"""Submission and resolution flows by calendar month for MonthlyTrendChart."""
	# Month expressions are fixed SQL fragments (not user input). Built via
	# placeholder replace so frappe.db.sql is never called with an f-string.
	month_expr = _sql_month_expr("g.creation")
	submitted_query = """
		SELECT
			__MONTH_EXPR__ AS period_month,
			g.administrative_area AS administrative_area,
			g.area_lft AS area_lft,
			a.rgt AS area_rgt,
			g.assigned_dept AS assigned_dept,
			g.service_category AS service_category,
			COUNT(*) AS case_count
		FROM `tabGrievance` g
		LEFT JOIN `tabGrievance Administrative Area` a
			ON a.name = g.administrative_area
		WHERE IFNULL(g.status, '') != %(draft)s
		GROUP BY
			__MONTH_EXPR__,
			g.administrative_area,
			g.area_lft,
			a.rgt,
			g.assigned_dept,
			g.service_category
		""".replace("__MONTH_EXPR__", month_expr)
	submitted = frappe.db.sql(
		submitted_query,
		{"draft": STATUS_DRAFT},
		as_dict=True,
	)

	resolved_month_expr = _sql_month_expr("h.timestamp")
	resolved_query = """
		SELECT
			__MONTH_EXPR__ AS period_month,
			g.administrative_area AS administrative_area,
			g.area_lft AS area_lft,
			a.rgt AS area_rgt,
			g.assigned_dept AS assigned_dept,
			g.service_category AS service_category,
			COUNT(DISTINCT g.name) AS resolved_count
		FROM `tabGrievance Status History` h
		INNER JOIN `tabGrievance` g ON g.name = h.grievance
		LEFT JOIN `tabGrievance Administrative Area` a
			ON a.name = g.administrative_area
		WHERE h.to_status IN %(resolved_statuses)s
		  AND IFNULL(g.status, '') != %(draft)s
		GROUP BY
			__MONTH_EXPR__,
			g.administrative_area,
			g.area_lft,
			a.rgt,
			g.assigned_dept,
			g.service_category
		""".replace("__MONTH_EXPR__", resolved_month_expr)
	resolved = frappe.db.sql(
		resolved_query,
		{
			"draft": STATUS_DRAFT,
			"resolved_statuses": (STATUS_RESOLVED, STATUS_CLOSED),
		},
		as_dict=True,
	)

	# Merge submitted + resolved on the same dimensional key.
	merged: dict[tuple, dict] = {}

	def key_of(r) -> tuple:
		return (
			r.period_month,
			r.administrative_area,
			r.area_lft,
			r.area_rgt,
			r.assigned_dept,
			r.service_category,
		)

	for r in submitted:
		k = key_of(r)
		merged[k] = {
			"metric_type": METRIC_MONTHLY,
			"period_month": r.period_month,
			"snapshot_at": snapshot_at,
			"administrative_area": r.administrative_area,
			"area_lft": r.area_lft,
			"area_rgt": r.area_rgt,
			"assigned_dept": r.assigned_dept,
			"service_category": r.service_category,
			"status": None,
			"case_count": int(r.case_count or 0),
			"sla_breached_count": 0,
			"escalated_count": 0,
			"resolved_count": 0,
		}

	for r in resolved:
		k = key_of(r)
		if k not in merged:
			merged[k] = {
				"metric_type": METRIC_MONTHLY,
				"period_month": r.period_month,
				"snapshot_at": snapshot_at,
				"administrative_area": r.administrative_area,
				"area_lft": r.area_lft,
				"area_rgt": r.area_rgt,
				"assigned_dept": r.assigned_dept,
				"service_category": r.service_category,
				"status": None,
				"case_count": 0,
				"sla_breached_count": 0,
				"escalated_count": 0,
				"resolved_count": 0,
			}
		merged[k]["resolved_count"] = int(r.resolved_count or 0)

	return list(merged.values())


def _sql_month_expr(column: str) -> str:
	"""Calendar month expression portable across MariaDB and Postgres."""
	if getattr(frappe.db, "db_type", None) == "postgres":
		return f"to_char({column}, 'YYYY-MM')"
	return f"DATE_FORMAT({column}, '%%Y-%%m')"


def _bulk_insert(rows: list[dict]) -> None:
	if not rows:
		return

	fields = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"metric_type",
		"period_month",
		"snapshot_at",
		"administrative_area",
		"area_lft",
		"area_rgt",
		"assigned_dept",
		"service_category",
		"status",
		"case_count",
		"sla_breached_count",
		"escalated_count",
		"resolved_count",
	]
	now = now_datetime()
	user = frappe.session.user or "Administrator"
	values = []
	for row in rows:
		values.append(
			[
				frappe.generate_hash(length=10),
				now,
				now,
				user,
				user,
				0,
				0,
				row["metric_type"],
				row.get("period_month"),
				row["snapshot_at"],
				row.get("administrative_area"),
				row.get("area_lft"),
				row.get("area_rgt"),
				row.get("assigned_dept"),
				row.get("service_category"),
				row.get("status"),
				row.get("case_count") or 0,
				row.get("sla_breached_count") or 0,
				row.get("escalated_count") or 0,
				row.get("resolved_count") or 0,
			]
		)

	frappe.db.bulk_insert(PROJECTION_DOCTYPE, fields=fields, values=values)


def _fetch_scoped_rows(metric_type: str, scope: dict, months: int | None = None) -> list:
	"""Load projection rows with RBAC pushed into SQL (area / dept / category)."""
	params: dict = {"metric_type": metric_type}
	conditions = ["metric_type = %(metric_type)s"]

	if months and metric_type == METRIC_MONTHLY:
		cutoff = add_months(now_datetime().replace(day=1), -(months - 1)).strftime("%Y-%m")
		conditions.append("period_month >= %(cutoff)s")
		params["cutoff"] = cutoff

	if not scope["unrestricted"]:
		scope_sql, scope_params = _scope_sql_filter(scope)
		if scope_sql is None:
			# Officer with no assignments: deny-by-default.
			return []
		conditions.append(scope_sql)
		params.update(scope_params)

	where_sql = " AND ".join(conditions)
	# WHERE fragments are built only from trusted placeholders + fixed column names.
	query = (
		"SELECT metric_type, period_month, snapshot_at, administrative_area, "
		"area_lft, area_rgt, assigned_dept, service_category, status, "
		"case_count, sla_breached_count, escalated_count, resolved_count "
		"FROM `tabGrievance Dashboard Projection` WHERE "
	) + where_sql

	return frappe.db.sql(query, params, as_dict=True)


def _scope_sql_filter(scope: dict) -> tuple[str | None, dict]:
	"""Return ``(sql_fragment, params)`` for OR-of-AND RBAC scope clauses.

	Each assignment may constrain area subtree (``area_lft`` between lft/rgt),
	department, and/or category. Matching any one assignment admits the row.
	"""
	clauses = scope.get("scope_clauses") or []
	if not clauses:
		return None, {}

	or_parts = []
	params: dict = {}
	for i, clause in enumerate(clauses):
		ands = []
		if "department" in clause:
			key = f"dept_{i}"
			ands.append(f"assigned_dept = %({key})s")
			params[key] = clause["department"]
		if "category" in clause:
			key = f"cat_{i}"
			ands.append(f"service_category = %({key})s")
			params[key] = clause["category"]
		if "area_bounds" in clause:
			lft, rgt = clause["area_bounds"]
			lft_key, rgt_key = f"lft_{i}", f"rgt_{i}"
			ands.append(f"(area_lft IS NOT NULL AND area_lft BETWEEN %({lft_key})s AND %({rgt_key})s)")
			params[lft_key] = int(lft)
			params[rgt_key] = int(rgt)
		if ands:
			or_parts.append("(" + " AND ".join(ands) + ")")

	if not or_parts:
		return None, {}
	return "(" + " OR ".join(or_parts) + ")", params


def _sum_by(rows, key: str, count_field: str) -> list[dict]:
	totals: dict[str, int] = defaultdict(int)
	for row in rows:
		label = getattr(row, key, None) or ""
		totals[label] += int(getattr(row, count_field, 0) or 0)
	ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0] or ""))
	return [{key: label, "count": count} for label, count in ordered]


def _sum_statuses(status_counts: dict[str, int], statuses) -> int:
	if not statuses:
		return 0
	return sum(status_counts.get(s, 0) for s in statuses)


def _monthly_trend(rows, months: int) -> list[dict]:
	by_month: dict[str, dict] = defaultdict(lambda: {"submitted": 0, "resolved": 0})
	for row in rows:
		month = row.period_month
		if not month:
			continue
		by_month[month]["submitted"] += int(row.case_count or 0)
		by_month[month]["resolved"] += int(row.resolved_count or 0)

	# Ensure a contiguous series for the chart even when some months are empty.
	start = add_months(now_datetime().replace(day=1), -(months - 1))
	series = []
	for i in range(months):
		month = add_months(start, i).strftime("%Y-%m")
		bucket = by_month.get(month, {"submitted": 0, "resolved": 0})
		series.append(
			{
				"month": month,
				"label": datetime.strptime(month, "%Y-%m").strftime("%b %Y"),
				"submitted": bucket["submitted"],
				"resolved": bucket["resolved"],
			}
		)
	return series
