# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Acceptance tests for Grievance Change Request.

Covers:
- Submitter anonymity request creation, approval, and rejection
- Department and officer reassignment with hierarchy routing & auto-approval
- SLA deferral request validation (+days) and approval
- Hierarchy forwarding on timeout and admin queue fallback
- Immutability of frozen fields and stale state prevention
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, now_datetime, today

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle
from oan_grievance_service.setup.install import seed_notifications, seed_role_levels, seed_workflow
from oan_grievance_service.tests.fixtures import (
	a_department,
	a_grievance,
	a_leaf_area,
	discard_grievance,
)


def _ensure_test_user(email, role="Grievance Officer", first_name=None):
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


class TestGrievanceChangeRequest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_role_levels()
		seed_workflow()
		seed_notifications()

	def setUp(self):
		frappe.set_user("Administrator")
		self.area = a_leaf_area()
		self.dept = a_department()

		self.officer1 = _ensure_test_user("cr_officer1@example.com", "Grievance Officer", "Officer One")
		self.officer2 = _ensure_test_user("cr_officer2@example.com", "Grievance Officer", "Officer Two")
		self.senior = _ensure_test_user("cr_senior@example.com", "Grievance Officer", "Senior Nodal")
		self.head = _ensure_test_user("cr_head@example.com", "Grievance Officer", "Dept Head")
		self.head2 = _ensure_test_user("cr_head2@example.com", "Grievance Officer", "Dept 2 Head")
		self.submitter_user = _ensure_test_user(
			"cr_submitter@example.com", "Grievance Submitter", "Submitter"
		)

		# Ensure second department for reassignment tests (distinct from self.dept)
		dept2_name = f"Secondary Dept {frappe.generate_hash(length=6)}"
		self.dept2 = (
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": dept2_name,
					"email_account": f"sec_{frappe.generate_hash(length=4)}@example.com",
					"active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

		# Setup RBAC hierarchy
		# officer1 (nodal_officer) -> senior (senior_nodal_officer) -> head (department_head)
		self.assignment1 = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept,
				"category_scope": "Inputs",
				"active": 1,
				"effective_from": today(),
				"officers": [
					{
						"user": self.officer1,
						"role_level": "nodal_officer",
						"reports_to": self.senior,
						"is_primary": 1,
						"active": 1,
					},
					{
						"user": self.officer2,
						"role_level": "nodal_officer",
						"reports_to": self.senior,
						"is_primary": 0,
						"active": 1,
					},
					{
						"user": self.senior,
						"role_level": "senior_nodal_officer",
						"reports_to": self.head,
						"is_primary": 0,
						"active": 1,
					},
					{
						"user": self.head,
						"role_level": "department_head",
						"is_primary": 0,
						"active": 1,
					},
				],
			}
		).insert(ignore_permissions=True)

		self.assignment2 = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": self.dept2,
				"category_scope": "Inputs",
				"active": 1,
				"effective_from": today(),
				"officers": [
					{
						"user": self.officer2,
						"role_level": "nodal_officer",
						"reports_to": self.head2,
						"is_primary": 1,
						"active": 1,
					},
					{
						"user": self.head2,
						"role_level": "department_head",
						"is_primary": 0,
						"active": 1,
					},
				],
			}
		).insert(ignore_permissions=True)

		self.grievance = a_grievance(
			assigned_dept=self.dept,
			assigned_to=self.officer1,
		)
		lifecycle.transition(self.grievance, "Assign")
		lifecycle.transition(self.grievance, "Start Work")

	def tearDown(self):
		frappe.set_user("Administrator")
		if hasattr(self, "assignment1") and frappe.db.exists(
			"Grievance RBAC Assignment", self.assignment1.name
		):
			frappe.delete_doc(
				"Grievance RBAC Assignment", self.assignment1.name, force=True, ignore_permissions=True
			)
		if hasattr(self, "assignment2") and frappe.db.exists(
			"Grievance RBAC Assignment", self.assignment2.name
		):
			frappe.delete_doc(
				"Grievance RBAC Assignment", self.assignment2.name, force=True, ignore_permissions=True
			)
		for cr in frappe.get_all("Grievance Change Request", pluck="name"):
			frappe.delete_doc("Grievance Change Request", cr, force=True, ignore_permissions=True)
		if hasattr(self, "dept2") and frappe.db.exists("Grievance Department", self.dept2):
			frappe.delete_doc("Grievance Department", self.dept2, force=True, ignore_permissions=True)
		if hasattr(self, "grievance") and frappe.db.exists("Grievance", self.grievance.name):
			discard_grievance(self.grievance.name)

	# ---------------------------------------------------------
	# Anonymity Change Request Tests
	# ---------------------------------------------------------

	def test_submitter_anonymity_request_creation_and_routing(self):
		"""Submitter requests anonymity when pending approval; routes to assigned officer."""
		self.grievance.db_set("anonymity_status", "Pending Approval", update_modified=False)
		self.grievance.db_set("is_anonymous", 0, update_modified=False)

		frappe.set_user(self.submitter_user)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request Anonymity",
				"reason": "Risk of retaliation in kebele",
				"changes": [{"fieldname": "anonymity_status", "new_value": "Approved"}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.officer1)
		self.assertEqual(cr.requested_by, self.submitter_user)
		self.assertEqual(len(cr.changes), 1)
		self.assertEqual(cr.changes[0].old_value, "Pending Approval")

	def test_submitter_cannot_request_non_anonymity_fields(self):
		"""Submitter cannot request department or SLA changes."""
		frappe.set_user(self.submitter_user)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Grievance Change Request",
					"grievance": self.grievance.name,
					"subject": "Illegal field change",
					"changes": [{"fieldname": "assigned_dept", "new_value": self.dept2}],
				}
			).insert(ignore_permissions=True)

	def test_anonymity_approval(self):
		"""Approving anonymity sets is_anonymous=1 and updates anonymity_status and approver."""
		self.grievance.db_set("anonymity_status", "Pending Approval", update_modified=False)
		self.grievance.db_set("is_anonymous", 0, update_modified=False)

		frappe.set_user(self.submitter_user)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request Anonymity",
				"reason": "Fear of retaliation",
				"changes": [{"fieldname": "anonymity_status", "new_value": "Approved"}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.officer1)
		cr.status = "Approved"
		cr.decision_note = "Valid concern; anonymity granted"
		cr.save(ignore_permissions=True)

		self.assertEqual(cr.status, "Approved")
		self.assertEqual(cr.decided_by, self.officer1)

		self.grievance.reload()
		self.assertEqual(self.grievance.is_anonymous, 1)
		self.assertEqual(self.grievance.anonymity_status, "Approved")
		self.assertEqual(self.grievance.anonymity_approved_by, self.officer1)

	def test_anonymity_rejection(self):
		"""Rejecting anonymity keeps is_anonymous=0 and sets anonymity_status to Rejected."""
		self.grievance.db_set("anonymity_status", "Pending Approval", update_modified=False)
		self.grievance.db_set("is_anonymous", 0, update_modified=False)

		frappe.set_user(self.submitter_user)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request Anonymity",
				"reason": "Fear of retaliation",
				"changes": [{"fieldname": "anonymity_status", "new_value": "Approved"}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.officer1)
		cr.status = "Rejected"
		cr.decision_note = "Identity disclosure necessary to verify local plot"
		cr.save(ignore_permissions=True)

		self.assertEqual(cr.status, "Rejected")
		self.grievance.reload()
		self.assertEqual(self.grievance.is_anonymous, 0)
		self.assertEqual(self.grievance.anonymity_status, "Rejected")

	# ---------------------------------------------------------
	# Reassignment Request Tests
	# ---------------------------------------------------------

	def test_officer_reassignment_within_dept_routes_to_dept_head(self):
		"""Officer requesting reassignment within department routes to dept head; grievance unchanged."""
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign to secondary officer",
				"reason": "Workload balancing",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.head)
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer1)

	def test_senior_reassignment_within_dept_routes_to_dept_head(self):
		"""Senior nodal reassigning within dept routes to head; outranking is not enough."""
		frappe.set_user(self.senior)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign by senior",
				"reason": "Rebalance",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.head)
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer1)

	def test_dept_head_reassignment_within_dept_auto_approves(self):
		"""Department head reassigning within dept is approved on insert and applied immediately."""
		frappe.set_user(self.head)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Direct reassignment by head",
				"reason": "Urgent reassignment",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Approved")
		self.assertIsNone(cr.pending_with)
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer2)

	def test_officer_reassignment_to_other_dept_routes_to_target_dept_head(self):
		"""Officer moving case to another dept routes to target dept head, not source head."""
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign to secondary department",
				"reason": "Case requires secondary department handling",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
					{"fieldname": "assigned_to", "new_value": self.officer2},
				],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.head2)
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept)
		self.assertEqual(self.grievance.assigned_to, self.officer1)

	def test_source_dept_head_reassignment_to_other_dept_routes_to_target_dept_head(self):
		"""Source dept head cannot push case to target dept alone; routes to target head."""
		frappe.set_user(self.head)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Handoff to dept 2",
				"reason": "Wrong jurisdiction",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
					{"fieldname": "assigned_to", "new_value": self.officer2},
				],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.head2)

	def test_target_dept_head_approves_or_rejects_reassignment(self):
		"""Target head approving applies reassignment with timeline & notification; rejecting leaves unchanged."""
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign to dept 2",
				"reason": "Transferred",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
					{"fieldname": "assigned_to", "new_value": self.officer2},
				],
			}
		).insert(ignore_permissions=True)

		# Reject test
		frappe.set_user(self.head2)
		cr.status = "Rejected"
		cr.decision_note = "Cannot accept case"
		cr.save(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept)
		self.assertEqual(self.grievance.assigned_to, self.officer1)

		# New request to test approve
		frappe.set_user(self.officer1)
		cr2 = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign to dept 2 retry",
				"reason": "Transferred retry",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
					{"fieldname": "assigned_to", "new_value": self.officer2},
				],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.head2)
		cr2.status = "Approved"
		cr2.decision_note = "Accepted"
		cr2.save(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept2)
		self.assertEqual(self.grievance.assigned_to, self.officer2)

		# Timeline entry verification
		timeline_entries = frappe.get_all(
			"Grievance Timeline",
			filters={"grievance": self.grievance.name},
			fields=["body", "entry_type"],
		)
		self.assertTrue(any("Reassign to dept 2 retry" in t.body for t in timeline_entries))

		# Notification queued for new officer
		logs = frappe.get_all(
			"Grievance Notification Log",
			filters={
				"grievance": self.grievance.name,
				"event": C.EVENT_ASSIGNED_MANUAL,
				"recipient": self.officer2,
			},
		)
		self.assertTrue(len(logs) > 0)

	def test_admin_reassignment_auto_approved(self):
		"""Administrator moves case to dept2: Approved on insert."""
		frappe.set_user("Administrator")
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Admin reassignment",
				"reason": "Administrative move",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
					{"fieldname": "assigned_to", "new_value": self.officer2},
				],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept2)

	def test_reassignment_to_dept_without_head_routes_to_admin_queue(self):
		"""Target department without head routes to admin queue (pending_with is None)."""
		dept3 = (
			frappe.get_doc(
				{
					"doctype": "Grievance Department",
					"dept_name": f"Headless Dept {frappe.generate_hash(length=4)}",
					"email_account": f"headless_{frappe.generate_hash(length=4)}@example.com",
					"active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

		desk3 = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": dept3,
				"category_scope": "Inputs",
				"active": 1,
				"effective_from": today(),
				"officers": [
					{
						"user": self.officer2,
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		try:
			frappe.set_user(self.officer1)
			cr = frappe.get_doc(
				{
					"doctype": "Grievance Change Request",
					"grievance": self.grievance.name,
					"subject": "Move to headless dept",
					"reason": "Routing",
					"changes": [
						{"fieldname": "assigned_dept", "new_value": dept3},
						{"fieldname": "assigned_to", "new_value": self.officer2},
					],
				}
			).insert(ignore_permissions=True)

			self.assertEqual(cr.status, "Pending")
			self.assertIsNone(cr.pending_with)
		finally:
			frappe.delete_doc("Grievance RBAC Assignment", desk3.name, force=True, ignore_permissions=True)
			frappe.delete_doc("Grievance Department", dept3, force=True, ignore_permissions=True)

	def test_notification_queued_for_approving_head_when_pending(self):
		"""Notification row is queued for the approving head when a request goes pending."""
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign to secondary officer",
				"reason": "Workload",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.head)

		logs = frappe.get_all(
			"Grievance Notification Log",
			filters={
				"grievance": self.grievance.name,
				"event": C.EVENT_REASSIGNMENT_REQUESTED,
				"recipient": self.head,
			},
		)
		self.assertTrue(len(logs) > 0)

	def test_reassignment_requires_approval_flag_off_auto_approves(self):
		"""When reassignment_requires_approval is 0 on source desk, reassignment auto-approves."""
		self.assignment1.db_set("reassignment_requires_approval", 0)
		self.assignment1.reload()

		frappe.set_user(self.officer1)
		# 1. Within dept
		cr1 = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Direct move within dept",
				"reason": "Direct",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(cr1.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer2)

		# 2. To dept2
		cr2 = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Direct move to dept2",
				"reason": "Direct across dept",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
				],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(cr2.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept2)

		# Restore
		self.assignment1.db_set("reassignment_requires_approval", 1)

	def test_source_flag_on_target_flag_off_and_subsequent_handoff(self):
		"""Moving from desk with approval ON to OFF is Pending; once moved, handoff in target desk is direct."""
		self.assignment1.db_set("reassignment_requires_approval", 1)
		self.assignment2.db_set("reassignment_requires_approval", 0)

		# Step 1: officer1 moving dept -> dept2 is Pending with head2
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Move to dept2",
				"reason": "Move",
				"changes": [
					{"fieldname": "assigned_dept", "new_value": self.dept2},
					{"fieldname": "assigned_to", "new_value": self.officer2},
				],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.head2)

		# Step 2: head2 approves
		frappe.set_user(self.head2)
		cr.status = "Approved"
		cr.save(ignore_permissions=True)
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_dept, self.dept2)

		# Step 3: Now that case sits on dept2 (which has flag OFF), handoff within dept2 is auto-approved
		frappe.set_user(self.officer2)
		cr_internal = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Handoff within dept2",
				"reason": "Direct handoff",
				"changes": [{"fieldname": "assigned_to", "new_value": self.head2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr_internal.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.head2)

	def test_no_desk_found_for_source_requires_approval(self):
		"""When no source desk is found, safe default is that approval is required."""
		self.assignment1.db_set("active", 0)

		try:
			frappe.set_user(self.officer1)
			cr = frappe.get_doc(
				{
					"doctype": "Grievance Change Request",
					"grievance": self.grievance.name,
					"subject": "Move with no source desk",
					"reason": "Move",
					"changes": [
						{"fieldname": "assigned_dept", "new_value": self.dept2},
						{"fieldname": "assigned_to", "new_value": self.officer2},
					],
				}
			).insert(ignore_permissions=True)

			self.assertEqual(cr.status, "Pending")
			self.assertEqual(cr.pending_with, self.head2)
		finally:
			self.assignment1.db_set("active", 1)

	def test_change_request_rejection_leaves_grievance_unchanged(self):
		"""Rejecting a routed change request (SLA extension) keeps existing values."""
		initial_deadline = add_days(today(), 5)
		new_deadline = add_days(today(), 10)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request SLA Extension",
				"reason": "Need more time",
				"changes": [{"fieldname": "sla_due_date", "new_value": str(new_deadline)}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.senior)
		cr.status = "Rejected"
		cr.decision_note = "Maintain original deadline"
		cr.save(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(str(self.grievance.sla_due_date)[:10], str(initial_deadline)[:10])

	# ---------------------------------------------------------
	# SLA Deferral Request Tests
	# ---------------------------------------------------------

	def test_sla_deferral_request_and_approval(self):
		"""Officer requests SLA deferral (+5 days), supervisor approves."""
		initial_deadline = add_days(today(), 5)
		new_deadline = add_days(today(), 10)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Extend SLA Deadline",
				"reason": "Awaiting soil lab sample results",
				"changes": [{"fieldname": "sla_due_date", "new_value": str(new_deadline)}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.senior)
		cr.status = "Approved"
		cr.decision_note = "Approved SLA extension"
		cr.save(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(str(self.grievance.sla_due_date)[:10], str(new_deadline)[:10])

	def test_sla_deferral_cannot_move_deadline_earlier(self):
		"""Deferral earlier than current due date is rejected on validation."""
		initial_deadline = add_days(today(), 10)
		earlier_deadline = add_days(today(), 5)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Change Request",
					"grievance": self.grievance.name,
					"subject": "Invalid Deferral",
					"reason": "Test",
					"changes": [{"fieldname": "sla_due_date", "new_value": str(earlier_deadline)}],
				}
			).insert(ignore_permissions=True)

	def test_sla_deferral_cannot_exceed_max_days(self):
		"""Deferral beyond max allowed ceiling (30 days) is rejected."""
		initial_deadline = add_days(today(), 5)
		excessive_deadline = add_days(today(), 40)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Change Request",
					"grievance": self.grievance.name,
					"subject": "Excessive Deferral",
					"reason": "Too far",
					"changes": [{"fieldname": "sla_due_date", "new_value": str(excessive_deadline)}],
				}
			).insert(ignore_permissions=True)

	# ---------------------------------------------------------
	# Forwarding & Escalation Tests
	# ---------------------------------------------------------

	def test_forwarding_up_the_hierarchy(self):
		"""forward() hands the request one step up the chain; reaching the top lands in admin queue."""
		initial_deadline = add_days(today(), 5)
		new_deadline = add_days(today(), 10)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request SLA Extension",
				"reason": "Complex investigation",
				"changes": [{"fieldname": "sla_due_date", "new_value": str(new_deadline)}],
			}
		).insert(ignore_permissions=True)

		# Initially with senior
		self.assertEqual(cr.pending_with, self.senior)

		# Forward 1: senior -> head
		cr.forward()
		cr.reload()
		self.assertEqual(cr.pending_with, self.head)

		# Forward 2: head -> None (admin queue)
		cr.forward()
		cr.reload()
		self.assertIsNone(cr.pending_with)

	def test_stale_scheduled_forwarding(self):
		"""forward_stale_change_requests() identifies overdue change requests and forwards them."""
		from oan_grievance_service.tasks import forward_stale_change_requests

		initial_deadline = add_days(today(), 5)
		new_deadline = add_days(today(), 10)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request SLA Extension",
				"reason": "Complex investigation",
				"changes": [{"fieldname": "sla_due_date", "new_value": str(new_deadline)}],
			}
		).insert(ignore_permissions=True)

		# Set pending_since in the past beyond escalation hours
		frappe.db.set_value(
			"Grievance Change Request",
			cr.name,
			"pending_since",
			add_to_date(now_datetime(), hours=-60),
			update_modified=False,
		)

		count = forward_stale_change_requests()
		self.assertGreaterEqual(count, 1)

		cr.reload()
		self.assertEqual(cr.pending_with, self.head)

	# ---------------------------------------------------------
	# Immutability & Validation Guards
	# ---------------------------------------------------------

	def test_cannot_raise_change_request_on_closed_case(self):
		"""Change requests can only be raised on open cases (docstatus == 1)."""
		self.grievance.db_set("docstatus", 2, update_modified=False)
		frappe.set_user(self.officer1)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Grievance Change Request",
					"grievance": self.grievance.name,
					"subject": "Invalid request on closed case",
					"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
				}
			).insert(ignore_permissions=True)

	def test_requester_cannot_approve_their_own_request(self):
		"""Officer cannot decide their own change request unless unrestricted."""
		initial_deadline = add_days(today(), 5)
		new_deadline = add_days(today(), 10)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request SLA Extension",
				"reason": "Need more time",
				"changes": [{"fieldname": "sla_due_date", "new_value": str(new_deadline)}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.senior)

		# Officer 1 attempts to self-approve
		cr.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			cr.save(ignore_permissions=True)

	def test_already_decided_request_cannot_be_redecided(self):
		"""Cannot approve or reject an already decided request."""
		initial_deadline = add_days(today(), 5)
		new_deadline = add_days(today(), 10)
		self.grievance.db_set("sla_due_date", initial_deadline, update_modified=False)

		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Request SLA Extension",
				"reason": "Need more time",
				"changes": [{"fieldname": "sla_due_date", "new_value": str(new_deadline)}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.senior)
		cr.status = "Approved"
		cr.save(ignore_permissions=True)

		# Attempt to change decision
		cr.status = "Rejected"
		with self.assertRaises(frappe.ValidationError):
			cr.save(ignore_permissions=True)
