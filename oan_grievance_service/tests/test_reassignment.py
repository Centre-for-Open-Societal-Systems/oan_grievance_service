# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Tests for reassignment resolution and apply logic (services/reassignment.py).

Covers:
R1. Handoff within the department: only assigned_to is in the changes.
R2. Across departments with the category changing: category and type follow desk, and SLA restarts.
R3. Across departments in the same category: SLA is unchanged.
R4. No officer named: routing picks one. A nodal current officer covering dept2 is kept.
R5. Receiving officer with no desk: Grievance Officer gets Invalid Officer, Grievance Admin succeeds.
R6. No desk in the target department and no officer named: error.
R7. Target department has desks for categories A and B, and case is category C:
    - no target_category: error listing A and B
    - target_category=B: category B, and officer comes from B's desk
R8. Target department has desks for A and C, and case is category C:
    - C is kept, SLA unchanged, officer from C's desk
R9. Target department has one category-A desk and case is category C:
    - A is used automatically
R10. target_category that no receiving desk accepts: error.
R11. Chosen category has several possible types and none given: error asking for target_grievance_type.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, now_datetime, today

from oan_grievance_service.api.v1.change_request import raise_change_request
from oan_grievance_service.services import lifecycle, reassignment
from oan_grievance_service.setup.install import seed_role_levels, seed_workflow
from oan_grievance_service.tests.fixtures import (
	a_department,
	a_grievance,
	a_leaf_area,
	discard_grievance,
)


def _ensure_user(email, role="Grievance Officer", first_name=None):
	if not frappe.db.exists("User", email):
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name or email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	else:
		doc = frappe.get_doc("User", email)
	return doc.email


def _ensure_department(name):
	existing = frappe.db.get_value("Grievance Department", {"dept_name": name}, "name")
	if existing:
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Department",
				"dept_name": name,
				"email_account": f"{frappe.scrub(name)}@example.com",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_category(name, code, sla_days=10):
	cat = frappe.db.get_value("Grievance Service Category", {"code": code}, "name")
	if not cat:
		cat = (
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": name,
					"code": code,
					"is_active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	if not frappe.db.exists("Grievance SLA Configuration", {"service_category": cat, "active": 1}):
		frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": cat,
				"sla_days": sla_days,
				"active": 1,
			}
		).insert(ignore_permissions=True)
	return cat


def _ensure_type(name, category):
	existing = frappe.db.get_value(
		"Grievance Type", {"type_name": name, "service_category": category}, "name"
	)
	if existing:
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": name,
				"service_category": category,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


