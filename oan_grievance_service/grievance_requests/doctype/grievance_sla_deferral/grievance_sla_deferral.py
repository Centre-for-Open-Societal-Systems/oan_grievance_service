# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""FSD 3.11.7: a deferral pushes a grievance's SLA deadline out.

Everything a deferral does lives here, so the API, a background job and the desk form
all behave the same way: saving an Approved deferral is what moves the clock.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, get_datetime, now_datetime

DECIDED = ("Approved", "Rejected")


class GrievanceSLADeferral(Document):
	def validate(self):
		self.validate_days()
		self.validate_decision_is_final()
		if self.status == "Approved" and self.has_value_changed("status"):
			self.validate_approver()

	def on_update(self):
		if self.status == "Approved" and self.has_value_changed("status"):
			self.extend_grievance_sla()

	def validate_days(self):
		from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
			max_deferral_days,
		)

		if (self.additional_days or 0) <= 0:
			frappe.throw(_("A deferral must add at least one day."), title=_("Invalid Deferral"))
		max_days = max_deferral_days()
		if self.additional_days > max_days:
			frappe.throw(
				_("A deferral may not exceed {0} days.").format(max_days),
				title=_("Deferral Too Long"),
			)

	def validate_decision_is_final(self):
		"""A decided deferral is an audit record. Editing it would move the clock twice."""
		before = self.get_doc_before_save()
		if before and before.status in DECIDED:
			frappe.throw(
				_("This deferral has already been {0} and cannot be changed.").format(_(before.status)),
				title=_("Deferral Decided"),
			)

	def validate_approver(self):
		from oan_grievance_service.permissions import can_approve_deferral

		grievance = frappe.get_doc("Grievance", self.grievance)
		user = frappe.session.user
		if not can_approve_deferral(user=user, assignee=grievance.assigned_to, grievance=grievance):
			frappe.throw(
				_("Only a supervising officer may decide a deferral."),
				frappe.PermissionError,
				title=_("Approval Not Permitted"),
			)
		self.approver = user
		self.decided_at = self.decided_at or now_datetime()

	def extend_grievance_sla(self):
		"""Push the deadline out and reopen the reminders against it.

		A case already climbing keeps its rung's remaining hours, shifted by the same
		days; one that has not escalated yet is re-armed against the new deadline.
		"""
		from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
			GrievanceTimeline,
		)
		from oan_grievance_service.services import sla

		grievance = frappe.get_doc("Grievance", self.grievance)
		if grievance.sla_due_date:
			days = self.additional_days
			updates = {
				"sla_due_date": add_days(get_datetime(grievance.sla_due_date), days),
				"reminder_50_sent": 0,
				"reminder_80_sent": 0,
			}
			if grievance.escalated and grievance.next_escalation_at:
				updates["next_escalation_at"] = add_days(get_datetime(grievance.next_escalation_at), days)
			grievance.db_set(updates, update_modified=False)
			if "next_escalation_at" not in updates:
				sla.arm_escalation(grievance, sla.resolve_policy(grievance.service_category))

		# Callers that answer with the timeline event (the API) read it off the flags.
		self.flags.timeline_entry = GrievanceTimeline.record(
			grievance=grievance.name,
			entry_type="note",
			is_internal=True,
			body=f"SLA deadline extended by {self.additional_days} days. Reason: {self.justification}",
			author_user=self.approver,
			ref_doctype=self.doctype,
			ref_docname=self.name,
		)
