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
from frappe.utils import add_days, add_to_date, getdate, now_datetime

from oan_grievance_service.api.v1 import charts as charts_api
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard, dashboard_rollup, hooks_handlers, sla
from oan_grievance_service.tests.fixtures import a_grievance, a_leaf_area, discard_grievance

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
		cls.draft = a_grievance(workflow_state=C.STATE_DRAFT).name
		cls.created.append(cls.draft)

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
		self.assertEqual(
			self.refreshed["from"], add_days(getdate(), -(dashboard_rollup.INCREMENTAL_DAYS - 1))
		)
		# Other tests refresh too, so as_of is at least this run's.
		self.assertGreaterEqual(dashboard_rollup.as_of(), self.refreshed["as_of"])

	def test_total_counts_every_state_but_draft(self):
		# Every fixture but the draft.
		self.assertEqual(self.delta("kpis", "total"), len(set(self.created)) - 1)

	def test_awaiting_action_is_submitted_assigned_and_in_progress_only(self):
		awaiting = sum(self.delta("statuses", s) for s in C.AWAITING_ACTION_STATES)
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
		open_now = sum(self.after["statuses"][s] for s in C.OPEN_STATES)
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
		for chart_id in dashboard.PUBLIC_CHARTS:
			self.assertNotIn(f"/api/v1/charts/{chart_id}", exempt)
		self.assertNotIn("/api/v1/charts/grvRecent", exempt)
		self.assertNotIn("/api/v1/charts", exempt)

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
	"""What Kong is told about the charts: the dashboards' key, never a user token."""

	def _root(self):
		import os

		return os.path.join(frappe.get_app_path("oan_grievance_service"), "..")

	def _generator(self):
		import importlib.util
		import os

		path = os.path.join(self._root(), "kong", "generate_kong_config_from_spec.py")
		spec = importlib.util.spec_from_file_location("grievance_kong_generator", path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module

	def test_public_charts_ask_for_a_frappe_api_key(self):
		import os

		import yaml

		with open(os.path.join(self._root(), "openapi", "openapi_v1.public.yaml")) as f:
			spec = yaml.safe_load(f)
		for chart_id in dashboard.PUBLIC_CHARTS:
			operation = spec["paths"][f"/api/v1/charts/{chart_id}"]["get"]
			self.assertEqual(operation["security"], [{"FrappeTokenAuth": []}], chart_id)
		self.assertEqual(spec["paths"]["/api/v1/charts"]["get"]["security"], [{"BearerAuth": []}])
		self.assertEqual(spec["components"]["securitySchemes"]["FrappeTokenAuth"]["name"], "Authorization")

	def test_kong_passes_the_frappe_token_through_on_the_charts(self):
		generator = self._generator()
		routes = [
			{**r, "tier": generator.TIER_OVERRIDES[(r["method"], r["path"])]}
			for r in generator.spec_routes(generator.load_spec(generator.SPEC_PATH))
			if r["path"].startswith("/api/v1/charts/")
		]
		self.assertEqual({r["auth"] for r in routes}, {"frappe-token"})
		config = generator.build_config(routes)
		for route in config["services"][0]["routes"]:
			plugins = {p["name"]: p["config"] for p in route["plugins"]}
			self.assertEqual(set(plugins), {"rate-limiting"})
			self.assertEqual(plugins["rate-limiting"]["limit_by"], "ip")
		self.assertNotIn("oan-dashboards", {c["username"] for c in config["consumers"]})
