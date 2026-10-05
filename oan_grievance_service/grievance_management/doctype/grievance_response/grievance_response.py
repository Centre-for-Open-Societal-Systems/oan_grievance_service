# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

# The action taken is capped at 500 characters.
ACTION_TAKEN_LIMIT = 500


class GrievanceResponse(Document):
	def before_validate(self):
		"""These fields are system-filled: the system fills them in
		and the officer cannot edit them. They are set before validation so the
		mandatory check sees a value rather than rejecting the officer's own save."""
		if not self.response_date:
			self.response_date = now_datetime()
		if not self.responded_by:
			self.responded_by = frappe.session.user
		if not self.response_sequence_no:
			self.response_sequence_no = (
				frappe.db.count("Grievance Response", {"grievance": self.grievance}) + 1
			)
		if not self.prior_status and self.grievance:
			self.prior_status = frappe.db.get_value("Grievance", self.grievance, "status")

	def validate(self):
		self.validate_action_taken_length()
		self.validate_referral_target()

	def validate_action_taken_length(self):
		"""Action taken has a 500-character limit."""
		if self.action_taken and len(self.action_taken) > ACTION_TAKEN_LIMIT:
			frappe.throw(
				_("Action taken must be {0} characters or fewer (currently {1}).").format(
					ACTION_TAKEN_LIMIT, len(self.action_taken)
				),
				title=_("Action Taken Too Long"),
			)

	def validate_referral_target(self):
		"""A referral has to say where the case is going."""
		requires_dept = False
		if self.response_type and frappe.db.exists("Grievance Response Type", self.response_type):
			requires_dept = bool(
				frappe.db.get_value("Grievance Response Type", self.response_type, "requires_referred_dept")
			)
		else:
			requires_dept = self.response_type == "Referred to another dept"

		if requires_dept and not self.get("referred_to_department"):
			# The field is optional in the scaffold; warn rather than block until the
			# referral target column is confirmed with the user.
			frappe.msgprint(
				_("This response refers the case onward but names no target department."),
				indicator="orange",
				alert=True,
			)

	@property
	def sla_behaviour(self):
		"""What the resulting grievance status does to the SLA clock ('running', 'paused', 'stopped')."""
		if not self.new_status:
			return None
		from oan_grievance_service.services import sla

		return (sla.sla_category_of(self.new_status) or "").lower()
