# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.tests.fixtures import a_grievance


class TestGrievanceStatusHistory(FrappeTestCase):
	"""FR-10: the status trail is evidence, so it must be append-only.

	These assert the refusal itself rather than the permission flags, because the
	service layer inserts with `ignore_permissions=True` and would sail past them.
	"""

	def setUp(self):
		self.grievance = a_grievance()
		self.row = frappe.get_doc(
			{
				"doctype": "Grievance Status History",
				"grievance": self.grievance.name,
				"from_status": "Submitted",
				"to_status": "Assigned",
				"changed_by": "Administrator",
				"timestamp": frappe.utils.now_datetime(),
				"notes": "created by the immutability tests",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()

	def test_row_can_be_written_once(self):
		self.assertTrue(self.row.name)
		self.assertEqual(self.row.to_status, "Assigned")

	def test_hash_chain_generation(self):
		# At Draft, so the chain below starts with the rows this test writes.
		g = a_grievance(workflow_state="Draft")
		h1 = frappe.get_doc(
			{
				"doctype": "Grievance Status History",
				"grievance": g.name,
				"from_status": "Submitted",
				"to_status": "Assigned",
				"changed_by": "Administrator",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(h1.prev_hash, "0" * 64)
		self.assertTrue(bool(h1.row_hash))

		h2 = frappe.get_doc(
			{
				"doctype": "Grievance Status History",
				"grievance": g.name,
				"from_status": "Assigned",
				"to_status": "In Progress",
				"changed_by": "Administrator",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(h2.prev_hash, h1.row_hash)
		self.assertNotEqual(h2.row_hash, h1.row_hash)

	def test_update_is_refused(self):
		self.row.to_status = "Closed"
		with self.assertRaises(frappe.ValidationError):
			self.row.save(ignore_permissions=True)

	def test_delete_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.row.delete(ignore_permissions=True)

	def test_update_is_refused_even_for_a_reloaded_document(self):
		"""A fresh handle on the same row is refused the same way, so the guard is
		not relying on state left over from the insert."""
		again = frappe.get_doc("Grievance Status History", self.row.name)
		again.notes = "tampered"
		with self.assertRaises(frappe.ValidationError):
			again.save(ignore_permissions=True)
