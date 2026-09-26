# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""A request to change fields on a submitted grievance, decided up the hierarchy.

Deferral, reassignment and anonymity are all the same thing: someone asks for new
values on a few fields and someone above them rules on it. The request is the audit
record of who asked, who was asked, and who decided; the Grievance controller owns
which fields may be requested and what else moves when they change.

Routing follows the chain the case already escalates along:
    submitter -> officer handling the case -> their higher authority -> ... -> admin
A request sits with one person. If they do not act within their rung's hours it is
forwarded one step up; past the top of the chain it waits in the admin queue
(`pending_with` empty). Everything here runs on save, so the API, background jobs
and the desk form all go through the same checks.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, get_datetime_str, now_datetime

from oan_grievance_service.permissions import is_staff, is_unrestricted
from oan_grievance_service.services import constants as C

DECIDED = ("Approved", "Rejected")

# Deferral and reassignment are the department's business; a submitter may only
# ask for their own identity to be withheld.
SUBMITTER_REQUESTABLE_FIELDS = ("anonymity_status",)

# What was asked for is fixed once the request exists; only the ruling moves.
_FROZEN_FIELDS = ("grievance", "subject", "reason", "requested_by", "requested_at")
# Moved only by routing and forwarding, never by a client.
_ROUTING_FIELDS = ("pending_with", "pending_since")


class GrievanceChangeRequest(Document):
	def before_insert(self):
		grievance = self._grievance()
		if grievance.docstatus != 1:
			frappe.throw(
				_("Changes can only be requested on an open grievance."), title=_("Grievance Not Open")
			)

		# Who asked, when, and what the case looked like are the server's to record.
		self.requested_by = frappe.session.user
		self.requested_at = now_datetime()
		self.status = "Pending"
		self.decided_by = None
		self.decided_at = None
		self.snapshot_changes(grievance)
		self.log("Requested", self.requested_by)

		if requester_may_decide(self.requested_by, grievance, self):
			self.decide("Approved", self.requested_by)
		else:
			self.route_to(approver_for(self.requested_by, grievance))

	def snapshot_changes(self, grievance):
		staff = is_staff(self.requested_by)
		seen = set()
		for row in self.changes:
			if not staff and row.fieldname not in SUBMITTER_REQUESTABLE_FIELDS:
				frappe.throw(
					_("A submitter can only request anonymity."),
					frappe.PermissionError,
					title=_("Change Not Permitted"),
				)
			if row.fieldname in seen:
				frappe.throw(
					_("Field '{0}' is listed more than once.").format(row.fieldname),
					title=_("Duplicate Field"),
				)
			seen.add(row.fieldname)
			grievance.validate_requested_change(row.fieldname, row.new_value)
			row.old_value = as_text(grievance, row.fieldname, grievance.get(row.fieldname))
			row.new_value = as_text(grievance, row.fieldname, row.new_value)
			if row.old_value == row.new_value:
				frappe.throw(
					_("Field '{0}' already has that value.").format(row.fieldname),
					title=_("Nothing To Change"),
				)

	def validate(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if not before:
			return

		for field in _FROZEN_FIELDS:
			if self.has_value_changed(field):
				frappe.throw(
					_("{0} cannot change once a request is raised.").format(_(self.meta.get_label(field))),
					title=_("Request Is Fixed"),
				)
		if _changes_key(self) != _changes_key(before):
			frappe.throw(
				_("The requested changes cannot be edited once raised."), title=_("Request Is Fixed")
			)
		if not self.flags.forwarding and any(self.has_value_changed(f) for f in _ROUTING_FIELDS):
			frappe.throw(_("Only routing may move a request between approvers."), title=_("Request Is Fixed"))

		if before.status in DECIDED:
			if self.has_value_changed("status") or self.has_value_changed("decision_note"):
				frappe.throw(_("This request has already been decided."), title=_("Already Decided"))
			return

		if self.has_value_changed("status"):
			if self.status not in DECIDED:
				frappe.throw(_("A request can only be approved or rejected."), title=_("Invalid Decision"))
			self.check_decider(frappe.session.user)
			self.decide(self.status, frappe.session.user)

	def on_update(self):
		if not self.has_value_changed("status") or self.status not in DECIDED:
			return

		grievance = self._grievance()
		if self.status == "Approved":
			self.check_not_stale(grievance)
			grievance.apply_change_request(self)
		else:
			grievance.reject_change_request(self)
		self.record_on_timeline()

	# Deciding
	# --------

	def check_decider(self, user):
		"""Only the person it sits with decides, or an admin. Never the requester."""
		if user == self.requested_by and not is_unrestricted(user):
			frappe.throw(
				_("A request cannot be decided by the person who raised it."),
				frappe.PermissionError,
				title=_("Approval Not Permitted"),
			)
		if is_unrestricted(user) or (self.pending_with and user == self.pending_with):
			return
		frappe.throw(
			_("This request is waiting on someone else."),
			frappe.PermissionError,
			title=_("Approval Not Permitted"),
		)

	def decide(self, status, user):
		self.status = status
		self.decided_by = user
		self.decided_at = now_datetime()
		self.pending_with = None
		self.log(status, user, note=self.decision_note)

	def check_not_stale(self, grievance):
		"""The approver ruled on the values shown to them; refuse if the case has moved since."""
		for row in self.changes:
			if as_text(grievance, row.fieldname, grievance.get(row.fieldname)) != row.old_value:
				frappe.throw(
					_("'{0}' has changed since this request was raised. Raise a new request.").format(
						row.fieldname
					),
					title=_("Request Is Stale"),
				)

	# Routing
	# -------

	def route_to(self, user, action=None):
		self.pending_with = user
		self.pending_since = now_datetime()
		if action:
			self.log(action, None)

	def forward(self):
		"""Hand an undecided request one step up. Past the top it waits for an admin."""
		self.flags.forwarding = True
		self.route_to(approver_above(self.pending_with, self._grievance()), action="Forwarded")
		self.save(ignore_permissions=True)

	# Records
	# -------

	def log(self, action, user, note=None):
		self.append(
			"approvals",
			{
				"action": action,
				"user": user,
				"pending_with": self.pending_with,
				"at": now_datetime(),
				"note": note,
			},
		)

	def record_on_timeline(self):
		from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
			GrievanceTimeline,
		)

		lines = [f"{row.fieldname}: {row.old_value or '-'} -> {row.new_value or '-'}" for row in self.changes]
		body = f"{self.subject} ({_(self.status).lower()})\n" + "\n".join(lines)
		if self.reason:
			body += f"\nReason: {self.reason}"
		if self.decision_note:
			body += f"\nNote: {self.decision_note}"

		# A new handler is something the submitter sees; the rest is internal housekeeping.
		reassigned = self.status == "Approved" and any(
			row.fieldname in ("assigned_dept", "assigned_to") for row in self.changes
		)
		# Callers that answer with the timeline event (the API) read it off the flags.
		self.flags.timeline_entry = GrievanceTimeline.record(
			grievance=self.grievance,
			entry_type="assignment" if reassigned else "note",
			is_internal=not reassigned,
			body=body,
			author_user=self.decided_by,
			ref_doctype=self.doctype,
			ref_docname=self.name,
		)

	def _grievance(self):
		return frappe.get_doc("Grievance", self.grievance)


