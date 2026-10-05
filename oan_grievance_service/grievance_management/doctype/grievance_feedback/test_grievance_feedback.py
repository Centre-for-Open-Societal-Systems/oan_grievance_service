# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.grievance import feedback as submit_feedback
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle
from oan_grievance_service.tests.fixtures import a_grievance, discard_grievance


class TestGrievanceFeedback(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.grievance = a_grievance()
		lifecycle.transition(self.grievance, "Assign", automated=True)
		lifecycle.transition(self.grievance, "Start Work")

	def tearDown(self):
		super().tearDown()
		discard_grievance(self.grievance.name)

	def test_feedback_rejected_on_in_progress(self):
		"""Feedback cannot be given while a case is still in progress."""
		fb = frappe.get_doc(
			{
				"doctype": "Grievance Feedback",
				"grievance": self.grievance.name,
				"rating": 4,
				"comments": "Great service",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			fb.insert(ignore_permissions=True)

	def test_feedback_allowed_on_resolved_and_closed(self):
		"""Feedback can be submitted on resolved cases and creates timeline entry."""
		# Respond to reach Resolved
		frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": self.grievance.name,
				"response_type": "Resolved",
				"action_taken": "Replaced faulty seed bags.",
				"resolution_summary": "Replaced seed bags at warehouse.",
			}
		).insert(ignore_permissions=True)

		self.grievance.reload()
		self.assertEqual(self.grievance.workflow_state, "Resolved")

		fb = frappe.get_doc(
			{
				"doctype": "Grievance Feedback",
				"grievance": self.grievance.name,
				"rating": 5,
				"comments": "Very satisfied with the speed!",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(fb.rating, 5)

		# Check Grievance has rating mirrored
		g_doc = frappe.get_doc("Grievance", self.grievance.name)
		self.assertEqual(g_doc.satisfaction_rating, 5)
		self.assertEqual(g_doc.satisfaction_comments, "Very satisfied with the speed!")

		# Check timeline entry created
		timeline = frappe.get_all(
			"Grievance Timeline",
			filters={"grievance": self.grievance.name, "entry_type": "feedback"},
			fields=["body", "ref_doctype", "ref_docname"],
		)
		self.assertEqual(len(timeline), 1)
		self.assertIn("Rating 5/5", timeline[0].body)
		self.assertEqual(timeline[0].ref_docname, fb.name)

	def test_feedback_api_endpoint(self):
		"""Test POST /api/v1/grievance/{ticket_number}/feedback endpoint."""
		frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": self.grievance.name,
				"response_type": "Resolved",
				"action_taken": "Replaced faulty seed bags.",
				"resolution_summary": "Replaced seed bags at warehouse.",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.grievance.owner or "Administrator")
		res = submit_feedback(
			ticket_number=self.grievance.name,
			rating=4,
			comments="Satisfactory resolution.",
			feedback_type="Resolution",
		)
		frappe.set_user("Administrator")

		self.assertEqual(res["data"]["rating"], 4)
		self.assertEqual(res["data"]["comments"], "Satisfactory resolution.")

		g_doc = frappe.get_doc("Grievance", self.grievance.name)
		self.assertEqual(g_doc.satisfaction_rating, 4)

	def test_feedback_rejected_for_non_owner_submitter(self):
		"""A submitter cannot insert feedback for someone else's grievance."""
		frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": self.grievance.name,
				"response_type": "Resolved",
				"action_taken": "Resolved case issue.",
				"resolution_summary": "Resolved.",
			}
		).insert(ignore_permissions=True)

		other_user = f"unauthorized_submitter_{frappe.generate_hash(length=6)}@example.com"
		if not frappe.db.exists("User", other_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": other_user,
					"first_name": "Unauthorized",
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)

		try:
			frappe.set_user(other_user)
			fb = frappe.get_doc(
				{
					"doctype": "Grievance Feedback",
					"grievance": self.grievance.name,
					"rating": 5,
					"comments": "Illegitimate feedback",
				}
			)
			with self.assertRaises(frappe.PermissionError):
				fb.insert()
		finally:
			frappe.set_user("Administrator")

	def test_feedback_api_endpoint_as_submitter(self):
		"""Test submitter role calling POST /api/v1/grievances/{ticket_number}/feedback directly."""
		frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": self.grievance.name,
				"response_type": "Resolved",
				"action_taken": "Fixed issue.",
				"resolution_summary": "Fixed issue.",
			}
		).insert(ignore_permissions=True)

		submitter_user = f"citizen_{frappe.generate_hash(length=6)}@example.com"
		if not frappe.db.exists("User", submitter_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": submitter_user,
					"first_name": "Citizen",
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)

		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"user": submitter_user,
				"submitter_name": "Citizen User",
				"submitter_type": "Individual Farmer",
				"contact_mobile": "+251911999888",
				"active": 1,
			}
		).insert(ignore_permissions=True)

		self.grievance.db_set("submitter", profile.name, update_modified=False)
		self.grievance.db_set("owner", submitter_user, update_modified=False)

		try:
			frappe.set_user(submitter_user)
			res = submit_feedback(
				ticket_number=self.grievance.ticket_number or self.grievance.name,
				rating=5,
				comments="Awesome redressal experience!",
				feedback_type="Resolution",
			)
			self.assertEqual(res["data"]["rating"], 5)
			self.assertEqual(res["data"]["comments"], "Awesome redressal experience!")
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"Grievance Submitter Profile", profile.name, force=True, ignore_permissions=True
			)
			frappe.delete_doc("User", submitter_user, force=True, ignore_permissions=True)
