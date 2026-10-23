# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Dashboard analytics: the rollups, the charts built on them, and the two endpoints.

The site may already hold grievances, so every count is asserted as a change
against a baseline taken before the fixtures are made.
"""

import frappe
import pymysql
from frappe.query_builder.functions import Sum
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_months, add_to_date, get_datetime, getdate, now_datetime

from oan_grievance_service.api.v1 import charts as charts_api
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard, dashboard_rollup, hooks_handlers, sla
from oan_grievance_service.tests.fixtures import (
	a_department,
	a_grievance,
	a_leaf_area,
	a_service_category,
	discard_grievance,
)

# Keys that name a case or a person; none may appear in a public chart.
CASE_DETAIL_KEYS = {
	"ticket_number",
	"title",
	"submitter_display",
	"description",
	"submitter_name",
	"department",
}


def _region_code(area):
	lft = frappe.db.get_value("Grievance Administrative Area", area, "lft")
	return frappe.db.get_value(
		"Grievance Administrative Area",
		{"level_name": "Region", "lft": ["<=", lft], "rgt": [">=", lft]},
		"code",
	)


def _chart(chart_id, params=None, admin=False):
	"""Build straight from the registry, past the cache."""
	return dashboard.CHARTS[chart_id].build(dashboard.Context(params or dashboard.Params(), admin))


def _kpis(params=None):
	return {row["metric"]: row["value"] for row in _chart("grvKpis", params)}


def _statuses(params=None):
	return {row["status"]: row["count"] for row in _chart("grvStatusDistribution", params)}


def _risk():
	return {row["bucket"]: row["count"] for row in _chart("grvSlaRisk")}


def _feedback():
	D = frappe.qb.DocType(dashboard_rollup.DAILY)
	row = (
		frappe.qb.from_(D)
		.select(Sum(D.feedback_count).as_("given"), Sum(D.feedback_satisfied_count).as_("satisfied"))
		.run(as_dict=True)[0]
	)
	return {key: int(value or 0) for key, value in row.items()}


def _dashboard_reader():
	"""The OAN dashboards' machine user: Grievance Dashboard Reader and nothing else."""
	email = "dashboard-reader@test.local"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Dashboard Reader",
				"send_welcome_email": 0,
				"roles": [{"role": C.ROLE_DASHBOARD_READER}],
			}
		).insert(ignore_permissions=True)
	return email


def _category_resolution(category):
	rows = _chart("grvCategoryResolution")
	return next(
		(r for r in rows if r["category"] == category), {"resolved_on_time": 0, "resolved_breached": 0}
	)


