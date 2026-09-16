# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestGrievanceAccessAuditEvent(FrappeTestCase):
	"""FR-10: the access trail records reads, and a read cannot be un-recorded.

	Every question about a breach is a question about who looked at what, so a row
	an operator can edit afterwards answers nothing.
	"""

	def setUp(self):
		self.event = frappe.get_doc(
			{
				"doctype": "Grievance Access Audit Event",
				"timestamp": frappe.utils.now_datetime(),
				"user": "Administrator",
				"role": "Grievance Admin",
				"action": "view_detail",
				"decision": "Allowed",
				"source": "test",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()

	def test_event_can_be_written_once(self):
		self.assertTrue(self.event.name)
		self.assertEqual(self.event.action, "view_detail")

	def test_update_is_refused(self):
		self.event.decision = "Denied"
		with self.assertRaises(frappe.ValidationError):
			self.event.save(ignore_permissions=True)

	def test_delete_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.event.delete(ignore_permissions=True)

	def test_the_recorded_user_cannot_be_rewritten(self):
		"""The whole point of the trail: you cannot move a read onto someone else."""
		self.event.user = "Guest"
		with self.assertRaises(frappe.ValidationError):
			self.event.save(ignore_permissions=True)
