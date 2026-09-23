# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from oan_grievance_service.services.audit import ImmutableRecord


class GrievanceStatusHistory(ImmutableRecord, Document):
	"""Grievance Status History: append-only. See ImmutableRecord for why the guard is here and not
	only in the permission flags."""

	def validate(self):
		if not self.timestamp:
			self.timestamp = now_datetime()

		require_reason(self.from_status, self.to_status, self.reason)

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


def require_reason(from_status, to_status, reason):
	"""FSD 3.4 rejections and 3.6 reopens must be justified.

	The rule lives here, with the row that records the move, and nowhere else.
	Every status change writes one of these rows inside the same transaction as
	the move, so a missing reason rolls the move back whether it came from the
	desk, the API or a scheduled job. The Grievance controller also asks this
	before it writes the move, so the refusal arrives before the row does.
	"""
	is_reason_required = (to_status == "Rejected") or (
		from_status == "Pending Submitter" and to_status == "In Progress"
	)
	if not is_reason_required:
		return
	if reason and reason.strip():
		return
	frappe.throw(
		_("A reason is required to move a grievance from {0} to {1}.").format(
			frappe.bold(from_status), frappe.bold(to_status)
		),
		title=_("Reason Required"),
	)
