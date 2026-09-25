# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from oan_grievance_service.permissions import can_approve_reassignment

# What was asked for is fixed once the request exists; only the ruling moves.
_FROZEN_FIELDS = (
	"grievance",
	"initiated_by",
	"requested_at",
	"prior_department",
	"prior_officer",
	"target_department",
	"target_officer",
	"reason",
	"sla_treatment",
)


class GrievanceReassignmentRequest(Document):
	"""FSD 3.3.1: a reassignment is a request a supervising officer rules on.

	The rules live here rather than in the endpoint because Frappe also reaches this
	doctype through /api/resource and frappe.client, and the grievance's assignment
	is only ever written by an approved request (see Grievance.guard_assignment).
	"""

	def before_insert(self):
		# Who asked, when, and what the case looked like are the server's to record.
		grievance = frappe.db.get_value(
			"Grievance", self.grievance, ["assigned_dept", "assigned_to"], as_dict=True
		)
		if not grievance:
			frappe.throw(_("Grievance {0} does not exist.").format(self.grievance), frappe.DoesNotExistError)
		self.initiated_by = frappe.session.user
		self.requested_at = now_datetime()
		self.prior_department = grievance.assigned_dept
		self.prior_officer = grievance.assigned_to
		self.decision = self.decision or "Pending"

	def validate(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if before:
			for field in _FROZEN_FIELDS:
				if self.has_value_changed(field):
					frappe.throw(
						_("{0} cannot change once a reassignment is requested.").format(
							self.meta.get_label(field)
						),
						title=_("Request Is Fixed"),
					)
			if before.decision != "Pending" and self.has_value_changed("decision"):
				frappe.throw(_("This reassignment has already been decided."), title=_("Already Decided"))

		if self.decision == "Pending":
			self.approver = None
			self.decided_at = None
		elif not before or before.decision == "Pending":
			self.decide()

	def decide(self):
		# The approver is whoever makes this save, never a value the client supplies.
		self.approver = frappe.session.user
		self.decided_at = now_datetime()
		if not can_approve_reassignment(user=self.approver, request_doc=self):
			frappe.throw(
				_("Only a supervising officer may approve or reject this reassignment."),
				frappe.PermissionError,
				title=_("Approval Not Permitted"),
			)
		if self.decision != "Approved":
			return
		current = frappe.db.get_value(
			"Grievance", self.grievance, ["assigned_dept", "assigned_to"], as_dict=True
		)
		if (current.assigned_dept, current.assigned_to) != (self.prior_department, self.prior_officer):
			frappe.throw(
				_("The grievance was reassigned after this request was made. Raise a new request."),
				title=_("Request Is Stale"),
			)

	def on_update(self):
		before = self.get_doc_before_save()
		if self.decision == "Approved" and (not before or before.decision == "Pending"):
			self.apply()

	def apply(self):
		from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
			GrievanceTimeline,
		)

		grievance = frappe.get_doc("Grievance", self.grievance)
		updates = {"assigned_dept": self.target_department}
		if self.target_officer:
			updates["assigned_to"] = self.target_officer
		grievance.db_set(updates, update_modified=False)

		body = f"Reassigned to {self.target_department}"
		if self.target_officer:
			body += f" ({self.target_officer})"
		if self.reason and self.reason.strip():
			body += f" - Reason: {self.reason.strip()}"

		# The endpoint answers with this entry, so hand it back rather than re-query.
		self.flags.timeline_entry = GrievanceTimeline.record(
			grievance=self.grievance,
			entry_type="assignment",
			is_internal=False,
			body=body,
			author_user=self.approver,
			ref_doctype=self.doctype,
			ref_docname=self.name,
		)
