# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.permissions import (
	active_scopes,
	find_officer_by_role_level,
	grievance_query_conditions,
	has_grievance_permission,
)
from oan_grievance_service.services import routing


class TestGrievanceRBACAssignment(FrappeTestCase):
	def setUp(self):
		# Create test users
		self.users = {}
		for name, email in [
			("Primary Officer", "prim_officer@example.com"),
			("Secondary Officer", "sec_officer@example.com"),
			("Least Loaded Officer", "least_officer@example.com"),
		]:
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": name,
						"roles": [{"role": "Grievance Officer"}],
					}
				).insert(ignore_permissions=True)
			else:
				doc = frappe.get_doc("User", email)
			self.users[email] = doc

		# Ensure department & category
		if not frappe.db.exists("Grievance Department", "Unified Agri Dept"):
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": "Unified Agri Dept",
					"email_account": "unified_agri@example.com",
					"active": 1,
				}
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

		# Ensure role level
		if not frappe.db.exists("Grievance Role Level", "nodal_officer"):
			frappe.get_doc(
				{
					"doctype": "Grievance Role Level",
					"level_code": "nodal_officer",
					"level_name": "Nodal Officer",
					"hierarchy_order": 10,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Grievance Submitter Type", "Individual Farmer"):
			frappe.get_doc(
				{
					"doctype": "Grievance Submitter Type",
					"type_name": "Individual Farmer",
					"code": "IND",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		self.gtype_name = frappe.db.get_value("Grievance Type", {"type_name": "Seed Quality Issue"}, "name")
		if not self.gtype_name:
			gtype = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Seed Quality Issue",
					"service_category": "Inputs",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
			self.gtype_name = gtype.name

		if not frappe.db.exists("Grievance Administrative Area", "TLW"):
			frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Leaf Woreda",
					"level_name": "Woreda",
					"code": "TLW",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

	def test_active_scopes_and_permissions(self):
		"""Verify active_scopes and has_grievance_permission query the unified desk record."""
		desk = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": "Unified Agri Dept",
				"category_scope": "Inputs",
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": "prim_officer@example.com",
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		scopes = active_scopes("prim_officer@example.com")
		self.assertIsNotNone(desk.name)
		self.assertTrue(any(s.get("department_scope") == "Unified Agri Dept" for s in scopes))

		# Check permission on a ticket matching department and category
		fake_grievance = frappe._dict(
			{
				"assigned_dept": "Unified Agri Dept",
				"service_category": "Inputs",
				"assigned_to": "other_officer@example.com",
				"status": "In Progress",
			}
		)
		self.assertTrue(
			has_grievance_permission(fake_grievance, ptype="read", user="prim_officer@example.com")
		)

	def test_two_tier_routing_strategies(self):
		"""Verify Tier 2 officer resolution under Primary First, Round Robin, and Least Loaded."""
		desk = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": "Unified Agri Dept",
				"category_scope": "Inputs",
				"routing_strategy": "Round Robin",
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": "prim_officer@example.com",
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					},
					{
						"user": "sec_officer@example.com",
						"role_level": "nodal_officer",
						"is_primary": 0,
						"active": 1,
					},
				],
			}
		).insert(ignore_permissions=True)

		# First round robin picks first (both have NULL last_assigned_at)
		picked1 = routing.pick_officer_by_strategy(desk)
		self.assertIn(picked1, ["prim_officer@example.com", "sec_officer@example.com"])

		# Second round robin picks the other officer
		desk.reload()
		picked2 = routing.pick_officer_by_strategy(desk)
		self.assertNotEqual(picked1, picked2)

		# Test Least Loaded
		desk.routing_strategy = "Least Loaded"
		desk.save(ignore_permissions=True)
		picked_ll = routing.pick_officer_by_strategy(desk)
		self.assertIsNotNone(picked_ll)

	def test_officer_reports_to_and_escalation_target(self):
		"""Verify reports_to on officer child table resolves supervisor and reassigns on escalation."""
		from oan_grievance_service.services import sla

		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": "Unified Agri Dept",
				"category_scope": "Inputs",
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{
						"user": "prim_officer@example.com",
						"role_level": "nodal_officer",
						"reports_to": "sec_officer@example.com",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		supervisor = sla.get_officer_supervisor("prim_officer@example.com", department="Unified Agri Dept")
		self.assertEqual(supervisor, "sec_officer@example.com")

		# Create fake grievance and escalate
		fake_g = frappe.get_doc(
			{
				"doctype": "Grievance",
				"ticket_number": "OROM-BISH-INPT-9999",
				"submission_channel": "Mobile App",
				"submitter_type": "Individual Farmer",
				"submitter_name": "Test Farmer",
				"contact_mobile": "+251911223344",
				"administrative_area": "TLW",
				"service_category": "Inputs",
				"grievance_type": self.gtype_name,
				"description": "Seed germination failed across the plot",
				"assigned_dept": "Unified Agri Dept",
				"assigned_to": "prim_officer@example.com",
				"status": "Assigned",
			}
		).insert(ignore_permissions=True)

		# Escalate L1
		res = sla.escalate(fake_g, level="L1", trigger="System", reassign=True)
		self.assertTrue(res)
		fake_g.reload()
		self.assertEqual(fake_g.escalated, 1)
		self.assertEqual(fake_g.escalation_level, 1)
		self.assertEqual(fake_g.assigned_to, "sec_officer@example.com")
