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
from oan_grievance_service.setup.install import seed_role_levels, seed_workflow
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

	def setUp(self):
		frappe.set_user("Administrator")
		self.area = a_leaf_area()
		self.dept = a_department()

		self.officer1 = _ensure_test_user("cr_officer1@example.com", "Grievance Officer", "Officer One")
		self.officer2 = _ensure_test_user("cr_officer2@example.com", "Grievance Officer", "Officer Two")
		self.senior = _ensure_test_user("cr_senior@example.com", "Grievance Officer", "Senior Nodal")
		self.head = _ensure_test_user("cr_head@example.com", "Grievance Officer", "Dept Head")
		self.submitter_user = _ensure_test_user(
			"cr_submitter@example.com", "Grievance Submitter", "Submitter"
		)

		# Ensure second department for reassignment tests
		if not frappe.db.exists("Grievance Department", "Secondary Dept"):
			self.dept2 = (
				frappe.get_doc(
					{
						"doctype": "Grievance Department",
						"dept_name": "Secondary Dept",
						"email_account": "sec_dept@example.com",
						"active": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		else:
			self.dept2 = "Secondary Dept"

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
		for cr in frappe.get_all("Grievance Change Request", pluck="name"):
			frappe.delete_doc("Grievance Change Request", cr, force=True, ignore_permissions=True)
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

	def test_officer_reassignment_routes_to_supervisor(self):
		"""Assigned officer requesting reassignment routes up to their supervisor."""
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign to secondary officer",
				"reason": "Case outside my specialty",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Pending")
		self.assertEqual(cr.pending_with, self.senior)

	def test_supervisor_approval_applies_reassignment(self):
		"""Supervisor approving reassignment updates grievance assigned_to."""
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

		frappe.set_user(self.senior)
		cr.status = "Approved"
		cr.decision_note = "Approved reassignment"
		cr.save(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer2)

	def test_supervisor_auto_approval_when_requesting(self):
		"""When a supervisor/head raises a reassignment, it is auto-approved."""
		frappe.set_user(self.senior)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Direct reassignment by supervisor",
				"reason": "Urgent reassignment",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		self.assertEqual(cr.status, "Approved")
		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer2)

	def test_reassignment_rejection_leaves_grievance_unchanged(self):
		"""Rejecting reassignment keeps existing assignment."""
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

		frappe.set_user(self.senior)
		cr.status = "Rejected"
		cr.decision_note = "Maintain original assignment"
		cr.save(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(self.grievance.assigned_to, self.officer1)

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
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign",
				"reason": "Workload",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		# Officer 1 attempts to self-approve
		cr.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			cr.save(ignore_permissions=True)

	def test_already_decided_request_cannot_be_redecided(self):
		"""Cannot approve or reject an already decided request."""
		frappe.set_user(self.officer1)
		cr = frappe.get_doc(
			{
				"doctype": "Grievance Change Request",
				"grievance": self.grievance.name,
				"subject": "Reassign",
				"reason": "Workload",
				"changes": [{"fieldname": "assigned_to", "new_value": self.officer2}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.senior)
		cr.status = "Approved"
		cr.save(ignore_permissions=True)

		# Attempt to change decision
		cr.status = "Rejected"
		with self.assertRaises(frappe.ValidationError):
			cr.save(ignore_permissions=True)
