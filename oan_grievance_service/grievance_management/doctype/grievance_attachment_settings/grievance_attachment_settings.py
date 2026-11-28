# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Storage configuration for grievance attachments, as a Single."""

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class GrievanceAttachmentSettings(Document):
	"""Validates the S3 settings when external storage is switched on."""

	def validate(self):
		if not cint(self.use_external_storage):
			return

		required = {
			"s3_endpoint": _("Endpoint URL"),
			"s3_bucket": _("Bucket"),
			"s3_region": _("Region"),
			"s3_access_key": _("Access Key ID"),
		}
		for field, label in required.items():
			if not (self.get(field) or "").strip():
				frappe.throw(_("{0} is required for external storage.").format(label), frappe.ValidationError)

		secret = self.get_password("s3_secret_key", raise_exception=False)
		if not secret:
			frappe.throw(_("Secret Access Key is required for external storage."), frappe.ValidationError)

		parsed = urlparse((self.s3_endpoint or "").strip())
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			frappe.throw(
				_("Endpoint URL must be a valid http:// or https:// URL."),
				frappe.ValidationError,
			)
