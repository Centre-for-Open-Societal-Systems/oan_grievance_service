# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.category_assignment import (
	create_assignment,
	deactivate_assignment,
	get_assignment,
	list_assignments,
	update_assignment,
)


class TestGrievanceCategoryAssignment(FrappeTestCase):
	def setUp(self):
		self.l1 = _user("stg404-l1@example.com", "Tigist Alemu")
		self.l2 = _user("stg404-l2@example.com", "Yonas Mekonnen")
		self.category = _category("STG404 Inputs", "Z94")
		self.department = _department("STG404 Inputs Agency", "S404")
		for level, order in (("nodal_officer", 10), ("senior_nodal_officer", 20)):
			if not frappe.db.exists("Grievance Role Level", level):
				frappe.get_doc(
					{
						"doctype": "Grievance Role Level",
						"level_code": level,
						"level_name": level,
						"level_order": order,
						"is_active": 1,
					}
				).insert(ignore_permissions=True)
		frappe.set_user("Administrator")

	def test_crud_projects_desk_and_sla(self):
		created = create_assignment(
			service_category=self.category,
			department="S404",
			l1_officer=self.l1,
			l2_officer=self.l2,
			priority="High Priority",
			sla_days=14,
			auto_escalate=True,
			notify_on_submit=True,
		)
		self.assertEqual(created["status"], "success")
		assignment = created["data"]["assignment"]
		self.assertEqual(assignment["service_category"], self.category)
		self.assertEqual(assignment["department"], self.department)
		self.assertEqual(assignment["priority"], "High")
		self.assertEqual(assignment["sla_days"], 14)
		self.assertTrue(assignment["auto_escalate"])
		self.assertTrue(assignment["notify_on_submit"])
		self.assertEqual(assignment["l1_officer_name"], "Tigist Alemu")
		self.assertTrue(assignment["rbac_assignment"])

		desk = frappe.get_doc("Grievance RBAC Assignment", assignment["rbac_assignment"])
		self.assertEqual(desk.category_scope, self.category)
		self.assertEqual(desk.department_scope, self.department)
		self.assertEqual(desk.active, 1)
		officers = {row.role_level: row for row in desk.officers}
		self.assertEqual(officers["nodal_officer"].user, self.l1)
		self.assertEqual(officers["nodal_officer"].is_primary, 1)
		self.assertEqual(officers["senior_nodal_officer"].user, self.l2)

		sla_days, auto_escalate = frappe.db.get_value(
			"Grievance SLA Configuration",
			{"service_category": self.category},
			["sla_days", "auto_escalate"],
		)
		self.assertEqual(sla_days, 14)
		self.assertEqual(auto_escalate, 1)

		updated = update_assignment(
			assignment["name"],
			sla_days=10,
			notify_on_submit=False,
			l2_officer=None,
		)
		self.assertEqual(updated["status"], "success")
		self.assertEqual(updated["data"]["assignment"]["sla_days"], 10)
		self.assertFalse(updated["data"]["assignment"]["notify_on_submit"])
		self.assertIsNone(updated["data"]["assignment"]["l2_officer"])

		listed = list_assignments(service_category=self.category, priority="High")
		self.assertEqual(listed["data"]["pagination"]["total_count"], 1)
		self.assertEqual(listed["data"]["assignments"][0]["name"], assignment["name"])

		fetched = get_assignment(assignment["name"])
		self.assertEqual(fetched["data"]["assignment"]["sla_days"], 10)

		deactivated = deactivate_assignment(assignment["name"])
		self.assertEqual(deactivated["status"], "success")
		self.assertFalse(deactivated["data"]["assignment"]["active"])
		self.assertEqual(
			frappe.db.get_value("Grievance RBAC Assignment", assignment["rbac_assignment"], "active"),
			0,
		)
		self.assertEqual(
			frappe.db.get_value("Grievance SLA Configuration", {"service_category": self.category}, "active"),
			0,
		)

		again = deactivate_assignment(assignment["name"])
		self.assertFalse(again["data"]["assignment"]["active"])

	def test_duplicate_category_is_rejected(self):
		create_assignment(
			service_category=self.category,
			department=self.department,
			l1_officer=self.l1,
			sla_days=7,
		)
		duplicate = create_assignment(
			service_category=self.category,
			department=self.department,
			l1_officer=self.l1,
			sla_days=8,
		)
		self.assertEqual(duplicate["status"], "error")

	def test_missing_assignment_is_not_found(self):
		missing = get_assignment("GR-CAT-DOES-NOT-EXIST")
		self.assertEqual(missing["status"], "error")

	def test_guest_cannot_list(self):
		frappe.set_user("Guest")
		result = list_assignments()
		self.assertEqual(result["status"], "error")


def _user(email, full_name):
	if frappe.db.exists("User", email):
		return email
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"send_welcome_email": 0,
			"roles": [{"role": "Grievance Officer"}],
		}
	).insert(ignore_permissions=True)
	return email


def _category(name, code):
	if frappe.db.exists("Grievance Service Category", name):
		return name
	frappe.get_doc(
		{
			"doctype": "Grievance Service Category",
			"category_name": name,
			"code": code,
			"is_active": 1,
		}
	).insert(ignore_permissions=True)
	return name


def _department(name, short_name):
	if frappe.db.exists("Grievance Department", name):
		return name
	frappe.get_doc(
		{
			"doctype": "Grievance Department",
			"dept_name": name,
			"short_name": short_name,
			"email_account": "stg404@example.com",
			"active": 1,
		}
	).insert(ignore_permissions=True)
	return name