def as_text(grievance, fieldname, value):
	"""One text form per value, so a snapshot compares equal to the live field."""
	if value in (None, ""):
		return ""
	df = grievance.meta.get_field(fieldname)
	if df and df.fieldtype == "Datetime":
		return get_datetime_str(value)
	return cstr(value)


def _changes_key(doc):
	return sorted((row.fieldname, row.new_value or "") for row in doc.get("changes") or [])


def approver_for(requester, grievance):
	"""Who a new request goes to first."""
	if is_staff(requester):
		return approver_above(requester, grievance)
	# A submitter's request goes to the officer handling the case.
	return grievance.assigned_to or approver_above(None, grievance)


def approver_above(user, grievance):
	"""One step above `user` for this case's department and area; None is the admin queue."""
	from oan_grievance_service.services import sla

	user_above, _level = sla.higher_authority_of(
		user,
		department=grievance.assigned_dept,
		administrative_area=grievance.administrative_area,
	)
	return user_above


def requester_may_decide(user, grievance, request):
	"""A requester who already stands above the case needs nobody's approval.

	Admins, and a supervising officer of the assignee, act directly - the same
	authority they would need to approve the request from someone else. A deferral
	also goes straight through when the deferral policy does not ask for a supervisor.
	"""
	if is_unrestricted(user):
		return True
	if not is_staff(user):
		return False
	# Anonymity is never decided by whoever asked for it - including the
	# officer who filed on the submitter's behalf.
	if any(row.fieldname in SUBMITTER_REQUESTABLE_FIELDS for row in request.changes):
		return False

	assignee = grievance.assigned_to
	if assignee and assignee != user:
		from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
			get_officer_supervisor,
		)
		from oan_grievance_service.permissions import outranks

		supervisor = get_officer_supervisor(
			assignee,
			department=grievance.assigned_dept,
			administrative_area=grievance.administrative_area,
			strict=True,
		)
		if supervisor == user or outranks(user, assignee, if_unplaced=False):
			return True

	if all(row.fieldname == "sla_due_date" for row in request.changes):
		from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
			requires_supervisor_approval,
		)

		return not requires_supervisor_approval()

	return False
