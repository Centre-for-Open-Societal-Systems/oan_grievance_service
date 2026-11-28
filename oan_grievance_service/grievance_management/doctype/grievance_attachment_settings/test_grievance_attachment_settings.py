# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestGrievanceAttachmentSettings(IntegrationTestCase):
	def tearDown(self):
		settings = frappe.get_doc("Grievance Attachment Settings")
		settings.use_external_storage = 0
		settings.save(ignore_permissions=True)

	def test_file_system_storage_requires_no_s3_fields(self):
		settings = frappe.get_doc("Grievance Attachment Settings")
		settings.use_external_storage = 0
		settings.s3_endpoint = ""
		settings.s3_bucket = ""
		settings.save(ignore_permissions=True)
		self.assertFalse(settings.use_external_storage)

	def test_s3_storage_requires_endpoint_bucket_and_credentials(self):
		settings = frappe.get_doc("Grievance Attachment Settings")
		settings.use_external_storage = 1
		settings.s3_endpoint = ""
		settings.s3_bucket = "grievance-docs"
		settings.s3_region = "us-east-1"
		settings.s3_access_key = "GK123"
		settings.s3_secret_key = "secret"
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_s3_endpoint_must_be_http_url(self):
		settings = frappe.get_doc("Grievance Attachment Settings")
		settings.use_external_storage = 1
		settings.s3_endpoint = "s3:9000"
		settings.s3_bucket = "grievance-docs"
		settings.s3_region = "us-east-1"
		settings.s3_access_key = "GK123"
		settings.s3_secret_key = "secret"
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_valid_s3_settings_save(self):
		settings = frappe.get_doc("Grievance Attachment Settings")
		settings.use_external_storage = 1
		settings.s3_endpoint = "http://s3:9000"
		settings.s3_bucket = "grievance-docs"
		settings.s3_region = "us-east-1"
		settings.s3_access_key = "GK123"
		settings.s3_secret_key = "secret"
		settings.save(ignore_permissions=True)
		self.assertTrue(settings.use_external_storage)
