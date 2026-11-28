# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from oan_grievance_service.services import storage


class _FakeS3Client:
	def __init__(self):
		self.objects = {}

	def put_object(self, Bucket, Key, Body, ContentType):
		self.objects[(Bucket, Key)] = (Body, ContentType)

	def get_object(self, Bucket, Key):
		body, _ = self.objects[(Bucket, Key)]
		return {"Body": BytesIO(body)}

	def delete_object(self, Bucket, Key):
		self.objects.pop((Bucket, Key), None)


class TestStorage(IntegrationTestCase):
	def test_parse_s3_uri(self):
		bucket, key = storage.parse_s3_uri("s3://grievance-docs/prefix/CASE-1/file.wav")
		self.assertEqual(bucket, "grievance-docs")
		self.assertEqual(key, "prefix/CASE-1/file.wav")

		with self.assertRaises(ValueError):
			storage.parse_s3_uri("/private/files/file.wav")

	def test_s3_roundtrip_store_read_delete(self):
		fake_client = _FakeS3Client()
		fake_settings = SimpleNamespace(
			use_external_storage=1,
			s3_bucket="grievance-docs",
			s3_key_prefix="grievance-attachments",
		)

		with (
			patch.object(storage, "_get_settings", return_value=fake_settings),
			patch.object(storage, "_s3_client", return_value=fake_client),
		):
			stored = storage.store_attachment_bytes("GRV-0001", "note.wav", b"RIFFdata", "audio/x-wav")
			self.assertIsNone(stored.file)
			self.assertTrue(stored.file_url.startswith("s3://grievance-docs/grievance-attachments/GRV-0001/"))
			self.assertEqual(storage.read_attachment_bytes(stored.file_url), b"RIFFdata")

			storage.delete_stored_object(file_url=stored.file_url)
			self.assertIsNone(storage.read_attachment_bytes(stored.file_url))
