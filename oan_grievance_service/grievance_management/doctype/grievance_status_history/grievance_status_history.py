# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from oan_grievance_service.services.audit import ImmutableRecord


class GrievanceStatusHistory(ImmutableRecord, Document):
	"""Grievance Status History: append-only. See ImmutableRecord for why the guard is here and not
	only in the permission flags."""

	def validate(self):
		if not self.timestamp:
			self.timestamp = now_datetime()

		if not self.prev_hash or not self.row_hash:
			self.compute_hash_chain()

	def compute_hash_chain(self):
		"""Calculate cryptographic SHA-256 hash chaining for tamper evidence."""
		prev_row = frappe.get_all(
			"Grievance Status History",
			filters={"grievance": self.grievance},
			fields=["name", "row_hash"],
			order_by="timestamp desc, creation desc",
			limit=1,
		)
		self.prev_hash = prev_row[0].row_hash if prev_row and prev_row[0].row_hash else "0" * 64

		payload = (
			f"{self.prev_hash}|"
			f"{self.grievance}|"
			f"{self.from_status or ''}|"
			f"{self.to_status}|"
			f"{self.timestamp}|"
			f"{self.changed_by or ''}|"
			f"{self.reason or ''}|"
			f"{self.closure_type or ''}|"
			f"{1 if self.is_automated else 0}"
		)
		self.row_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

