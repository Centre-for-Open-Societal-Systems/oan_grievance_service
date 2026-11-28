# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Installation-wide grievance settings, as a Single."""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class GrievanceSettings(Document):
	def validate(self):
		from oan_grievance_service.services import duplicates

		if cint(self.change_request_hours) < 1:
			frappe.throw(_("Change Request Wait (Hours) must be at least 1."), frappe.ValidationError)

		duplicates.validate_tuning(
			flt(self.duplicate_weight_category),
			flt(self.duplicate_weight_type),
			flt(self.duplicate_threshold),
			flt(self.duplicate_high_threshold),
		)

		try:
			re.compile(self.national_id_pattern)
		except re.error as exc:
			frappe.throw(
				_("National ID Pattern is not a valid regular expression: {0}").format(str(exc)),
				frappe.ValidationError,
			)

	def on_update(self):
		if cint(self.mds_geo_sync_enabled) and (self.mds_base_url or "").strip():
			from oan_grievance_service.services import mds_geo_sync

			mds_geo_sync.enqueue_sync(after_commit=True)


def auto_routing_enabled() -> bool:
	"""Whether cases go to the matching desk's officer; off sends every case to Unrouted."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_routing_enabled)


def auto_escalation_enabled() -> bool:
	"""Site-wide switch for escalating overdue cases; a desk can still opt out for the cases it routes."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_escalation_enabled)


def reopen_escalation_enabled() -> bool:
	"""Site-wide switch for sending a reopened case one level above its last resolver (STG-521).

	Read raw: a site that saved its settings before this field existed has no value stored for it,
	and that means the field's default, on. A loaded Check would read it as 0.
	"""
	value = frappe.db.get_singles_dict("Grievance Settings").get("reopen_escalation_enabled")
	return True if value is None else bool(cint(value))


def change_request_hours() -> int:
	"""Hours a change request waits with an approver whose rung sets no hours of its own."""
	return cint(frappe.get_cached_doc("Grievance Settings").change_request_hours)
