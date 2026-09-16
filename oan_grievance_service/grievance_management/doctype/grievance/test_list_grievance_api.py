# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.grievance import list_grievances, track
from oan_grievance_service.services import constants as C


class TestListGrievanceAPI(FrappeTestCase):
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

		if not frappe.db.exists("Grievance Service Category", "Credit"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Credit",
					"code": "CRDT",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Type", "Fertilizer Shortage"):
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Fertilizer Shortage",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Department", "Dept of Agriculture"):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": "Dept of Agriculture",
					"active": 1,
				}
			).insert(ignore_permissions=True)

		area_name = frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": "API List Woreda"}, "name"
		)
		if not area_name:
			self.area = frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "API List Woreda",
					"level_name": "Woreda",
					"code": "ALW",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		else:
			self.area = frappe.get_doc("Grievance Administrative Area", area_name)

		# Setup users
		if not frappe.db.exists("User", "list_officer@example.com"):
			self.officer = frappe.get_doc(
				{
					"doctype": "User",
					"email": "list_officer@example.com",
					"first_name": "Officer",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Officer"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.officer = frappe.get_doc("User", "list_officer@example.com")

		if not frappe.db.exists("User", "list_farmer@example.com"):
			self.farmer = frappe.get_doc(
				{
					"doctype": "User",
					"email": "list_farmer@example.com",
					"first_name": "Farmer",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		else:
			self.farmer = frappe.get_doc("User", "list_farmer@example.com")

		self.farmer_profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_type": "Individual Farmer",
				"submitter_name": "List Farmer Submitter",
				"contact_mobile": "+251911998877",
				"user": self.farmer.name,
			}
		).insert(ignore_permissions=True)

		self.created_docs = []

		# Create sample grievances
		for i in range(5):
			g = frappe.get_doc(
				{
					"doctype": "Grievance",
					"submitter_type": "Individual Farmer",
					"submitter": self.farmer_profile.name,
					"submitter_name": f"Farmer Submitter {i}",
					"contact_mobile": "+251911998877",
					"submission_channel": "Mobile App",
					"administrative_area": self.area.name,
					"service_category": "Inputs" if i % 2 == 0 else "Credit",
					"grievance_type": "Fertilizer Shortage",
					"description": f"Grievance test issue number {i}",
					"status": C.SUBMITTED if i < 3 else C.IN_PROGRESS,
					"assigned_dept": "Dept of Agriculture",
					"assigned_to": self.officer.name if i >= 3 else None,
				}
			).insert(ignore_permissions=True)
			self.created_docs.append(g)

		self.addCleanup(frappe.set_user, "Administrator")

	def tearDown(self):
		frappe.flags.in_test = True
		for g in self.created_docs:
			if frappe.db.exists("Grievance", g.name):
				frappe.delete_doc("Grievance", g.name, force=True, ignore_permissions=True)
		if frappe.db.exists("Grievance Submitter Profile", self.farmer_profile.name):
			frappe.delete_doc(
				"Grievance Submitter Profile", self.farmer_profile.name, force=True, ignore_permissions=True
			)

	def test_list_grievances_admin_pagination(self):
		"""Admin retrieves paginated grievances."""
		frappe.set_user("Administrator")
		res = list_grievances(page=1, page_size=3)
		self.assertTrue(res.get("success"))
		data = res.get("data", {})
		self.assertEqual(len(data.get("items", [])), 3)
		self.assertGreaterEqual(data.get("pagination", {}).get("total_count", 0), 5)
		self.assertTrue(data.get("pagination", {}).get("has_next"))

	def test_list_grievances_filter_by_status(self):
		"""Filter by status."""
		frappe.set_user("Administrator")
		res = list_grievances(status=C.IN_PROGRESS)
		items = res.get("data", {}).get("items", [])
		for item in items:
			self.assertEqual(item.get("status"), C.IN_PROGRESS)

	def test_list_grievances_filter_by_status_multi(self):
		"""Filter by multiple statuses (comma-separated and list)."""
		frappe.set_user("Administrator")
		res = list_grievances(status=f"{C.SUBMITTED},{C.IN_PROGRESS}")
		items = res.get("data", {}).get("items", [])
		self.assertEqual(len(items), 5)
		statuses = {item.get("status") for item in items}
		self.assertIn(C.SUBMITTED, statuses)
		self.assertIn(C.IN_PROGRESS, statuses)

		res_list = list_grievances(status=[C.SUBMITTED, C.IN_PROGRESS])
		items_list = res_list.get("data", {}).get("items", [])
		self.assertEqual(len(items_list), 5)

	def test_list_grievances_filter_by_category(self):
		"""Filter by service category."""
		frappe.set_user("Administrator")
		res = list_grievances(service_category="Credit")
		items = res.get("data", {}).get("items", [])
		for item in items:
			self.assertEqual(item.get("service_category"), "Credit")

	def test_list_grievances_filter_by_category_multi(self):
		"""Filter by multiple service categories using category alias."""
		frappe.set_user("Administrator")
		res = list_grievances(category=["Inputs", "Credit"])
		items = res.get("data", {}).get("items", [])
		self.assertEqual(len(items), 5)

	def test_list_grievances_search(self):
		"""Search by ticket number."""
		frappe.set_user("Administrator")
		target = self.created_docs[2]
		res = list_grievances(search=target.ticket_number)
		items = res.get("data", {}).get("items", [])
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0].get("ticket_number"), target.ticket_number)

	def test_list_grievances_search_by_type_and_submitter(self):
		"""Search by grievance type and submitter name."""
		frappe.set_user("Administrator")
		res_type = list_grievances(search="Fertilizer")
		items_type = res_type.get("data", {}).get("items", [])
		self.assertEqual(len(items_type), 5)

		res_sub = list_grievances(search="Farmer Submitter 1")
		items_sub = res_sub.get("data", {}).get("items", [])
		self.assertEqual(len(items_sub), 1)
		self.assertEqual(items_sub[0].get("submitter_name"), "Farmer Submitter 1")

	def test_get_grievance_detail(self):
		"""Retrieve full grievance detail."""
		frappe.set_user("Administrator")
		target = self.created_docs[0]
		res = track(target.ticket_number)
		self.assertTrue(res.get("success"))
		data = res.get("data", {})
		self.assertEqual(data.get("ticket_number"), target.ticket_number)
		self.assertEqual(data.get("submitter_name"), target.submitter_name)
		self.assertEqual(data.get("service_category"), "Inputs")
		self.assertIn("attachments", data)
