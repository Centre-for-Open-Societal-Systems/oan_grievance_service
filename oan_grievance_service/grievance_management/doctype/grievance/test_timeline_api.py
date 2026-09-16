# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.grievance import add_note, message, timeline
from oan_grievance_service.services import lifecycle


class TestTimelineAPI(FrappeTestCase):
	def setUp(self):
		if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
			frappe.get_doc(
				{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "code": "IND"}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Service Category", "Inputs"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Inputs",
					"code": "INPT",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		gtype_name = frappe.db.get_value("Grievance Type", {"type_name": "Fertilizer Shortage"}, "name")
		if not gtype_name:
			self.gtype = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Fertilizer Shortage",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
			gtype_name = self.gtype.name
		self.gtype_name = gtype_name

		area_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "API Timeline Woreda"}, "name"
		)
		if not area_name:
			self.area = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "API Timeline Woreda",
					"level_name": "Woreda",
					"code": "ATW",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		else:
			self.area = frappe.get_doc("Grievance Administrative Area", area_name)

		# Setup users
		if not frappe.db.exists("User", "timeline_officer@example.com"):
			self.officer = frappe.get_doc(
				{
					"doctype": "User",
					"email": "timeline_officer@example.com",
					"first_name": "Officer",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Officer"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.officer = frappe.get_doc("User", "timeline_officer@example.com")

		if not frappe.db.exists("User", "timeline_farmer@example.com"):
			self.farmer = frappe.get_doc(
				{
					"doctype": "User",
					"email": "timeline_farmer@example.com",
					"first_name": "Farmer",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer = frappe.get_doc("User", "timeline_farmer@example.com")

		profile_name = frappe.db.get_value(
			"Grievance Submitter Profile", {"contact_mobile": "+251911445566"}, "name"
		)
		if profile_name:
			self.farmer_profile = frappe.get_doc("Grievance Submitter Profile", profile_name)
		else:
			self.farmer_profile = frappe.get_doc(
				{
					"doctype": "Grievance Submitter Profile",
					"submitter_type": "Individual Farmer",
					"submitter_name": "Farmer Submitter",
					"contact_mobile": "+251911445566",
					"user": self.farmer.name,
				}
			).insert(ignore_permissions=True)

		self.grievance = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submitter_type": "Individual Farmer",
				"submitter": self.farmer_profile.name,
				"submitter_name": "Farmer Submitter",
				"contact_mobile": "+251911445566",
				"submission_channel": "Mobile App",
				"administrative_area": self.area.name,
				"service_category": "Inputs",
				"grievance_type": self.gtype_name,
				"description": "Fertilizer delivery delay for API timeline testing.",
			}
		).insert(ignore_permissions=True)

		self.addCleanup(frappe.set_user, "Administrator")

	def tearDown(self):
		frappe.flags.in_test = True
		if hasattr(self, "grievance") and frappe.db.exists("Grievance", self.grievance.name):
			frappe.delete_doc("Grievance", self.grievance.name, force=True, ignore_permissions=True)
		if hasattr(self, "farmer_profile") and frappe.db.exists(
			"Grievance Submitter Profile", self.farmer_profile.name
		):
			frappe.delete_doc(
				"Grievance Submitter Profile", self.farmer_profile.name, force=True, ignore_permissions=True
			)

	def test_timeline_lifecycle_flow(self):
		"""Test that lifecycle events generate timeline entries and visibility filtering works."""
		frappe.set_user(self.officer.name)

		# 1. Officer adds internal note
		note_res = add_note(
			self.grievance.ticket_number, body="Investigating warehouse logs.", is_internal=True
		)
		self.assertTrue(note_res["data"]["is_internal"])

		# Move grievance through Assigned -> In Progress
		lifecycle.change_status(self.grievance, "Assigned")
		lifecycle.accept(self.grievance)

		# 2. Officer requests more info
		lifecycle.request_more_info(self.grievance, "Please provide the receipt number.")

		# 3. Submitter replies
		frappe.set_user(self.farmer.name)
		lifecycle.submitter_replies(self.grievance, "Receipt is #REC-98765.")

		# 4. Verify Officer timeline view (sees public + internal)
		frappe.set_user(self.officer.name)
		officer_view = timeline(self.grievance.ticket_number)
		officer_entries = officer_view["data"]["timeline"]
		internals = {e["is_internal"] for e in officer_entries}
		types = [e["entry_type"] for e in officer_entries]

		self.assertIn(True, internals)
		self.assertIn(False, internals)
		self.assertIn("note", types)
		self.assertIn("info_request", types)
		self.assertIn("info_response", types)
		self.assertIn("status_change", types)

		# 5. Verify Submitter timeline view (sees ONLY public, zero internal notes)
		frappe.set_user(self.farmer.name)
		submitter_view = timeline(self.grievance.ticket_number)
		submitter_entries = submitter_view["data"]["timeline"]
		for e in submitter_entries:
			self.assertFalse(e["is_internal"])
			self.assertNotEqual(e["body"], "Investigating warehouse logs.")

	def test_timeline_pagination(self):
		"""Test limit and keyset pagination on timeline."""
		frappe.set_user(self.officer.name)
		for i in range(5):
			add_note(self.grievance.ticket_number, body=f"Note {i}", is_internal=False)

		res = timeline(self.grievance.ticket_number, limit=3)
		self.assertEqual(len(res["data"]["timeline"]), 3)
		self.assertTrue(res["data"]["has_more"])
		self.assertIsNotNone(res["data"]["next_cursor"])
