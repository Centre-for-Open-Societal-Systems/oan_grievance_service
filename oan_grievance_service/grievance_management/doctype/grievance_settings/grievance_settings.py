# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Installation-wide grievance settings, as a Single."""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

DEFAULT_NATIONAL_ID_PATTERN = r"^\d{12}(\d{4})?$"
DEFAULT_NATIONAL_ID_FORMAT_HINT = "a 12- or 16-digit number"


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

		pattern = (self.national_id_pattern or "").strip()
		if pattern:
			try:
				re.compile(pattern)
			except re.error as exc:
				frappe.throw(
					_("National ID Pattern is not a valid regular expression: {0}").format(exc),
					frappe.ValidationError,
				)


def auto_routing_enabled() -> bool:
	"""Whether cases go to the matching desk's officer; off sends every case to Unrouted."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_routing_enabled)


def auto_escalation_enabled() -> bool:
	"""Site-wide switch for escalating overdue cases; a desk can still opt out for the cases it routes."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_escalation_enabled)


def change_request_hours() -> int:
	"""Hours a change request waits with an approver whose rung sets no hours of its own."""
	return cint(frappe.get_cached_doc("Grievance Settings").change_request_hours)


def national_id_pattern() -> re.Pattern[str]:
	"""Compiled regex that a national / Fayda ID must match, from Grievance Settings."""
	raw = (frappe.get_cached_doc("Grievance Settings").get("national_id_pattern") or "").strip()
	return re.compile(raw or DEFAULT_NATIONAL_ID_PATTERN)


def national_id_format_hint() -> str:
	"""Human-readable format description for national / Fayda ID validation errors."""
	hint = (frappe.get_cached_doc("Grievance Settings").get("national_id_format_hint") or "").strip()
	return hint or DEFAULT_NATIONAL_ID_FORMAT_HINT
