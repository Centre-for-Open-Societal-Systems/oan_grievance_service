# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""STG-330: Dashboard Statistics API and FR-09 reporting projection."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from oan_grievance_service.api.v1 import dashboard
from oan_grievance_service.services import dashboard_stats
from oan_grievance_service.tests.fixtures import a_leaf_area

STATUS_SUBMITTED = "Submitted"
STATUS_IN_PROGRESS = "In Progress"


class TestDashboardStatisticsAPI(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

		for role in ("Grievance Submitter", "Grievance Officer", "Grievance Admin"):
			if not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
			frappe.get_doc(
				{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "code": "IND"}
			).insert(ignore_permissions=True)

		for cat, code in (("Inputs", "001"), ("Credit", "004")):
			if not frappe.db.exists("Grievance Service Category", cat):
				frappe.get_doc(
					{
						"doctype": "Grievance Service Category",
						"category_name": cat,
						"code": code,
						"is_active": 1,
					}
				).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Department", "Dept of Agriculture"):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": "Dept of Agriculture",
					"email_account": "agri@example.com",
					"active": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Role Level", "nodal_officer"):
			frappe.get_doc(
				{
					"doctype": "Grievance Role Level",
					"level_name": "Nodal Officer",
					"level_code": "nodal_officer",
					"level_order": 1,
				}
			).insert(ignore_permissions=True)

		self.area = a_leaf_area()
		area_doc = frappe.get_doc("Grievance Administrative Area", self.area)

		# Sibling leaf under the same parent when possible, else a second leaf area.
		parent = area_doc.parent_administrative_area
		other = frappe.db.get_value(
			"Grievance Administrative Area",
			{"is_group": 0, "is_active": 1, "name": ["!=", self.area]},
			"name",
		)
		if not other and parent:
			other = (
				frappe.get_doc(
					{
						"doctype": "Grievance Administrative Area",
						"area_name": "Dashboard Other Leaf",
						"code": "DOL",
						"level_name": "Woreda",
						"parent_administrative_area": parent,
						"is_group": 0,
						"is_active": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		self.other_area = other or self.area

		self.officer_email = "dash_officer@example.com"
		if not frappe.db.exists("User", self.officer_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": self.officer_email,
					"first_name": "Dash",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Officer"}],
				}
			).insert(ignore_permissions=True)

		self.submitter_email = "dash_farmer@example.com"
		if not frappe.db.exists("User", self.submitter_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": self.submitter_email,
					"first_name": "Farmer",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)

		# Scope officer to self.area + Inputs + Dept of Agriculture only.
		existing = frappe.db.get_value(
			"Grievance RBAC Assignment Officer",
			{"user": self.officer_email, "active": 1},
			"parent",
		)
		if existing:
			frappe.delete_doc("Grievance RBAC Assignment", existing, force=True, ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": "Dept of Agriculture",
				"category_scope": "Inputs",
				"administrative_area_scope": self.area,
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": self.officer_email,
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		self.created = []
		# In-scope: Inputs + Dept + self.area
		self.created.append(self._make_grievance(STATUS_SUBMITTED, "Inputs", self.area))
		self.created.append(self._make_grievance(STATUS_IN_PROGRESS, "Inputs", self.area, breached=True))
		# Out of scope: Credit category
		self.created.append(self._make_grievance(STATUS_SUBMITTED, "Credit", self.area))
		# Out of scope: other area (when distinct)
		if self.other_area != self.area:
			self.created.append(self._make_grievance(STATUS_SUBMITTED, "Inputs", self.other_area))

		dashboard_stats.refresh_projection()
		self.addCleanup(frappe.set_user, "Administrator")

	def _make_grievance(self, status, category, area, breached=False):
		gtype = frappe.db.get_value("Grievance Type", {"service_category": category}, "name")
		if not gtype:
			gtype = (
				frappe.get_doc(
					{
						"doctype": "Grievance Type",
						"type_name": f"{category} Dashboard Type",
						"service_category": category,
						"is_active": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)

		doc = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submitter_type": "Individual Farmer",
				"submitter_name": "Dashboard Farmer",
				"contact_mobile": "+251911000111",
				"submission_channel": "Mobile App",
				"administrative_area": area,
				"service_category": category,
				"grievance_type": gtype,
				"description": "Dashboard statistics fixture grievance with enough text.",
				"consent_given": 1,
				"status": status,
				"assigned_dept": "Dept of Agriculture",
			}
		).insert(ignore_permissions=True)

		if breached:
			doc.db_set("sla_due_date", add_to_date(now_datetime(), days=-2), update_modified=False)
			doc.db_set("on_hold_since", None, update_modified=False)

		return doc

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.sql("DELETE FROM `tabGrievance Dashboard Projection`")
		for g in getattr(self, "created", []):
			if frappe.db.exists("Grievance", g.name):
				frappe.delete_doc("Grievance", g.name, force=True, ignore_permissions=True)

	def test_admin_statistics_envelope_and_shape(self):
		frappe.set_user("Administrator")
		res = dashboard.get_statistics(months=6)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["meta"]["api_version"], "v1")
		data = res["data"]
		for key in ("kpis", "by_status", "by_category", "sla_breach", "monthly_trend", "scope", "meta"):
			self.assertIn(key, data)
		self.assertEqual(data["meta"]["source"], "projection")
		self.assertIsNotNone(data["meta"]["snapshot_at"])
		self.assertTrue(data["scope"]["unrestricted"])
		self.assertGreaterEqual(data["kpis"]["total"], 3)
		self.assertGreaterEqual(data["kpis"]["sla_breached"], 1)
		self.assertEqual(len(data["monthly_trend"]), 6)
		self.assertIn("submitted", data["monthly_trend"][0])
		self.assertIn("resolved", data["monthly_trend"][0])

	def test_officer_statistics_are_scope_filtered(self):
		frappe.set_user(self.officer_email)
		res = dashboard.get_statistics()
		self.assertEqual(res["status"], "success")
		data = res["data"]
		self.assertFalse(data["scope"]["unrestricted"])
		self.assertEqual(data["scope"]["departments"], ["Dept of Agriculture"])
		self.assertEqual(data["scope"]["categories"], ["Inputs"])
		# Only the two Inputs + self.area rows.
		self.assertEqual(data["kpis"]["total"], 2)
		categories = {row["category"]: row["count"] for row in data["by_category"]}
		self.assertEqual(categories.get("Inputs"), 2)
		self.assertNotIn("Credit", categories)
		self.assertEqual(data["kpis"]["sla_breached"], 1)

	def test_reads_projection_not_live_grievance_delta(self):
		"""After projection refresh, a new grievance must not appear until rebuild."""
		frappe.set_user("Administrator")
		before = dashboard.get_statistics()["data"]["kpis"]["total"]
		extra = self._make_grievance(STATUS_SUBMITTED, "Inputs", self.area)
		self.created.append(extra)
		after = dashboard.get_statistics()["data"]["kpis"]["total"]
		self.assertEqual(after, before)
		dashboard_stats.refresh_projection()
		rebuilt = dashboard.get_statistics()["data"]["kpis"]["total"]
		self.assertEqual(rebuilt, before + 1)

	def test_admin_refresh_projection_endpoint(self):
		frappe.set_user("Administrator")
		before = dashboard.get_statistics()["data"]["kpis"]["total"]
		extra = self._make_grievance(STATUS_SUBMITTED, "Inputs", self.area)
		self.created.append(extra)
		self.assertEqual(dashboard.get_statistics()["data"]["kpis"]["total"], before)

		res = dashboard.refresh_projection()
		self.assertEqual(res["status"], "success")
		self.assertIn("snapshot_at", res["data"])
		self.assertEqual(dashboard.get_statistics()["data"]["kpis"]["total"], before + 1)

	def test_officer_cannot_refresh_projection(self):
		frappe.set_user(self.officer_email)
		res = dashboard.refresh_projection()
		self.assertEqual(res["status"], "error")

	def test_submitter_and_guest_denied(self):
		frappe.set_user(self.submitter_email)
		res = dashboard.get_statistics()
		self.assertEqual(res["status"], "error")

		frappe.set_user("Guest")
		res_guest = dashboard.get_statistics()
		self.assertEqual(res_guest["status"], "error")

	def test_invalid_months_rejected(self):
		frappe.set_user("Administrator")
		res = dashboard.get_statistics(months=99)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")
