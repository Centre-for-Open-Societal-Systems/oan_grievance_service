# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-330: the officer view of GET /api/v1/charts, the public charts narrowed to the caller's desks.

Each case sits in a department made for this class, so a count can be asserted exactly even
when the site already holds grievances.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import charts as charts_api
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard, dashboard_rollup
from oan_grievance_service.tests.fixtures import (
	a_grievance,
	a_grievance_type,
	a_leaf_area,
	a_service_category,
	a_unique_department,
	discard_grievance,
)


def _area_at_level(level, containing_lft=None):
	filters = {"level_name": level}
	if containing_lft is not None:
		filters.update({"lft": ["<=", containing_lft], "rgt": [">=", containing_lft]})
	return frappe.db.get_value("Grievance Administrative Area", filters, "name")


def _officer(label):
	email = f"scope-{label}@test.local"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": f"Scope {label}",
				"send_welcome_email": 0,
				"roles": [{"role": C.ROLE_OFFICER}],
			}
		).insert(ignore_permissions=True)
	return email


def _total(rows):
	return next(r["value"] for r in rows if r["metric"] == "total")


class TestOfficerChartScope(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not frappe.db.exists("Grievance Role Level", "nodal_officer"):
			frappe.get_doc(
				{
					"doctype": "Grievance Role Level",
					"level_name": "Nodal Officer",
					"level_code": "nodal_officer",
					"level_order": 1,
				}
			).insert(ignore_permissions=True)

		leaf = a_leaf_area()
		lft = frappe.db.get_value("Grievance Administrative Area", leaf, "lft")
		cls.leaf = leaf
		cls.region_area = _area_at_level("Region", lft)
		cls.region_code = frappe.db.get_value("Grievance Administrative Area", cls.region_area, "code")
		cls.other_region_area = frappe.db.get_value(
			"Grievance Administrative Area",
			{"level_name": "Region", "name": ["!=", cls.region_area]},
			"name",
		)
		cls.category = a_service_category()
		cls.dept = a_unique_department("Inside")
		cls.other_dept = a_unique_department("Outside")
		cls.officer = _officer("a")
		cls.other_officer = _officer("b")

		cls.created = []
		for dept in (cls.dept, cls.dept, cls.other_dept):
			doc = a_grievance(assigned_dept=dept)
			cls.created.append(doc.name)
		dashboard_rollup.refresh()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in cls.created:
			discard_grievance(name)
		dashboard_rollup.refresh(full=True)
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _desk(self, user, area=None, department=None, **scope):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": department,
				"category_scope": scope.pop("category_scope", self.category),
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": user,
						"role_level": "nodal_officer",
						"administrative_area": area,
						"is_primary": 0,
						"active": 1,
					}
				],
				**scope,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "Grievance RBAC Assignment", doc.name, force=True, ignore_permissions=True
		)
		return doc

	def _kpi_total(self, user):
		frappe.set_user(user)
		res = charts_api.get_charts(charts="grvKpis")
		self.assertEqual(res["status"], "success", res)
		return _total(res["data"]["grvKpis"])

	def test_a_region_desk_counts_only_its_department(self):
		self._desk(self.officer, area=self.region_area, department=self.dept)
		self.assertEqual(self._kpi_total(self.officer), 2)

	def test_a_desk_without_an_area_covers_every_region(self):
		self._desk(self.officer, department=self.dept)
		self.assertEqual(self._kpi_total(self.officer), 2)

	def test_two_desks_are_alternatives(self):
		self._desk(self.officer, area=self.region_area, department=self.dept)
		self._desk(self.officer, department=self.other_dept)
		self.assertEqual(self._kpi_total(self.officer), 3)

	def test_a_desk_for_another_region_counts_nothing(self):
		if not self.other_region_area:
			self.skipTest("the site has a single region")
		self._desk(self.officer, area=self.other_region_area, department=self.dept)
		self.assertEqual(self._kpi_total(self.officer), 0)

	def test_a_desk_below_region_level_counts_nothing(self):
		"""The rollups cannot narrow a region, so the desk is not widened to it."""
		if self.leaf == self.region_area:
			self.skipTest("the fixture area is itself a region")
		self._desk(self.officer, area=self.leaf, department=self.dept)
		self.assertEqual(self._kpi_total(self.officer), 0)

	def test_a_desk_narrowed_by_grievance_type_counts_nothing(self):
		self._desk(
			self.officer,
			area=self.region_area,
			department=self.dept,
			grievance_type_scope=a_grievance_type(),
		)
		self.assertEqual(self._kpi_total(self.officer), 0)

	def test_an_officer_with_no_desk_reads_zeros(self):
		self.assertEqual(self._kpi_total(self.officer), 0)

	def test_officers_do_not_share_cached_charts(self):
		self._desk(self.officer, department=self.dept)
		self._desk(self.other_officer, department=self.other_dept)
		self.assertEqual(self._kpi_total(self.officer), 2)
		self.assertEqual(self._kpi_total(self.other_officer), 1)
		self.assertEqual(self._kpi_total(self.officer), 2)

	def test_the_region_filter_lists_only_the_officers_regions(self):
		self._desk(self.officer, area=self.region_area, department=self.dept)
		frappe.set_user(self.officer)
		res = charts_api.get_charts(charts="grvFilterRegions")
		self.assertEqual({r["region_code"] for r in res["data"]["grvFilterRegions"]}, {self.region_code})

	def test_an_officer_gets_the_public_charts_and_not_case_detail(self):
		self._desk(self.officer, department=self.dept)
		frappe.set_user(self.officer)
		res = charts_api.get_charts()
		self.assertEqual(res["status"], "success")
		self.assertEqual(set(res["data"]), set(dashboard.PUBLIC_CHARTS))

	def test_an_officer_never_receives_a_case_title_or_ticket(self):
		self._desk(self.officer, department=self.dept)
		frappe.set_user(self.officer)
		data = charts_api.get_charts()["data"]
		for chart_id, rows in data.items():
			for row in rows:
				self.assertFalse({"ticket_number", "title", "submitter_display"} & set(row), chart_id)

	def test_an_admin_is_not_scoped(self):
		frappe.set_user("Administrator")
		res = charts_api.get_charts(charts="grvKpis,grvRecent")
		self.assertEqual(set(res["data"]), {"grvKpis", "grvRecent"})
		self.assertGreaterEqual(_total(res["data"]["grvKpis"]), 3)

	def test_the_admin_view_refuses_a_scope(self):
		with self.assertRaises(ValueError):
			dashboard.get_chart("grvKpis", dashboard.Params(), admin=True, scope=())


