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


class _FakeClamd:
	"""Stands in for socket.create_connection and answers with a canned reply."""

	def __init__(self, reply: bytes):
		self.reply = reply
		self.sent = b""

	def __call__(self, *args, **kwargs):
		return self

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def sendall(self, data: bytes):
		self.sent += data

	def recv(self, size: int) -> bytes:
		return self.reply


class TestScannerReplies(FrappeTestCase):
	"""clamd's wire replies are parsed as clamd actually sends them.

	The z-prefixed INSTREAM command asks for NUL-terminated replies, so a clean
	verdict arrives as b"stream: OK\\0". Found against a live clamd 1.4: the NUL
	survived str.strip() and every clean file was marked Failed.
	"""

	def setUp(self):
		frappe.conf["grievance_clamav_host"] = "fake-clamd"
		self._real_connect = scanning.socket.create_connection

	def tearDown(self):
		scanning.socket.create_connection = self._real_connect
		frappe.conf.pop("grievance_clamav_host", None)

	def _scan_with_reply(self, reply: bytes):
		fake = _FakeClamd(reply)
		scanning.socket.create_connection = fake
		return scanning.scan_bytes(JPEG_HEADER), fake

	def test_a_nul_terminated_ok_is_clean(self):
		(status, detail), _ = self._scan_with_reply(b"stream: OK\x00")
		self.assertEqual(status, scanning.SCAN_CLEAN)
		self.assertEqual(detail, "stream: OK")

	def test_a_newline_terminated_ok_is_clean_too(self):
		(status, _), _ = self._scan_with_reply(b"stream: OK\n")
		self.assertEqual(status, scanning.SCAN_CLEAN)

	def test_a_found_verdict_is_infected(self):
		(status, detail), _ = self._scan_with_reply(b"stream: Eicar-Test-Signature FOUND\x00")
		self.assertEqual(status, scanning.SCAN_INFECTED)
		self.assertIn("Eicar-Test-Signature", detail)

	def test_an_error_reply_is_failed_not_clean(self):
		(status, _), _ = self._scan_with_reply(b"INSTREAM size limit exceeded. ERROR\x00")
		self.assertEqual(status, scanning.SCAN_FAILED)

	def test_an_empty_reply_is_failed_not_clean(self):
		(status, detail), _ = self._scan_with_reply(b"")
		self.assertEqual(status, scanning.SCAN_FAILED)
		self.assertIn("nothing", detail)

	def test_the_stream_is_sent_in_clamd_wire_format(self):
		_, fake = self._scan_with_reply(b"stream: OK\x00")
		self.assertTrue(fake.sent.startswith(b"zINSTREAM\x00"))
		self.assertTrue(fake.sent.endswith(b"\x00\x00\x00\x00"))

	def test_text_decoded_content_is_still_sent_as_bytes(self):
		"""Core's File.get_content() hands back str for anything valid as UTF-8.

		An all-ASCII upload (the EICAR test file is one) reached the socket as
		str, the send raised, and the row sat in Pending forever. The bytes must
		go out whatever type the object came back as.
		"""
		fake = _FakeClamd(b"stream: Eicar-Test-Signature FOUND\x00")
		scanning.socket.create_connection = fake
		status, _ = scanning.scan_bytes("ID3 plain text payload")
		self.assertEqual(status, scanning.SCAN_INFECTED)
		self.assertIn(b"ID3 plain text payload", fake.sent)


def _real_pdf() -> bytes:
	from PIL import Image

	out = io.BytesIO()
	Image.new("RGB", (4, 4), (255, 255, 255)).save(out, format="PDF")
	return out.getvalue()


class TestCorruptPdf(FrappeTestCase):
	"""A PDF the reader cannot open is refused with a reason, not a 500.

	Core's File.check_content runs pypdf over every PDF to look for embedded
	JavaScript and lets the parse error escape. Found with a %PDF header and no
	cross-reference table: the upload came back as INTERNAL_ERROR.
	"""

	def test_a_readable_pdf_passes(self):
		self.assertEqual(scanning.validate_upload("evidence.pdf", _real_pdf()).mime_type, "application/pdf")

	def test_a_header_only_pdf_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			scanning.validate_upload("evidence.pdf", PDF_HEADER + b"no body, no xref\n")
		self.assertIn("not a readable PDF", str(caught.exception))

	def test_the_check_only_runs_for_pdfs(self):
		# A JPEG never goes near the PDF reader.
		self.assertEqual(scanning.validate_upload("photo.jpg", _real_jpeg()).mime_type, "image/jpeg")
