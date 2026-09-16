# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, now_datetime

MAX_PAYLOAD_BYTES = 256 * 1024


class GrievanceDraft(Document):
	def validate(self):
		self.set_expiry()
		self.validate_payload_size()

	def set_expiry(self):
		if not self.expires_on:
			self.expires_on = add_days(now_datetime(), 30)

	def validate_payload_size(self):
		"""A draft holds typed answers, not files.

		Attachments are File records parented to the draft, so a payload this large
		is a client serialising something it should have uploaded instead.
		"""
		size = len((self.payload or "").encode("utf-8"))
		if size > MAX_PAYLOAD_BYTES:
			frappe.throw(
				_(
					"Draft payload is {0} KB; the limit is {1} KB. Attach files instead of embedding them."
				).format(size // 1024, MAX_PAYLOAD_BYTES // 1024),
				title=_("Draft Too Large"),
			)
