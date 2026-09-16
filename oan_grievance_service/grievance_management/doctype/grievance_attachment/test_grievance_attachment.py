# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.grievance_management.doctype.grievance_attachment.grievance_attachment import (
	MAX_SIZE_BYTES,
)
from oan_grievance_service.tests.fixtures import a_grievance


class TestGrievanceAttachment(FrappeTestCase):
	"""The evidence panel's limits, held in the backend rather than trusted from it."""

	def setUp(self):
		self.grievance = a_grievance()

	def tearDown(self):
		frappe.db.rollback()

	def _attachment(self, **overrides):
		values = {
			"doctype": "Grievance Attachment",
			"grievance": self.grievance.name,
			"file_name": "receipt.jpg",
			"file_url": "/files/receipt.jpg",
			"mime_type": "image/jpeg",
			"size_bytes": 120_000,
			"uploaded_by_user": "Administrator",
		}
		values.update(overrides)
		return frappe.get_doc(values)

	def test_a_valid_attachment_is_accepted(self):
		doc = self._attachment().insert(ignore_permissions=True)
		self.assertEqual(doc.grievance, self.grievance.name)
		self.assertEqual(doc.scan_status, "Pending")

	def test_an_attachment_must_name_its_uploader(self):
		doc = self._attachment(uploaded_by_user=None)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_file_over_ten_megabytes_is_refused(self):
		doc = self._attachment(size_bytes=MAX_SIZE_BYTES + 1)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_an_unsupported_type_is_refused(self):
		doc = self._attachment(file_name="payload.exe", mime_type="application/x-msdownload")
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_renamed_executable_is_refused_on_its_contents(self):
		"""The client says .jpg; the sniffed type says otherwise, and that wins."""
		doc = self._attachment(file_name="holiday.jpg", mime_type="application/x-msdownload")
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_an_extension_that_disagrees_with_the_content_is_refused(self):
		doc = self._attachment(file_name="scan.jpg", mime_type="application/pdf")
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_voice_note_is_accepted(self):
		"""MP3 matters: it is how a submitter with limited literacy gives evidence."""
		doc = self._attachment(
			file_name="statement.mp3", mime_type="audio/mpeg", size_bytes=2_400_000
		).insert(ignore_permissions=True)
		self.assertEqual(doc.mime_type, "audio/mpeg")

	def test_nothing_is_servable_until_it_has_been_scanned(self):
		doc = self._attachment().insert(ignore_permissions=True)
		self.assertFalse(doc.is_servable())

		doc.db_set("scan_status", "Clean", update_modified=False)
		doc.reload()
		self.assertTrue(doc.is_servable())

	def test_an_infected_file_keeps_its_row_and_stays_unservable(self):
		doc = self._attachment().insert(ignore_permissions=True)
		doc.db_set("scan_status", "Infected", update_modified=False)
		doc.reload()
		self.assertFalse(doc.is_servable())
		self.assertTrue(frappe.db.exists("Grievance Attachment", doc.name))

	def test_a_response_from_another_case_is_refused(self):
		# A response moves the case to Pending Submitter, which is only legal from In Progress.
		other = a_grievance(status="In Progress")
		response = frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": other.name,
				"response_type": "Resolved",
				"action_taken": "Handled on the other case.",
				"proposed_close_date": frappe.utils.add_days(None, 5),
				"resolution_summary": "<p>Closed on the other case.</p>",
			}
		).insert(ignore_permissions=True)

		doc = self._attachment(response=response.name)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)