class TestDashboardCharts(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		dashboard_rollup.refresh(full=True)
		cls.before = {"kpis": _kpis(), "statuses": _statuses(), "risk": _risk(), "feedback": _feedback()}

		now = now_datetime()
		first = a_grievance()
		cls.category = first.service_category
		cls.created = [first.name]

		def case(status, **values):
			doc = a_grievance()
			cls.created.append(doc.name)
			frappe.db.set_value("Grievance", doc.name, {"status": status, "workflow_state": status, **values})
			return doc.name

		cls.submitted = case(C.STATE_SUBMITTED)
		cls.assigned_at_risk = case(C.STATE_ASSIGNED, sla_due_date=add_to_date(now, hours=3))
		cls.in_progress_breached = case(
			C.STATE_IN_PROGRESS, sla_due_date=add_to_date(now, days=-2), escalated=1, escalated_at=now
		)
		cls.more_info = case(C.STATE_MORE_INFO_NEEDED, sla_due_date=add_to_date(now, days=-2))
		cls.resolved_on_time = case(
			C.STATE_RESOLVED, resolved_at=now, sla_due_date=add_to_date(now, days=1), satisfaction_rating=5
		)
		cls.closed_late = case(
			C.STATE_CLOSED, resolved_at=now, sla_due_date=add_to_date(now, days=-1), satisfaction_rating=2
		)
		cls.rejected = case(C.STATE_REJECTED)

		cls.refreshed = dashboard_rollup.refresh()
		cls.after = {"kpis": _kpis(), "statuses": _statuses(), "risk": _risk(), "feedback": _feedback()}

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in set(cls.created):
			discard_grievance(name)
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	def delta(self, section, key):
		return self.after[section][key] - self.before[section][key]

	# Rollups -> charts
	# -----------------

	def test_refresh_reports_what_it_did(self):
		self.assertIsNotNone(self.refreshed)
		# It reaches further back when an older case was escalated in the window.
		self.assertLessEqual(
			self.refreshed["from"], add_days(getdate(), -(dashboard_rollup.INCREMENTAL_DAYS - 1))
		)
		# Other tests refresh too, so as_of is at least this run's.
		self.assertGreaterEqual(dashboard_rollup.as_of(), self.refreshed["as_of"])

	def test_total_counts_every_state(self):
		self.assertEqual(self.delta("kpis", "total"), len(set(self.created)))

	def test_awaiting_action_is_submitted_assigned_and_in_progress_only(self):
		awaiting = sum(self.delta("statuses", s) for s in sla.awaiting_action_states())
		self.assertEqual(self.delta("kpis", "awaiting_action"), awaiting)
		self.assertEqual(self.delta("statuses", C.STATE_MORE_INFO_NEEDED), 1)

	def test_resolved_is_resolved_plus_closed(self):
		self.assertEqual(self.delta("kpis", "resolved"), 2)
		self.assertEqual(self.delta("statuses", C.STATE_RESOLVED), 1)
		self.assertEqual(self.delta("statuses", C.STATE_CLOSED), 1)
		self.assertEqual(self.delta("statuses", C.STATE_REJECTED), 1)

	def test_satisfaction_comes_from_the_rating_given_on_confirmation(self):
		# Two resolved cases rated 5 and 2; only the 5 is satisfied.
		self.assertEqual(self.delta("feedback", "given"), 2)
		self.assertEqual(self.delta("feedback", "satisfied"), 1)

	def test_escalated_counts_open_escalated_cases(self):
		self.assertEqual(self.delta("kpis", "escalated"), 1)
		self.assertEqual(self.delta("risk", "escalated"), 1)

	def test_at_risk_and_breached_are_split(self):
		self.assertEqual(self.delta("risk", "at_risk"), 1)
		# The breached In Progress case counts; the More Info Needed one only if that
		# state does not pause the clock.
		paused = C.STATE_MORE_INFO_NEEDED in sla.states_in_category(sla.PAUSED)
		self.assertEqual(self.delta("risk", "breached"), 1 if paused else 2)

	def test_resolution_is_split_by_sla_outcome(self):
		row = _category_resolution(self.category)
		self.assertGreaterEqual(row["resolved_on_time"], 1)
		self.assertGreaterEqual(row["resolved_breached"], 1)

	def test_region_filter_narrows_to_that_region(self):
		code = _region_code(a_leaf_area())
		if not code:
			self.skipTest("The fixture area sits under no Region")
		narrowed = _kpis(dashboard.Params(region=(code,)))
		self.assertLessEqual(narrowed["total"], self.after["kpis"]["total"])
		self.assertGreaterEqual(narrowed["total"], len(set(self.created)) - 1)

	def test_monthly_trend_has_six_months_and_counts_this_month(self):
		rows = _chart("grvMonthlyTrend")
		self.assertEqual(len(rows), 6)
		self.assertEqual(rows[-1]["month"], getdate().strftime("%Y-%m"))
		self.assertGreaterEqual(rows[-1]["submitted"], len(set(self.created)) - 1)

	def test_net_backlog_ends_at_todays_open_count(self):
		rows = _chart("grvNetBacklogTrend")
		open_now = sum(self.after["statuses"][s] for s in sla.open_states())
		self.assertEqual(rows[-1]["backlog"], open_now)

	def test_refresh_is_idempotent(self):
		D = frappe.qb.DocType(dashboard_rollup.DAILY)
		totals = frappe.qb.from_(D).select(Sum(D.submitted_count), Sum(D.resolved_count))
		first = totals.run()
		dashboard_rollup.refresh()
		self.assertEqual(totals.run(), first)

	def test_past_snapshots_are_kept(self):
		yesterday = add_days(getdate(), -1)
		dashboard_rollup._insert(
			dashboard_rollup.SNAPSHOT,
			("snapshot_date", *dashboard_rollup.SNAPSHOT_FIELDS),
			[(yesterday, C.STATE_SUBMITTED, "", self.category, "", 0, "ok", 7, 0, None)],
		)
		dashboard_rollup.refresh()
		self.assertTrue(frappe.db.exists(dashboard_rollup.SNAPSHOT, {"snapshot_date": yesterday}))
		frappe.db.delete(dashboard_rollup.SNAPSHOT, {"snapshot_date": yesterday, "grievance_count": 7})

	def test_overlapping_refresh_is_skipped(self):
		lock = f"{frappe.local.site}:{dashboard_rollup.LOCK_KEY}"
		# Held from a second connection, as a concurrent worker would.
		other = pymysql.connect(
			host=frappe.conf.db_host or "localhost",
			port=int(frappe.conf.db_port or 3306),
			user=frappe.conf.db_user or frappe.conf.db_name,
			password=frappe.conf.db_password,
			database=frappe.conf.db_name,
		)
		try:
			with other.cursor() as cursor:
				cursor.execute("SELECT GET_LOCK(%s, 0)", (lock,))
			self.assertIsNone(dashboard_rollup.refresh())
		finally:
			other.close()

	# Public view
	# -----------

	def test_every_public_chart_builds_and_names_no_case(self):
		for chart_id in dashboard.PUBLIC_CHARTS:
			with self.subTest(chart=chart_id):
				rows = _chart(chart_id)
				self.assertIsInstance(rows, list)
				for row in rows:
					self.assertFalse(
						CASE_DETAIL_KEYS & set(row), f"{chart_id} exposes {CASE_DETAIL_KEYS & set(row)}"
					)

	def test_admin_only_charts_are_not_public(self):
		self.assertNotIn("grvRecent", dashboard.PUBLIC_CHARTS)
		self.assertNotIn("grvFilterDepartments", dashboard.PUBLIC_CHARTS)

	def test_oldest_open_names_the_case_only_for_an_admin(self):
		public = _chart("grvOldestOpen")
		admin = _chart("grvOldestOpen", admin=True)
		self.assertTrue(public and admin)
		self.assertNotIn("ticket_number", public[0])
		self.assertIn("ticket_number", admin[0])

	def test_recent_hides_anonymous_submitters(self):
		frappe.db.set_value("Grievance", self.submitted, "is_anonymous", 1)
		rows = _chart("grvRecent", dashboard.Params(limit=50), admin=True)
		row = next(
			r
			for r in rows
			if r["ticket_number"] == frappe.db.get_value("Grievance", self.submitted, "ticket_number")
		)
		self.assertIsNone(row["submitter_display"])

	# Endpoints
	# ---------

	def test_public_chart_as_dashboard_reader(self):
		frappe.set_user(_dashboard_reader())
		try:
			res = charts_api._public_endpoint("grvKpis")(category=self.category)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(res["message"], "Chart fetched successfully")
		self.assertIsInstance(res["data"], list)
		self.assertIn("as_of", res["meta"])
		self.assertEqual(res["meta"]["filters"]["service_category"], [self.category])
		self.assertNotIn("assigned_dept", res["meta"]["filters"])

	def test_public_view_ignores_admin_only_params(self):
		params = charts_api.parse_params({"assigned_dept": "No Such Dept", "limit": "999"}, admin=False)
		self.assertEqual(params.assigned_dept, ())
		self.assertEqual(params.limit, 10)

	def test_invalid_params_are_400_with_the_field(self):
		for raw, field in (
			({"region": "XX99"}, "region"),
			({"service_category": "No Such Category"}, "service_category"),
			({"from": "2026-13-01"}, "from"),
			({"from": "2026-09-30", "to": "2026-09-01"}, "from"),
			({"month": "2999-01"}, "month"),
			({"as_of_month": "2999-01"}, "as_of_month"),
			({"as_of_month": "2021-13"}, "as_of_month"),
		):
			with self.subTest(raw=raw):
				frappe.local.response = frappe._dict()
				res = charts_api._public_chart("grvKpis", raw)
				self.assertEqual(res["code"], "VALIDATION_ERROR")
				self.assertIn(field, res["details"])
				self.assertEqual(frappe.local.response.get("http_status_code"), 400)

	def test_no_chart_route_is_exempt_from_auth(self):
		from oan_auth_service.api import middleware

		from oan_grievance_service.api.router import ensure_routes_registered

		ensure_routes_registered()
		exempt = middleware._NAMESPACES["/api/v1"]["exempt_paths"]

		def is_exempt(path):
			# A path table matches by method; older oan_auth_service kept a plain set.
			match = getattr(exempt, "match", None)
			return match(path) is not None if match else path in exempt

		for chart_id in dashboard.PUBLIC_CHARTS:
			self.assertFalse(is_exempt(f"/api/v1/charts/{chart_id}"), chart_id)
		self.assertFalse(is_exempt("/api/v1/charts/grvRecent"))
		self.assertFalse(is_exempt("/api/v1/charts"))

	def test_admin_endpoint(self):
		frappe.set_user("Administrator")
		res = charts_api.get_charts(charts="grvKpis,grvRecent", limit=5)
		self.assertEqual(res["status"], "success")
		self.assertEqual(set(res["data"]), {"grvKpis", "grvRecent"})
		self.assertEqual(res["meta"]["errors"], {})
		self.assertLessEqual(len(res["data"]["grvRecent"]), 5)

		frappe.local.response = frappe._dict()
		res = charts_api.get_charts(charts="grvKpis,grvNope")
		self.assertEqual(res["code"], "NOT_FOUND")
		self.assertEqual(res["details"]["unknown_charts"], ["grvNope"])

	def test_a_failing_chart_does_not_fail_the_request(self):
		original = dashboard.CHARTS["grvByCategory"]
		dashboard.CHARTS["grvByCategory"] = dashboard.Chart(lambda ctx: 1 / 0)
		try:
			frappe.cache.delete_keys("grievance:charts:grvByCategory")
			res = charts_api.get_charts(charts="grvKpis,grvByCategory")
		finally:
			dashboard.CHARTS["grvByCategory"] = original
		self.assertEqual(res["status"], "success")
		self.assertIsNone(res["data"]["grvByCategory"])
		self.assertIn("grvByCategory", res["meta"]["errors"])
		self.assertIsInstance(res["data"]["grvKpis"], list)


class TestChartEndpointErrors(FrappeTestCase):
	"""Error paths. handle_api_errors rolls the transaction back on an error, so these
	live apart from the fixtures TestDashboardCharts builds in setUpClass."""

	def test_chart_endpoints_refuse_guests(self):
		frappe.set_user("Guest")
		try:
			frappe.local.response = frappe._dict()
			admin = charts_api.get_charts(charts="grvKpis")
			frappe.local.response = frappe._dict()
			public = charts_api._public_endpoint("grvKpis")()
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(admin["code"], "PERMISSION_DENIED")
		self.assertEqual(public["code"], "PERMISSION_DENIED")


def _rollup_totals(start, end, *metrics, category=None):
	"""Sums straight from the daily rollup for start <= stat_date <= end."""
	D = frappe.qb.DocType(dashboard_rollup.DAILY)
	query = (
		frappe.qb.from_(D)
		.select(*(Sum(getattr(D, metric)).as_(metric) for metric in metrics))
		.where(D.stat_date >= start)
		.where(D.stat_date <= end)
	)
	if category:
		query = query.where(D.service_category == category)
	row = query.run(as_dict=True)[0]
	return {metric: float(row[metric] or 0) for metric in metrics}


class TestEscalationRateCohorts(FrappeTestCase):
	"""The escalation rate is of the cases filed in the period, so a case filed in one
	month and escalated in a later one is part of the earlier month only."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		dashboard_rollup.refresh(full=True)

		today = getdate()
		cls.month_start = today.replace(day=1)
		cls.filed_on = getdate(add_months(cls.month_start, -1)).replace(day=15)
		cls.before_filed_day = _rollup_totals(
			cls.filed_on, cls.filed_on, "submitted_count", "escalated_count"
		)
		cls.before_this_month = _rollup_totals(cls.month_start, today, "escalated_count")

		# Filed last month, escalated now.
		doc = a_grievance()
		cls.name = doc.name
		frappe.db.set_value(
			"Grievance",
			doc.name,
			{
				"creation": get_datetime(f"{cls.filed_on} 10:00:00"),
				"escalated": 1,
				"escalated_at": now_datetime(),
			},
		)
		# Incremental, as the scheduler runs it: the filing day is outside the last few days.
		cls.refreshed = dashboard_rollup.refresh()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		discard_grievance(cls.name)
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	def test_escalation_is_counted_on_the_day_the_case_was_filed(self):
		after = _rollup_totals(self.filed_on, self.filed_on, "submitted_count", "escalated_count")
		self.assertEqual(after["submitted_count"] - self.before_filed_day["submitted_count"], 1)
		self.assertEqual(after["escalated_count"] - self.before_filed_day["escalated_count"], 1)

	def test_a_later_escalation_is_not_counted_in_the_month_it_happened(self):
		after = _rollup_totals(self.month_start, getdate(), "escalated_count")
		self.assertEqual(after["escalated_count"], self.before_this_month["escalated_count"])

	def test_incremental_refresh_reaches_back_to_the_filing_day(self):
		self.assertLessEqual(self.refreshed["from"], self.filed_on)

	def test_escalation_rate_never_exceeds_100(self):
		for months_back in range(4):
			month = getdate(add_months(self.month_start, -months_back)).strftime("%Y-%m")
			with self.subTest(month=month):
				rows = _chart("grvPerformanceKpis", dashboard.Params(month=month))
				rate = next(r["value"] for r in rows if r["metric"] == "escalation_rate")
				self.assertTrue(rate is None or rate <= 100, rate)


class TestAvgResolutionByCategory(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		dashboard_rollup.refresh(full=True)

		today = getdate()
		cls.yesterday = add_days(today, -1)
		cls.window_start = getdate(add_months(today.replace(day=1), -5))
		cls.created = []

		def resolved(days_open, resolved_on):
			doc = a_grievance()
			cls.created.append(doc.name)
			resolved_at = get_datetime(f"{resolved_on} 12:00:00")
			frappe.db.set_value(
				"Grievance",
				doc.name,
				{
					"status": C.STATE_RESOLVED,
					"workflow_state": C.STATE_RESOLVED,
					"creation": add_to_date(resolved_at, days=-days_open),
					"resolved_at": resolved_at,
				},
			)

		metrics = ("resolved_count", "resolution_hours_sum")
		cls.category = a_service_category()
		cls.before = _rollup_totals(cls.window_start, today, *metrics, category=cls.category)
		cls.before_yesterday = _rollup_totals(cls.yesterday, cls.yesterday, *metrics, category=cls.category)

		# One case of 2 days resolved today, two of 10 days resolved yesterday. Averaging the
		# two days' averages would give 6; the average over the cases is 22 / 3.
		resolved(2, today)
		resolved(10, cls.yesterday)
		resolved(10, cls.yesterday)
		dashboard_rollup.refresh(full=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in cls.created:
			discard_grievance(name)
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	def row(self, params=None):
		rows = _chart("grvAvgResolutionByCategory", params)
		return next(r for r in rows if r["category"] == self.category)

	def test_it_is_a_public_chart(self):
		self.assertIn("grvAvgResolutionByCategory", dashboard.PUBLIC_CHARTS)

	def test_average_weighs_every_resolved_case_equally(self):
		row = self.row()
		count = self.before["resolved_count"] + 3
		hours = self.before["resolution_hours_sum"] + 22 * 24
		self.assertEqual(row["resolved"], count)
		self.assertEqual(row["avg_resolution_days"], round(hours / count / 24, 1))

	def test_the_window_narrows_the_average(self):
		row = self.row(dashboard.Params(from_date=self.yesterday, to_date=self.yesterday))
		count = self.before_yesterday["resolved_count"] + 2
		hours = self.before_yesterday["resolution_hours_sum"] + 20 * 24
		self.assertEqual(row["resolved"], count)
		self.assertEqual(row["avg_resolution_days"], round(hours / count / 24, 1))

	def test_a_category_with_nothing_resolved_is_left_out(self):
		rows = _chart("grvAvgResolutionByCategory")
		self.assertTrue(all(r["resolved"] > 0 for r in rows))
		# Past the cases there is no resolution at all.
		far = dashboard.Params(from_date=add_days(getdate(), -500), to_date=add_days(getdate(), -499))
		self.assertEqual(
			[r for r in _chart("grvAvgResolutionByCategory", far) if r["category"] == self.category], []
		)

	def test_endpoint_takes_the_category_and_period_filters(self):
		res = charts_api.get_charts(
			charts="grvAvgResolutionByCategory",
			service_category=self.category,
			**{"from": str(self.yesterday), "to": str(self.yesterday)},
		)
		self.assertEqual(res["meta"]["errors"], {})
		rows = res["data"]["grvAvgResolutionByCategory"]
		self.assertEqual([r["category"] for r in rows], [self.category])
		self.assertEqual(rows[0]["resolved"], self.before_yesterday["resolved_count"] + 2)


def _public_update(grievance, author_type="officer", entry_type="message", is_internal=0):
	GrievanceTimeline.record(
		grievance=grievance,
		entry_type=entry_type,
		body="An update.",
		is_internal=is_internal,
		author_type=author_type,
	)


def _coverage_counts():
	"""Cases in today's snapshot, and how many of them sit with a real desk."""
	S = frappe.qb.DocType(dashboard_rollup.SNAPSHOT)
	rows = (
		frappe.qb.from_(S)
		.select(S.assigned_dept, Sum(S.grievance_count).as_("n"))
		.where(S.snapshot_date == dashboard._latest_snapshot_date())
		.where(S.status.isin(dashboard.status_order()))
		.groupby(S.assigned_dept)
		.run(as_dict=True)
	)
	total = sum(int(r.n) for r in rows)
	routed = sum(int(r.n) for r in rows if r.assigned_dept not in ("", None, C.UNROUTED_DEPARTMENT))
	return total, routed


class TestPerformanceScore(FrappeTestCase):
	"""The radar's six axes. The cases sit in the test department, which the chart is
	narrowed to. The monthly axes are exact whatever else the site holds; the two read
	from the snapshot count every case, so they are taken against a baseline."""

	@classmethod
	def dept_cases(cls):
		"""Cases in the test department in today's snapshot, and how many are resolved."""
		ctx = dashboard.Context(dashboard.Params(assigned_dept=(cls.dept,)), True)
		by_status = dashboard._by_status(dashboard._snapshot(ctx, ("status",)))
		return sum(by_status.values()), sum(by_status[s] for s in C.RESOLVED_STATES)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		dashboard_rollup.refresh(full=True)
		cls.dept = a_department()
		cls.before_cases = cls.dept_cases()
		cls.created = []

		def case(status, dept=None, **values):
			doc = a_grievance()
			cls.created.append(doc.name)
			frappe.db.set_value(
				"Grievance",
				doc.name,
				{"status": status, "workflow_state": status, "assigned_dept": dept, **values},
			)
			return doc.name

		now = now_datetime()
		# Resolved on time, rated 5, and answered by an officer.
		a = case(
			C.STATE_RESOLVED,
			cls.dept,
			resolved_at=now,
			sla_due_date=add_to_date(now, days=1),
			satisfaction_rating=5,
		)
		# Closed late and rated 2. An internal note and a reply from the submitter do not inform.
		b = case(
			C.STATE_CLOSED,
			cls.dept,
			resolved_at=now,
			sla_due_date=add_to_date(now, days=-1),
			satisfaction_rating=2,
		)
		# Resolved with no SLA and no rating. The officer's own filing entry does not inform.
		c = case(C.STATE_RESOLVED, cls.dept, resolved_at=now, sla_due_date=None)
		case(C.STATE_SUBMITTED, cls.dept)
		_public_update(a)
		_public_update(b, entry_type="note", is_internal=1)
		_public_update(b, author_type="submitter")
		_public_update(c, entry_type="submission")

		# Outside the department: one on the Unrouted desk, one with no desk at all.
		case(C.STATE_SUBMITTED, C.UNROUTED_DEPARTMENT)
		case(C.STATE_SUBMITTED, None)
		dashboard_rollup.refresh(full=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in cls.created:
			discard_grievance(name)
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	def axes(self, params=None):
		rows = _chart("grvPerformanceScore", params or dashboard.Params(assigned_dept=(self.dept,)))
		return {r["axis"]: r for r in rows}

	def test_six_axes_in_radar_order(self):
		rows = _chart("grvPerformanceScore")
		self.assertEqual(
			[r["axis"] for r in rows],
			["timeliness", "resolution_rate", "satisfaction", "transparency", "follow_up", "coverage"],
		)
		for row in rows:
			self.assertTrue(row["score"] is None or 0 <= row["score"] <= 100, row)

	def test_timeliness_is_resolved_on_time_over_resolved_with_an_sla(self):
		# On time and late; the case with no SLA is in neither.
		self.assertEqual(self.axes()["timeliness"], {"axis": "timeliness", "score": 50.0, "basis": 2})

	def test_resolution_rate_is_resolved_over_all_cases(self):
		# Three of the four new cases are resolved.
		cases, resolved = self.before_cases[0] + 4, self.before_cases[1] + 3
		self.assertEqual(self.dept_cases(), (cases, resolved))
		axis = self.axes()["resolution_rate"]
		self.assertEqual(axis["basis"], cases)
		self.assertEqual(axis["score"], round(resolved * 100 / cases, 1))

	def test_satisfaction_is_rated_4_or_5_over_rated(self):
		self.assertEqual(self.axes()["satisfaction"]["score"], 50.0)
		self.assertEqual(self.axes()["satisfaction"]["basis"], 2)

	def test_transparency_counts_only_public_officer_updates(self):
		# One of four cases has one; an internal note, the submitter's own reply and the
		# officer's filing entry are not updates.
		self.assertEqual(self.axes()["transparency"]["score"], 25.0)
		self.assertEqual(self.axes()["transparency"]["basis"], 4)

	def test_follow_up_is_rated_over_resolved(self):
		self.assertEqual(self.axes()["follow_up"]["score"], round(2 * 100 / 3, 1))
		self.assertEqual(self.axes()["follow_up"]["basis"], 3)

	def test_coverage_is_every_case_with_a_real_desk(self):
		self.assertEqual(self.axes()["coverage"]["score"], 100.0)
		everything = _chart("grvPerformanceScore")
		coverage = next(r for r in everything if r["axis"] == "coverage")
		total, routed = _coverage_counts()
		self.assertEqual(coverage["basis"], total)
		self.assertEqual(coverage["score"], round(routed * 100 / total, 1))
		# The Unrouted desk and the case with no desk both count against it.
		self.assertGreaterEqual(total - routed, 2)

	def test_a_month_with_no_cases_has_no_score(self):
		scores = self.axes(dashboard.Params(assigned_dept=(self.dept,), month="2020-01"))
		self.assertTrue(all(row["score"] is None and row["basis"] == 0 for row in scores.values()))

	def test_endpoint_returns_it_with_the_month_filter(self):
		res = charts_api.get_charts(
			charts="grvPerformanceScore", month=getdate().strftime("%Y-%m"), assigned_dept=self.dept
		)
		self.assertEqual(res["meta"]["errors"], {})
		self.assertEqual(len(res["data"]["grvPerformanceScore"]), 6)


class TestInformedCohorts(FrappeTestCase):
	"""Like escalations: an officer answering an old case counts on the day it was filed."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		dashboard_rollup.refresh(full=True)

		today = getdate()
		cls.month_start = today.replace(day=1)
		cls.filed_on = getdate(add_months(cls.month_start, -1)).replace(day=15)
		cls.before_filed_day = _rollup_totals(cls.filed_on, cls.filed_on, "submitted_count", "informed_count")
		cls.before_this_month = _rollup_totals(cls.month_start, today, "informed_count")

		doc = a_grievance()
		cls.name = doc.name
		frappe.db.set_value("Grievance", doc.name, "creation", get_datetime(f"{cls.filed_on} 10:00:00"))
		_public_update(doc.name)
		cls.refreshed = dashboard_rollup.refresh()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		discard_grievance(cls.name)
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	def test_the_update_is_counted_on_the_filing_day(self):
		after = _rollup_totals(self.filed_on, self.filed_on, "submitted_count", "informed_count")
		self.assertEqual(after["submitted_count"] - self.before_filed_day["submitted_count"], 1)
		self.assertEqual(after["informed_count"] - self.before_filed_day["informed_count"], 1)

	def test_it_is_not_counted_in_the_month_it_happened(self):
		after = _rollup_totals(self.month_start, getdate(), "informed_count")
		self.assertEqual(after["informed_count"], self.before_this_month["informed_count"])

	def test_incremental_refresh_reaches_back_to_the_filing_day(self):
		self.assertLessEqual(self.refreshed["from"], self.filed_on)


class TestResolutionRateByRegionMonth(FrappeTestCase):
	"""Without `as_of_month` the chart is today's snapshot; with it, the snapshot kept for that month.
	`month`, which a page sends to every chart at once, must not move it."""

	def setUp(self):
		frappe.set_user("Administrator")
		dashboard_rollup.refresh()
		self.category = a_service_category()
		self.day = getdate("2021-03-15")
		dashboard_rollup._insert(
			dashboard_rollup.SNAPSHOT,
			("snapshot_date", *dashboard_rollup.SNAPSHOT_FIELDS),
			[
				(self.day, C.STATE_RESOLVED, "XX01", self.category, "", 0, "", 3, 0, None),
				(self.day, C.STATE_SUBMITTED, "XX01", self.category, "", 0, "ok", 1, 0, None),
			],
		)
		self.addCleanup(frappe.db.delete, dashboard_rollup.SNAPSHOT, {"snapshot_date": self.day})

	def test_a_month_reads_the_snapshot_kept_for_it(self):
		rows = _chart("grvResolutionRateByRegion", dashboard.Params(as_of_month="2021-03"))
		self.assertEqual(
			rows,
			[{"region_code": "XX01", "region_name": "XX01", "resolved": 3, "total": 4, "rate": 75.0}],
		)

	def test_a_month_with_no_snapshot_has_no_rows(self):
		# The 2021 snapshot is from March: April's end has nothing kept inside April.
		self.assertEqual(_chart("grvResolutionRateByRegion", dashboard.Params(as_of_month="2021-04")), [])

	def test_the_current_month_matches_the_default(self):
		this_month = getdate().strftime("%Y-%m")
		self.assertEqual(
			_chart("grvResolutionRateByRegion", dashboard.Params(as_of_month=this_month)),
			_chart("grvResolutionRateByRegion"),
		)

	def test_month_does_not_move_it(self):
		self.assertEqual(
			_chart("grvResolutionRateByRegion", dashboard.Params(month="2021-03")),
			_chart("grvResolutionRateByRegion"),
		)

	def test_endpoint_keeps_the_region_chart_all_time_when_the_page_sends_month(self):
		res = charts_api.get_charts(charts="grvPerformanceKpis,grvResolutionRateByRegion", month="2021-03")
		self.assertEqual(res["data"]["grvResolutionRateByRegion"], _chart("grvResolutionRateByRegion"))
		res = charts_api.get_charts(charts="grvResolutionRateByRegion", as_of_month="2021-03")
		self.assertEqual([r["region_code"] for r in res["data"]["grvResolutionRateByRegion"]], ["XX01"])

	def test_the_default_is_unaffected_by_history(self):
		self.assertNotIn("XX01", [r["region_code"] for r in _chart("grvResolutionRateByRegion")])


class TestResolutionAndEscalationStamps(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.doc = a_grievance()
		self.addCleanup(discard_grievance, self.doc.name)

	def test_resolved_at_set_once_and_cleared_on_reopen(self):
		hooks_handlers.stamp_resolution(self.doc, C.STATE_RESOLVED)
		first = frappe.db.get_value("Grievance", self.doc.name, "resolved_at")
		self.assertIsNotNone(first)

		hooks_handlers.stamp_resolution(self.doc, C.STATE_CLOSED)
		self.assertEqual(frappe.db.get_value("Grievance", self.doc.name, "resolved_at"), first)

		self.doc.reload()
		hooks_handlers.stamp_resolution(self.doc, C.STATE_IN_PROGRESS)
		self.assertIsNone(frappe.db.get_value("Grievance", self.doc.name, "resolved_at"))

	def test_rejection_does_not_stamp_resolution(self):
		hooks_handlers.stamp_resolution(self.doc, C.STATE_REJECTED)
		self.assertIsNone(frappe.db.get_value("Grievance", self.doc.name, "resolved_at"))

	def test_escalated_at_keeps_the_first_escalation(self):
		self.assertIn("escalated_at", sla._first_escalation(self.doc))
		self.doc.escalated_at = now_datetime()
		self.assertEqual(sla._first_escalation(self.doc), {})

	def test_backfill_fills_resolved_at_from_status_history(self):
		from oan_grievance_service.patches import backfill_resolved_and_escalated_at

		frappe.db.set_value(
			"Grievance",
			self.doc.name,
			{"status": C.STATE_RESOLVED, "workflow_state": C.STATE_RESOLVED, "resolved_at": None},
		)
		backfill_resolved_and_escalated_at.execute()
		self.assertIsNotNone(frappe.db.get_value("Grievance", self.doc.name, "resolved_at"))


class TestGatewayContract(FrappeTestCase):
	"""What the gateway is told about the charts: the dashboards' key, never a user token."""

	def _root(self):
		import os

		return os.path.join(frappe.get_app_path("oan_grievance_service"), "..")

	def _generator(self):
		import importlib.util
		import os

		path = os.path.join(self._root(), "apisix", "generate_apisix_config_from_spec.py")
		spec = importlib.util.spec_from_file_location("grievance_apisix_generator", path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module

	def test_public_charts_ask_for_a_frappe_api_key(self):
		import os

		import yaml

		with open(os.path.join(self._root(), "openapi", "openapi_v1.public.yaml")) as f:
			spec = yaml.safe_load(f)
		for chart_id in dashboard.PUBLIC_CHARTS:
			operation = spec["paths"][f"/v1/charts/{chart_id}"]["get"]
			self.assertEqual(operation["security"], [{"FrappeTokenAuth": []}], chart_id)
		self.assertEqual(spec["paths"]["/v1/charts"]["get"]["security"], [{"BearerAuth": []}])
		self.assertEqual(spec["components"]["securitySchemes"]["FrappeTokenAuth"]["name"], "Authorization")

	def test_gateway_passes_the_frappe_token_through_on_the_charts(self):
		generator = self._generator()
		gw = generator.gw
		spec = gw.load_spec(generator.SPEC_PATH)
		routes = [
			r
			for r in gw.reconcile(gw.spec_routes(spec), generator.tier_overrides())
			if r["path"].startswith("/v1/charts/")
		]
		self.assertEqual({r["auth"] for r in routes}, {"frappe-token"})
		doc = gw.build_document(
			routes,
			app=generator.APP,
			tiers=generator.TIERS,
			api_upstream=generator.API_UPSTREAM,
			service_plugins={},
		)
		# Frappe checks the key; the gateway only limits by address and adds no auth.
		for route in doc["services"][0]["routes"]:
			if route["methods"] == ["OPTIONS"]:
				continue
			self.assertEqual(set(route["plugins"]), {"limit-count"})
			for rule in route["plugins"]["limit-count"]["rules"]:
				self.assertTrue(rule["key"].startswith("$remote_addr:"))
		self.assertNotIn("consumers", doc)
