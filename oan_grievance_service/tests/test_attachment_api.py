# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""The upload endpoint, end to end.

The scanning service already has its own tests for the three checks in isolation.
What these cover is that the endpoint actually applies them -- that a renamed
executable is refused at the door rather than merely refusable, and that a file
nobody has scanned yet cannot be downloaded.
"""

import io

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1 import attachment
from oan_grievance_service.services import scanning
from oan_grievance_service.tests.fixtures import a_grievance

WINDOWS_EXECUTABLE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"


def _pdf() -> bytes:
	"""A structurally valid PDF.

	Not a handful of header bytes: core's File runs pdf_contains_js() over every
	PDF upload -- its own check for embedded JavaScript -- and that parses the
	whole document. A truncated fixture fails there rather than in anything this
	module does.
	"""
	from io import BytesIO

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=72, height=72)
	out = BytesIO()
	writer.write(out)
	return out.getvalue()


def _jpeg(with_gps: bool = False) -> bytes:
	from PIL import Image

	image = Image.new("RGB", (8, 8), (120, 140, 110))
	out = io.BytesIO()
	if with_gps:
		exif = image.getexif()
		gps = exif.get_ifd(scanning.EXIF_GPS_TAG)
		gps[1] = "N"
		gps[2] = (9.0, 10.0, 0.0)
		gps[3] = "E"
		gps[4] = (38.0, 45.0, 0.0)
		image.save(out, format="JPEG", exif=exif)
	else:
		image.save(out, format="JPEG")
	return out.getvalue()


class _Upload:
	"""Stands in for the werkzeug FileStorage the endpoint reads off the request."""

	def __init__(self, filename, content):
		self.filename = filename
		self.stream = io.BytesIO(content)


class _Request:
	"""A minimal stand-in for the werkzeug request.

	`host` is here because saving a File reaches frappe.utils.get_url(), which asks
	the request for its host before resolving a storage path. Left falsy so that
	lookup falls through to the site config, as it does outside a web request.
	"""

	def __init__(self, upload):
		self.files = {"file": upload} if upload else {}
		self.path = "/api/method/test"
		self.host = None


class AttachmentAPITestCase(FrappeTestCase):
	def setUp(self):
		self.grievance = a_grievance()
		self._saved_request = getattr(frappe.local, "request", None)

	def tearDown(self):
		frappe.local.request = self._saved_request
		frappe.db.rollback()

	def _send(self, filename, content, **kwargs):
		frappe.local.request = _Request(_Upload(filename, content))
		return attachment.submit_document(grievance=self.grievance.name, **kwargs)


class TestUploadGate(AttachmentAPITestCase):
	def test_a_valid_image_is_accepted_and_queued(self):
		result = self._send("receipt.jpg", _jpeg())

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["mime_type"], "image/jpeg")
		# Pending, not Clean: nothing has looked at it yet.
		self.assertEqual(result["data"]["scan_status"], "Pending")

	def test_a_pdf_is_accepted(self):
		result = self._send("evidence.pdf", _pdf())
		self.assertEqual(result["data"]["mime_type"], "application/pdf")

	def test_an_executable_renamed_to_jpg_is_refused(self):
		"""The extension says image; the bytes decide, and they say otherwise."""
		result = self._send("holiday.jpg", WINDOWS_EXECUTABLE)
		self.assertEqual(result["status"], "error")

	def test_an_oversized_file_is_refused(self):
		oversized = _jpeg() + b"\x00" * scanning.MAX_SIZE_BYTES
		result = self._send("huge.jpg", oversized)
		self.assertEqual(result["status"], "error")

	def test_an_empty_upload_is_refused(self):
		result = self._send("nothing.jpg", b"")
		self.assertEqual(result["status"], "error")

	def test_a_request_with_no_file_is_refused(self):
		frappe.local.request = _Request(None)
		result = attachment.submit_document(grievance=self.grievance.name)
		self.assertEqual(result["status"], "error")

	def test_an_upload_naming_neither_grievance_nor_draft_is_refused(self):
		frappe.local.request = _Request(_Upload("receipt.jpg", _jpeg()))
		result = attachment.submit_document()
		self.assertEqual(result["status"], "error")


class TestLocationMetadataIsStripped(AttachmentAPITestCase):
	def test_coordinates_do_not_survive_the_upload(self):
		"""FSD 9.2 anonymity would otherwise die in the EXIF block."""
		original = _jpeg(with_gps=True)
		self.assertTrue(scanning.has_location_metadata(original))

		result = self._send("field.jpg", original)
		name = result["data"]["attachment"]
		stored = frappe.get_doc("Grievance Attachment", name)

		file_name = frappe.db.get_value("File", {"file_url": stored.file_url}, "name")
		content = frappe.get_doc("File", file_name).get_content()
		self.assertFalse(scanning.has_location_metadata(content))

	def test_the_checksum_matches_what_was_actually_stored(self):
		"""Taken after the strip, so it describes the bytes on disk, not the upload."""
		result = self._send("field.jpg", _jpeg(with_gps=True))
		stored = frappe.get_doc("Grievance Attachment", result["data"]["attachment"])

		file_name = frappe.db.get_value("File", {"file_url": stored.file_url}, "name")
		content = frappe.get_doc("File", file_name).get_content()
		self.assertEqual(stored.checksum_sha256, scanning.sha256_of(content))


class TestDownloadIsGatedOnTheScan(AttachmentAPITestCase):
	def _uploaded(self):
		result = self._send("receipt.jpg", _jpeg())
		return result["data"]["attachment"]

	def test_a_pending_file_cannot_be_downloaded(self):
		result = attachment.download(attachment=self._uploaded())
		self.assertEqual(result["status"], "error")

	def test_a_clean_file_can_be(self):
		name = self._uploaded()
		frappe.db.set_value("Grievance Attachment", name, "scan_status", "Clean")

		result = attachment.download(attachment=name)
		self.assertEqual(result["status"], "success")
		self.assertTrue(result["data"]["file_url"])

	def test_an_infected_file_stays_withheld(self):
		name = self._uploaded()
		frappe.db.set_value("Grievance Attachment", name, "scan_status", "Infected")

		result = attachment.download(attachment=name)
		self.assertEqual(result["status"], "error")

	def test_a_failed_scan_withholds_too(self):
		"""Fail closed: an outage makes a file unavailable, not trusted."""
		name = self._uploaded()
		frappe.db.set_value("Grievance Attachment", name, "scan_status", "Failed")

		result = attachment.download(attachment=name)
		self.assertEqual(result["status"], "error")


class TestListing(AttachmentAPITestCase):
	def test_every_attachment_is_listed_with_its_verdict(self):
		self._send("one.jpg", _jpeg())
		self._send("two.pdf", _pdf())

		result = attachment.get_attachments(grievance=self.grievance.name)
		rows = result["data"]

		self.assertEqual(len(rows), 2)
		self.assertEqual({r["file_name"] for r in rows}, {"one.jpg", "two.pdf"})
		self.assertTrue(all(r["servable"] is False for r in rows))

	def test_an_infected_file_is_still_listed(self):
		"""An officer needs to know something was submitted and what became of it."""
		name = self._send("bad.jpg", _jpeg())["data"]["attachment"]
		frappe.db.set_value("Grievance Attachment", name, "scan_status", "Infected")

		rows = attachment.get_attachments(grievance=self.grievance.name)["data"]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["scan_status"], "Infected")
		self.assertFalse(rows[0]["servable"])


class TestLimits(AttachmentAPITestCase):
	def test_a_case_cannot_carry_more_than_the_cap(self):
		for i in range(attachment.MAX_ATTACHMENTS_PER_CASE):
			self._send(f"file{i}.jpg", _jpeg())

		result = self._send("one-too-many.jpg", _jpeg())
		self.assertEqual(result["status"], "error")


class TestDeletion(AttachmentAPITestCase):
	def test_an_attachment_can_be_removed_while_the_case_is_open(self):
		name = self._send("mistake.jpg", _jpeg())["data"]["attachment"]

		result = attachment.delete(attachment=name)
		self.assertEqual(result["status"], "success")
		self.assertFalse(frappe.db.exists("Grievance Attachment", name))

	def test_evidence_cannot_be_removed_once_the_case_is_closed(self):
		"""It is part of what the decision rested on.

		handle_api_errors rolls the request back before returning the envelope, so
		the assertion is on the refusal rather than on the row outliving it.
		"""
		name = self._send("evidence.jpg", _jpeg())["data"]["attachment"]
		frappe.db.set_value("Grievance", self.grievance.name, "status", "Closed")

		result = attachment.delete(attachment=name)
		self.assertEqual(result["status"], "error")
		self.assertIn("Closed", result["message"])
