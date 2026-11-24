# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestGrievanceDeferralPolicy(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.rollback()
		self.policy = frappe.get_doc("Grievance Deferral Policy")
		self.orig_max_days = self.policy.max_deferral_days
		self.orig_timers = [row.as_dict() for row in self.policy.state_timers]

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_negative_max_deferral_days_raises_error(self):
		self.policy.max_deferral_days = -1
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.policy.validate()
		self.assertIn("cannot be negative", str(ctx.exception))

	def test_state_timer_duplicate_state_raises_error(self):
		self.policy.set(
			"state_timers",
			[
				{"workflow_state": "In Progress", "hours": 24, "on_expiry": "Escalate"},
				{"workflow_state": "In Progress", "hours": 48, "on_expiry": "Escalate"},
			],
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.policy.validate_state_timers()
		self.assertIn("already has a timer", str(ctx.exception))

	def test_state_timer_invalid_workflow_state_raises_error(self):
		self.policy.set(
			"state_timers",
			[
				{"workflow_state": "NonExistentState", "hours": 24, "on_expiry": "Escalate"},
			],
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.policy.validate_state_timers()
		self.assertIn("is not a state of the Grievance Workflow", str(ctx.exception))

	def test_state_timer_zero_or_negative_hours_raises_error(self):
		self.policy.set(
			"state_timers",
			[
				{"workflow_state": "In Progress", "hours": 0, "on_expiry": "Escalate"},
			],
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.policy.validate_state_timers()
		self.assertIn("Hours must be greater than zero", str(ctx.exception))

	def test_state_timer_invalid_workflow_auto_action_raises_error(self):
		# Submitted has no "Auto Resolve" transition in the workflow
		self.policy.set(
			"state_timers",
			[
				{"workflow_state": "Submitted", "hours": 24, "on_expiry": "Auto Resolve"},
			],
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.policy.validate_state_timers()
		self.assertIn("the Workflow has no Auto Resolve from Submitted", str(ctx.exception))

	def test_state_timer_escalate_on_stopped_state_raises_error(self):
		# Closed is a Stopped SLA category state
		self.policy.set(
			"state_timers",
			[
				{"workflow_state": "Closed", "hours": 24, "on_expiry": "Escalate"},
			],
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.policy.validate_state_timers()
		self.assertIn("is stopped", str(ctx.exception))

	def test_valid_state_timers_pass(self):
		self.policy.max_deferral_days = 15
		self.policy.auto_escalation_threshold = 80
		self.policy.set(
			"state_timers",
			[
				{"workflow_state": "Resolved", "hours": 48, "on_expiry": "Auto Close"},
				{"workflow_state": "More Info Needed", "hours": 72, "on_expiry": "Escalate"},
			],
		)
		# Should not throw
		self.policy.validate()
