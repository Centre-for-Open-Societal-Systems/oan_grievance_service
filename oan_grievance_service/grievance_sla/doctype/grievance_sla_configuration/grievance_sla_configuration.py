# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GrievanceSLAConfiguration(Document):
	def validate(self):
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
