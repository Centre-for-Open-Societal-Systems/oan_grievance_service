# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.grievance import add_note, message, timeline
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import lifecycle
from oan_grievance_service.tests.fixtures import a_department, a_leaf_area, discard_grievance


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
					"code": "001",
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

		self.area_name = a_leaf_area()

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
				"administrative_area": self.area_name,
				"service_category": "Inputs",
				"grievance_type": self.gtype_name,
				"description": "Fertilizer delivery delay for API timeline testing.",
				"assigned_to": self.officer.name,
			}
		).insert(ignore_permissions=True)

		# Inserted as a Draft; the officer's first move below needs a submitted case.
		lifecycle.transition(self.grievance, "Submit")
		frappe.local.message_log = []
		self.addCleanup(frappe.set_user, "Administrator")

	def tearDown(self):
		frappe.flags.in_test = True
		if hasattr(self, "grievance"):
			discard_grievance(self.grievance.name)
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

		# Move grievance through Assigned -> In Progress. Work starts in a department.
		if not self.grievance.assigned_dept:
			self.grievance.db_set("assigned_dept", a_department(), update_modified=False)
		lifecycle.transition(self.grievance, "Assign")
		from oan_grievance_service.api.v1.grievance import action

		action_res = action(self.grievance.ticket_number, action="Start Work")
		self.assertEqual(action_res["status"], "success")

		# 2. Officer requests more info
		GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="info_request",
			is_internal=False,
			body="Please provide the receipt number.",
			author_user=self.officer.name,
		)
		lifecycle.transition(self.grievance, "Request More Info")

		# 3. Submitter replies
		frappe.set_user(self.farmer.name)
		GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="info_response",
			is_internal=False,
			body="Receipt is #REC-98765.",
			author_submitter=self.grievance.submitter,
		)
		lifecycle.transition(self.grievance, "Submitter Reply")

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
