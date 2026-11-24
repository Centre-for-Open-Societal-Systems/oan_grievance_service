# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.setup.install import seed_role_levels


class TestGrievanceOfficerPosition(FrappeTestCase):
	def setUp(self):
		seed_role_levels()
		self.user1 = "pos_user_1@example.com"
		self.user2 = "pos_user_2@example.com"
		for email in (self.user1, self.user2):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": "Test",
						"last_name": email.split("@")[0],
						"send_welcome_email": 0,
						"enabled": 1,
						"roles": [{"role": "Grievance Officer"}],
					}
				).insert(ignore_permissions=True)
			else:
				u = frappe.get_doc("User", email)
				if not any(r.role == "Grievance Officer" for r in u.roles):
					u.append("roles", {"role": "Grievance Officer"})
					u.save(ignore_permissions=True)

	def tearDown(self):
		frappe.db.delete("Grievance Officer Position", {"position_title": ["like", "Test Pos%"]})

	def test_create_position_as_a_role(self):
		"""A position is a role: it needs no user, area or leave."""
		pos = frappe.get_doc(
			{
				"doctype": "Grievance Officer Position",
				"position_title": "Test Pos Vacant Woreda SMS",
				"role_level": "senior_nodal_officer",
				"active": 1,
			}
		).insert(ignore_permissions=True)
		self.assertTrue(pos.name.startswith("POS-"))
		self.assertFalse(pos.meta.has_field("occupant"))

	def test_reports_to_seniority_validation(self):
		"""Junior position cannot be supervisor of senior position."""
		pos_l2 = frappe.get_doc(
			{
				"doctype": "Grievance Officer Position",
				"position_title": "Test Pos L2",
				"role_level": "senior_nodal_officer",
				"active": 1,
			}
		).insert(ignore_permissions=True)

		pos_l1 = frappe.get_doc(
			{
				"doctype": "Grievance Officer Position",
				"position_title": "Test Pos L1",
				"role_level": "nodal_officer",
				"reports_to_position": pos_l2.name,
				"active": 1,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(pos_l1.reports_to_position, pos_l2.name)

		# Try making L2 report to L1 -> should fail
		pos_l2.reports_to_position = pos_l1.name
		with self.assertRaises(frappe.ValidationError):
			pos_l2.save(ignore_permissions=True)

	def test_circular_reporting_loop_rejected(self):
		"""Positions cannot form a loop."""
		p1 = frappe.get_doc(
			{
				"doctype": "Grievance Officer Position",
				"position_title": "Test Pos P1",
				"role_level": "nodal_officer",
				"active": 1,
			}
		).insert(ignore_permissions=True)

		p2 = frappe.get_doc(
			{
				"doctype": "Grievance Officer Position",
				"position_title": "Test Pos P2",
				"role_level": "senior_nodal_officer",
				"reports_to_position": p1.name,
			}
		)
		# Should fail because p1 order (10) is <= p2 order (20)
		with self.assertRaises(frappe.ValidationError):
			p2.insert(ignore_permissions=True)
