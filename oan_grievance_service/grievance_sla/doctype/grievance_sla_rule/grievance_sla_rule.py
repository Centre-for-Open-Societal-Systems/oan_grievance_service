# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GrievanceSLARule(Document):
	"""A seasonal or urgent rule of a service category (STG-514). See `services/classification.py`."""

	def validate(self):
		"""A real window (or all year), for a type of the rule's category, that ties with no other rule.

		Two rules that would tie for the same case are refused (see
		`classification.overlapping_rules`): which one won would depend on the order they were made in.
		The category row is locked first, so two rules saved at once cannot both pass the check.
		"""
		from oan_grievance_service.services import classification

		error = classification.window_error(self)
		if error:
			frappe.throw(error)
		if (self.sla_days or 0) < 0:
			frappe.throw(_("SLA Days cannot be negative."))
		if self.grievance_type:
			category = frappe.db.get_value("Grievance Type", self.grievance_type, "service_category")
			if category != self.service_category:
				frappe.throw(
					_("{0} belongs to {1}, not {2}.").format(
						self.grievance_type, category, self.service_category
					)
				)

		frappe.db.get_value("Grievance Service Category", self.service_category, "name", for_update=True)
		others = classification.rules_of(self.service_category, exclude=None if self.is_new() else self.name)
		for other in others:
			if classification.overlapping_rules([other, self]):
				frappe.throw(
					_(
						"Rule {0} is for the same type and area and shares days of the year with this "
						"one, so a case could match both."
					).format(other.rule_label or other.name)
				)
