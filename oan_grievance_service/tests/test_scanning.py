# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import io

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.services import scanning

# Minimal real headers. The point of these tests is that the bytes decide, so
# the bytes have to be genuine even when the file is tiny.
JPEG_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
PDF_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
WINDOWS_EXECUTABLE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"


def _real_jpeg(with_gps: bool = False) -> bytes:
	from PIL import Image

	image = Image.new("RGB", (4, 4), (120, 140, 110))
	out = io.BytesIO()
	if with_gps:
		exif = image.getexif()
		# 34853 is the GPS block; a phone writes this without being asked. These
		# are real coordinates in Oromia.
		gps = exif.get_ifd(scanning.EXIF_GPS_TAG)
		gps[1] = "N"
		gps[2] = (9.0, 10.0, 0.0)
		gps[3] = "E"
		gps[4] = (38.0, 45.0, 0.0)
		image.save(out, format="JPEG", exif=exif)
	else:
		image.save(out, format="JPEG")
	return out.getvalue()


class TestTypeSniffing(FrappeTestCase):
	"""The extension is a claim; the leading bytes are the evidence."""

	def test_a_real_jpeg_is_recognised(self):
		self.assertEqual(scanning.sniff_mime(JPEG_HEADER), "image/jpeg")

	def test_png_and_pdf_are_recognised(self):
		self.assertEqual(scanning.sniff_mime(PNG_HEADER), "image/png")
		self.assertEqual(scanning.sniff_mime(PDF_HEADER), "application/pdf")

	def test_an_executable_is_recognised_whatever_it_is_called(self):
		self.assertEqual(scanning.sniff_mime(WINDOWS_EXECUTABLE), "application/x-msdownload")

	def test_an_executable_renamed_to_jpg_is_refused(self):
		"""Frappe's own content_type comes from the filename and would say image/jpeg."""
		with self.assertRaises(frappe.ValidationError):
			scanning.validate_upload("holiday.jpg", WINDOWS_EXECUTABLE)

	def test_a_pdf_renamed_to_jpg_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			scanning.validate_upload("scan.jpg", PDF_HEADER)

	def test_a_matching_file_passes_and_returns_its_type(self):
		validated = scanning.validate_upload("receipt.jpg", JPEG_HEADER)
		self.assertEqual(validated.mime_type, "image/jpeg")
		self.assertEqual(validated.file_name, "receipt.jpg")
		self.assertEqual(validated.size_bytes, len(JPEG_HEADER))

	def test_a_file_over_the_limit_is_refused(self):
		oversized = JPEG_HEADER + b"\x00" * scanning.MAX_SIZE_BYTES
		with self.assertRaises(frappe.ValidationError):
			scanning.validate_upload("huge.jpg", oversized)

	def test_unrecognised_content_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			scanning.validate_upload("notes.jpg", b"just some text, no magic number")


class TestLocationMetadata(FrappeTestCase):
	"""FSD 9.2 anonymity survives the database and then dies in the EXIF block."""

	def test_a_phone_photo_carries_coordinates(self):
		self.assertTrue(scanning.has_location_metadata(_real_jpeg(with_gps=True)))

	def test_stripping_removes_them(self):
		stripped = scanning.strip_location_metadata(_real_jpeg(with_gps=True), "image/jpeg")
		self.assertFalse(scanning.has_location_metadata(stripped))

	def test_the_image_still_opens_afterwards(self):
		stripped = scanning.strip_location_metadata(_real_jpeg(with_gps=True), "image/jpeg")
		self.assertEqual(scanning.sniff_mime(stripped), "image/jpeg")

	def test_non_images_pass_through_untouched(self):
		self.assertEqual(scanning.strip_location_metadata(PDF_HEADER, "application/pdf"), PDF_HEADER)


class TestScannerFailsClosed(FrappeTestCase):
	"""An outage makes attachments unavailable; it must not make them trusted."""

	def test_no_scanner_configured_yields_failed_not_clean(self):
		status, detail = scanning.scan_bytes(JPEG_HEADER)
		self.assertEqual(status, scanning.SCAN_FAILED)
		self.assertIn("No scanner configured", detail)

	def test_an_unreachable_scanner_yields_failed_not_clean(self):
		frappe.conf["grievance_clamav_host"] = "127.0.0.1"
		frappe.conf["grievance_clamav_port"] = 1  # nothing listens here
		try:
			status, _ = scanning.scan_bytes(JPEG_HEADER)
			self.assertEqual(status, scanning.SCAN_FAILED)
		finally:
			frappe.conf.pop("grievance_clamav_host", None)
			frappe.conf.pop("grievance_clamav_port", None)
