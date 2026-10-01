# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GrievanceSLAConfiguration(Document):
	def validate(self):
		self.validate_one_active_row()
		if self.sla_days is not None and self.sla_days <= 0:
			frappe.throw(_("SLA Days must be greater than zero."))
		if self.first_response_hours is not None and self.first_response_hours < 0:
			frappe.throw(_("First Response Hours cannot be negative."))
		if self.update_cadence_hours is not None and self.update_cadence_hours < 0:
			frappe.throw(_("Update Cadence Hours cannot be negative."))
		if self.remand_execution_hours is not None and self.remand_execution_hours < 0:
			frappe.throw(_("Remand Execution Hours cannot be negative."))
		if self.appeal_window_days is not None and self.appeal_window_days < 0:
			frappe.throw(_("Appeal Window Days cannot be negative."))

	def validate_one_active_row(self):
		"""One active policy per category, so the row the API edits is the row routing enforces.

		The category row is locked first so two concurrent saves cannot both pass the check.
		"""
		if not self.active or not self.service_category:
			return
		frappe.db.get_value("Grievance Service Category", self.service_category, "name", for_update=True)
		filters = {"service_category": self.service_category, "active": 1}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.get_value("Grievance SLA Configuration", filters, "name", for_update=True)
		if existing:
			frappe.throw(
				_("An active SLA configuration already exists for {0} ({1}).").format(
					self.service_category, existing
				),
				frappe.DuplicateEntryError,
			)
