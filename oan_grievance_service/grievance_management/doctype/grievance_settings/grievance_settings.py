# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Installation-wide grievance settings, as a Single."""

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


def auto_routing_enabled() -> bool:
	"""Whether cases go to the matching desk's officer; off sends every case to Unrouted."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_routing_enabled)


def auto_escalation_enabled() -> bool:
	"""Site-wide switch for escalating overdue cases; a category can still opt out on its SLA."""
	return bool(frappe.get_cached_doc("Grievance Settings").auto_escalation_enabled)


def change_request_hours() -> int:
	"""Hours a change request waits with an approver whose rung sets no hours of its own."""
	return cint(frappe.get_cached_doc("Grievance Settings").change_request_hours)