class TestOfficerChartErrors(FrappeTestCase):
	"""Error paths. handle_api_errors rolls the transaction back on an error, so these live
	apart from the fixtures TestOfficerChartScope builds in setUpClass."""

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_an_officer_cannot_ask_for_an_admin_only_chart(self):
		frappe.set_user(_officer("errors"))
		for chart_id in ("grvRecent", "grvFilterDepartments"):
			frappe.local.response = frappe._dict()
			res = charts_api.get_charts(charts=chart_id)
			self.assertEqual(res["code"], "NOT_FOUND", chart_id)
			self.assertEqual(frappe.local.response.get("http_status_code"), 404)

	def test_a_submitter_is_refused(self):
		email = "scope-submitter@test.local"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Scope Submitter",
					"send_welcome_email": 0,
					"roles": [{"role": C.ROLE_SUBMITTER}],
				}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		frappe.local.response = frappe._dict()
		self.assertEqual(charts_api.get_charts()["code"], "PERMISSION_DENIED")

	def test_the_dashboard_reader_cannot_use_the_jwt_charts_route(self):
		email = "scope-reader@test.local"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Scope Reader",
					"send_welcome_email": 0,
					"roles": [{"role": C.ROLE_DASHBOARD_READER}],
				}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		frappe.local.response = frappe._dict()
		self.assertEqual(charts_api.get_charts()["code"], "PERMISSION_DENIED")