class TestReassignmentResolution(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_role_levels()
		seed_workflow()
		for t in frappe.get_all(
			"Grievance Type",
			filters={"type_name": ["in", ["Type A2", "Type M1", "Type M2"]]},
			pluck="name",
		):
			frappe.delete_doc("Grievance Type", t, force=True, ignore_permissions=True)

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.area = a_leaf_area()
		self.dept1 = _ensure_department("Dept Alpha Reassign")
		self.dept2 = _ensure_department("Dept Beta Reassign")

		self.officer1 = _ensure_user("reassign_officer1@example.com", "Grievance Officer", "Officer 1")
		self.officer2 = _ensure_user("reassign_officer2@example.com", "Grievance Officer", "Officer 2")
		self.officer3 = _ensure_user("reassign_officer3@example.com", "Grievance Officer", "Officer 3")

		self.cat_a = _ensure_category("CatA_Test", "CTA", sla_days=5)
		self.cat_b = _ensure_category("CatB_Test", "CTB", sla_days=10)
		self.cat_c = _ensure_category("CatC_Test", "CTC", sla_days=15)

		self.type_a = _ensure_type("Type A1", self.cat_a)
		self.type_b = _ensure_type("Type B1", self.cat_b)
		self.type_c = _ensure_type("Type C1", self.cat_c)

		# Initial case in dept1, cat_c, type_c, officer1
		self.desk1 = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept1,
				"category_scope": self.cat_c,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer1, "role_level": "nodal_officer", "is_primary": 1, "active": 1},
					{"user": self.officer2, "role_level": "nodal_officer", "is_primary": 0, "active": 1},
				],
			}
		).insert(ignore_permissions=True)

		self.grievance = a_grievance(
			assigned_dept=self.dept1,
			assigned_to=self.officer1,
			service_category=self.cat_c,
			grievance_type=self.type_c,
			administrative_area=self.area,
		)
		lifecycle.transition(self.grievance, "Assign")
		lifecycle.transition(self.grievance, "Start Work")

	def tearDown(self):
		frappe.set_user("Administrator")
		for d in frappe.get_all("Grievance RBAC Assignment", pluck="name"):
			frappe.delete_doc("Grievance RBAC Assignment", d, force=True, ignore_permissions=True)
		for cr in frappe.get_all("Grievance Change Request", pluck="name"):
			frappe.delete_doc("Grievance Change Request", cr, force=True, ignore_permissions=True)
		if hasattr(self, "grievance") and frappe.db.exists("Grievance", self.grievance.name):
			discard_grievance(self.grievance.name)
		super().tearDown()

	def test_r1_handoff_within_department_only_assigned_to_in_changes(self):
		"""R1: Handoff within same department only returns assigned_to in changes dict."""
		changes = reassignment.resolve(
			self.grievance,
			self.dept1,
			officer=self.officer2,
			actor=self.officer1,
		)
		self.assertNotIn("assigned_dept", changes)
		self.assertEqual(changes.get("assigned_to"), self.officer2)
		self.assertNotIn("service_category", changes)
		self.assertNotIn("grievance_type", changes)

	def test_r2_across_departments_category_changes_and_sla_restarts(self):
		"""R2: Category and type follow target desk; approved reassignment restarts SLA."""
		# Desk in dept2 accepting cat_a and type_a
		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_a,
				"grievance_type_scope": self.type_a,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		initial_sla_start = self.grievance.sla_start_at
		changes = reassignment.resolve(
			self.grievance,
			self.dept2,
			officer=self.officer3,
			actor="Administrator",
		)
		self.assertEqual(changes.get("assigned_dept"), self.dept2)
		self.assertEqual(changes.get("assigned_to"), self.officer3)
		self.assertEqual(changes.get("service_category"), self.cat_a)
		self.assertEqual(changes.get("grievance_type"), self.type_a)

		# Apply change request
		cr = raise_change_request(self.grievance, "Reassign across dept", changes)
		self.assertEqual(cr.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept2)
		self.assertEqual(self.grievance.service_category, self.cat_a)
		self.assertEqual(self.grievance.grievance_type, self.type_a)
		self.assertEqual(self.grievance.escalated, 0)
		self.assertIsNotNone(self.grievance.sla_due_date)
		self.assertNotEqual(str(self.grievance.sla_start_at), str(initial_sla_start))

	def test_r3_across_departments_same_category_sla_unchanged(self):
		"""R3: Across departments with same category: SLA due date is unchanged."""
		# Desk in dept2 accepting same category cat_c
		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_c,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		old_due = self.grievance.sla_due_date
		old_start = self.grievance.sla_start_at

		changes = reassignment.resolve(
			self.grievance,
			self.dept2,
			officer=self.officer3,
			actor="Administrator",
		)
		self.assertEqual(changes.get("assigned_dept"), self.dept2)
		self.assertNotIn("service_category", changes)

		cr = raise_change_request(self.grievance, "Move to dept2 same cat", changes)
		self.assertEqual(cr.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept2)
		self.assertEqual(str(self.grievance.sla_due_date), str(old_due))
		self.assertEqual(str(self.grievance.sla_start_at), str(old_start))

	def test_r4_no_officer_named_picks_one_or_keeps_nodal_officer(self):
		"""R4: No officer named picks from strategy, or keeps current officer if covering target."""
		# Case 4a: Target dept has officer3
		desk2 = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_c,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		changes = reassignment.resolve(self.grievance, self.dept2, actor="Administrator")
		self.assertEqual(changes.get("assigned_to"), self.officer3)

		# Case 4b: Current officer (officer1) also has a desk covering dept2
		desk2.append(
			"officers",
			{"user": self.officer1, "role_level": "nodal_officer", "is_primary": 0, "active": 1},
		)
		desk2.save(ignore_permissions=True)

		changes_nodal = reassignment.resolve(self.grievance, self.dept2, actor="Administrator")
		# Current officer is officer1, who covers dept2 desk -> keeps officer1, so assigned_to not in changes
		self.assertNotIn("assigned_to", changes_nodal)

	def test_r5_receiving_officer_with_no_desk_validates_actor_permission(self):
		"""R5: Grievance Officer gets 'Invalid Officer'; Grievance Admin succeeds."""
		# officer3 has no desk in dept1
		with self.assertRaises(frappe.ValidationError) as ctx:
			reassignment.resolve(
				self.grievance,
				self.dept1,
				officer=self.officer3,
				actor=self.officer1,
			)
		self.assertIn("Invalid Officer", str(ctx.exception))

		# Admin caller succeeds
		changes = reassignment.resolve(
			self.grievance,
			self.dept1,
			officer=self.officer3,
			actor="Administrator",
		)
		self.assertEqual(changes.get("assigned_to"), self.officer3)

	def test_r6_no_desk_in_target_department_and_no_officer_named_errors(self):
		"""R6: If target department has no desk and no officer named, throw error."""
		# dept2 has no desk
		with self.assertRaises(frappe.ValidationError) as ctx:
			reassignment.resolve(self.grievance, self.dept2, actor=self.officer1)
		self.assertIn("No assignment in", str(ctx.exception))

	def test_r7_target_dept_has_desks_a_and_b_case_is_c(self):
		"""R7: Desks for A and B, case is C: error if none chosen; picking B succeeds."""
		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_a,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer2, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_b,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		# Without target_category -> error listing A and B
		with self.assertRaises(frappe.ValidationError) as ctx:
			reassignment.resolve(self.grievance, self.dept2, actor="Administrator")
		self.assertIn("Choose target_category", str(ctx.exception))

		# With target_category=B -> category B, officer comes from B's desk (officer3)
		changes = reassignment.resolve(self.grievance, self.dept2, actor="Administrator", category=self.cat_b)
		self.assertEqual(changes.get("service_category"), self.cat_b)
		self.assertEqual(changes.get("assigned_to"), self.officer3)

	def test_r8_target_dept_has_desks_a_and_c_case_is_c(self):
		"""R8: Target dept has desks for A and C, case is C: C is kept, officer from C's desk."""
		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_a,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer2, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_c,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		changes = reassignment.resolve(self.grievance, self.dept2, actor="Administrator")
		self.assertNotIn("service_category", changes)
		self.assertEqual(changes.get("assigned_to"), self.officer3)

	def test_r9_target_dept_has_one_cat_a_desk_and_case_is_c(self):
		"""R9: Exactly one category on target department: automatically selected."""
		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_a,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		changes = reassignment.resolve(self.grievance, self.dept2, actor="Administrator")
		self.assertEqual(changes.get("service_category"), self.cat_a)
		self.assertEqual(changes.get("assigned_to"), self.officer3)

	def test_r10_target_category_not_accepted_by_desk_throws_error(self):
		"""R10: target_category that no receiving desk accepts raises 'does not handle'."""
		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": self.cat_a,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError) as ctx:
			reassignment.resolve(self.grievance, self.dept2, actor="Administrator", category=self.cat_b)
		self.assertIn("does not handle", str(ctx.exception))

	def test_r11_chosen_category_multiple_types_none_given_throws_error(self):
		"""R11: If chosen category has multiple possible types and none given, throw error."""
		cat_multi = _ensure_category("CatMulti_Test", "CTM", sla_days=10)
		_ensure_type("Type M1", cat_multi)
		type_m2 = _ensure_type("Type M2", cat_multi)

		frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": cat_multi,
				"active": 1,
				"effective_from": today(),
				"officers": [
					{"user": self.officer3, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError) as ctx:
			reassignment.resolve(self.grievance, self.dept2, actor="Administrator")
		self.assertIn("Choose target_grievance_type", str(ctx.exception))

		# Providing target_grievance_type succeeds
		changes = reassignment.resolve(
			self.grievance, self.dept2, actor="Administrator", grievance_type=type_m2
		)
		self.assertEqual(changes.get("grievance_type"), type_m2)
