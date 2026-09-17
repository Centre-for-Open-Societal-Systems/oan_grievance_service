# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class GrievanceTimeline(Document):
	def validate(self):
		if not self.created_on:
			self.created_on = now_datetime()

		if self.author_submitter and self.is_internal:
			frappe.throw(
				_("Submitters cannot create internal entries."),
				frappe.ValidationError,
				title=_("Invalid Permission"),
			)

	def before_save(self):
		if not self.is_new():
			frappe.throw(
				_("Timeline entries are append-only and immutable."),
				frappe.ValidationError,
				title=_("Immutable Record"),
			)

	def on_trash(self):
		if not frappe.flags.in_test and not frappe.flags.in_uninstall:
			frappe.throw(
				_("Timeline entries cannot be deleted."),
				frappe.ValidationError,
				title=_("Immutable Record"),
			)

	@staticmethod
	def record(
		grievance,
		entry_type: str,
		body: str,
		is_internal: bool | int = 0,
		author_user: str | None = None,
		author_submitter: str | None = None,
		ref_doctype: str | None = None,
		ref_docname: str | None = None,
		created_on=None,
	):
		"""Insert an append-only timeline entry for a grievance. Returns the timeline doc."""
		grievance_name = getattr(grievance, "name", grievance)

		return frappe.get_doc(
			{
				"doctype": "Grievance Timeline",
				"grievance": grievance_name,
				"entry_type": entry_type,
				"is_internal": 1 if is_internal else 0,
				"body": body,
				"author_user": author_user,
				"author_submitter": author_submitter,
				"ref_doctype": ref_doctype,
				"ref_docname": ref_docname,
				"created_on": created_on or now_datetime(),
			}
		).insert(ignore_permissions=True)
