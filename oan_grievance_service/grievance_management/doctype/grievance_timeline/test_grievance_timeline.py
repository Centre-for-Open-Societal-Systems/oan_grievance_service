# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.tests.fixtures import a_leaf_area


class TestGrievanceTimeline(FrappeTestCase):
	def setUp(self):
		frappe.flags.in_test = True

		# Ensure masters
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

		self.grievance = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submitter_type": "Individual Farmer",
				"submitter_name": "Abebe",
				"contact_mobile": "+251911223344",
				"submission_channel": "Mobile App",
				"administrative_area": self.area_name,
				"service_category": "Inputs",
				"grievance_type": self.gtype_name,
				"description": "Fertilizer delivery delay for testing timeline spine.",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.flags.in_test = True
		if hasattr(self, "grievance") and frappe.db.exists("Grievance", self.grievance.name):
			frappe.db.delete("Grievance", {"name": self.grievance.name})

	def test_timeline_entry_creation(self):
		entry = frappe.get_doc(
			{
				"doctype": "Grievance Timeline",
				"grievance": self.grievance.name,
				"entry_type": "note",
				"is_internal": 1,
				"body": "Officer internal case note.",
				"author_user": "Administrator",
			}
		).insert(ignore_permissions=True)

		self.assertTrue(entry.name.startswith("GR-TIME-"))
		self.assertEqual(entry.is_internal, 1)
		self.assertEqual(entry.author_type, "officer")
		self.assertEqual(entry.author_user, "Administrator")
		self.assertIsNotNone(entry.created_on)

	def test_submitter_author_type_cannot_author_internal(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Timeline",
					"grievance": self.grievance.name,
					"entry_type": "message",
					"is_internal": 1,
					"body": "Illegal internal message with author_type submitter.",
					"author_type": "submitter",
				}
			).insert(ignore_permissions=True)

	def test_submitter_user_cannot_author_internal(self):
		user_email = "farmer_timeline_test@example.com"
		if not frappe.db.exists("User", user_email):
			test_user = frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "Farmer",
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
			self.addCleanup(frappe.db.delete, "User", {"name": test_user.name})
			self.addCleanup(frappe.db.delete, "Has Role", {"parent": test_user.name})

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Timeline",
					"grievance": self.grievance.name,
					"entry_type": "message",
					"is_internal": 1,
					"body": "Illegal internal message by non-staff user.",
					"author_user": user_email,
				}
			).insert(ignore_permissions=True)

	def test_timeline_record_idempotency_scoped_to_author(self):
		from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
			GrievanceTimeline,
		)

		client_msg_id = "test-msg-id-12345"

		# Author A records message
		entry_a1 = GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="message",
			body="Message from A",
			author_user="Administrator",
			client_message_id=client_msg_id,
		)

		# Author A repeats same message
		entry_a2 = GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="message",
			body="Message from A again",
			author_user="Administrator",
			client_message_id=client_msg_id,
		)
		self.assertEqual(entry_a1.name, entry_a2.name)

		# Author B uses same client_message_id -> should create distinct entry
		entry_b = GrievanceTimeline.record(
			grievance=self.grievance.name,
			entry_type="message",
			body="Message from B",
			author_user="Guest",
			client_message_id=client_msg_id,
		)
		self.assertNotEqual(entry_a1.name, entry_b.name)

	def test_timeline_immutability(self):
		entry = frappe.get_doc(
			{
				"doctype": "Grievance Timeline",
				"grievance": self.grievance.name,
				"entry_type": "status_change",
				"is_internal": 0,
				"body": "Status changed to In Progress",
			}
		).insert(ignore_permissions=True)

		entry.body = "Modified body"
		with self.assertRaises(frappe.ValidationError):
			entry.save(ignore_permissions=True)
