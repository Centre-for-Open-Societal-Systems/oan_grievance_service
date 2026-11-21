# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GrievanceSLARule(Document):
	"""One row of a service category's SLA (STG-514). See `services/classification.py`.

	The rules are the one source of SLA days: the category's base rule (no type, area or window)
	is its SLA, and narrower rules override it where they match.
	"""

	def validate(self):
		"""A real window (or all year), for a type of the rule's category, that ties with no other
		rule, in a category that keeps its base rule.

		Two rules that would tie for the same case are refused (see
		`classification.overlapping_rules`): which one won would depend on the order they were made in.
		The category row is locked first, so two rules saved at once cannot both pass the checks.
		"""
		from oan_grievance_service.services import classification

		error = classification.window_error(self)
		if error:
			frappe.throw(error)
		if (self.sla_days or 0) <= 0:
			frappe.throw(_("SLA Days must be greater than zero."))
		if self.grievance_type:
			category = frappe.db.get_value("Grievance Type", self.grievance_type, "service_category")
			if category != self.service_category:
				frappe.throw(
					_("{0} belongs to {1}, not {2}.").format(
						self.grievance_type, category, self.service_category
					)
				)

		self.is_base = 1 if classification.is_base(self) else 0
		# Priority has one value and follows Urgent: it is never set on its own.
		self.priority = classification.priority_of(self.is_urgent)
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
		if self.is_base:
			return
		if not self.is_new() and self.get_doc_before_save() and self.get_doc_before_save().is_base:
			frappe.throw(
				_(
					"This is the category's base rule, its SLA wherever no narrower rule matches. It "
					"cannot name a type, an area or a window: add a new rule instead."
				)
			)
		if not any(other.is_base for other in others):
			frappe.throw(
				_(
					"{0} has no base rule. Add the rule with no type, area or window first: it is the "
					"category's SLA wherever no narrower rule matches."
				).format(self.service_category)
			)

	def on_trash(self):
		"""The base rule is the category's SLA, so it stays while the category has one."""
		if self.is_base and not self.flags.removing_category_sla:
			frappe.throw(
				_(
					"The base rule is the category's SLA and cannot be deleted. Change its SLA Days "
					"instead."
				)
			)
