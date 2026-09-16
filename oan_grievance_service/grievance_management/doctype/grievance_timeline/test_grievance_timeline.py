# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestGrievanceTimeline(FrappeTestCase):
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

		# Ensure area
		area_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "Timeline Test Woreda"}, "name"
		)
		if not area_name:
			self.area = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Timeline Test Woreda",
					"level_name": "Woreda",
					"code": "TTW",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		else:
			self.area = frappe.get_doc("Grievance Administrative Area", area_name)

		self.grievance = frappe.get_doc(
			{
				"doctype": "Grievance",
				"submitter_type": "Individual Farmer",
				"submitter_name": "Abebe",
				"contact_mobile": "+251911223344",
				"submission_channel": "Mobile App",
				"administrative_area": self.area.name,
				"service_category": "Inputs",
				"grievance_type": self.gtype_name,
				"description": "Fertilizer delivery delay for testing timeline spine.",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.flags.in_test = True
		if frappe.db.exists("Grievance", self.grievance.name):
			frappe.delete_doc("Grievance", self.grievance.name, force=True, ignore_permissions=True)

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
		self.assertIsNotNone(entry.created_on)

	def test_submitter_cannot_author_internal_entry(self):
		profile_name = frappe.db.get_value(
			"Grievance Submitter Profile", {"contact_mobile": "+251911998877"}, "name"
		)
		if profile_name:
			profile = frappe.get_doc("Grievance Submitter Profile", profile_name)
		else:
			profile = frappe.get_doc(
				{
					"doctype": "Grievance Submitter Profile",
					"submitter_type": "Individual Farmer",
					"submitter_name": "Abebe Submitter",
					"contact_mobile": "+251911998877",
				}
			).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance Submitter Profile",
			profile.name,
			force=True,
			ignore_permissions=True,
		)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Timeline",
					"grievance": self.grievance.name,
					"entry_type": "message",
					"is_internal": 1,
					"body": "Illegal internal message by submitter.",
					"author_submitter": profile.name,
				}
			).insert(ignore_permissions=True)

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
