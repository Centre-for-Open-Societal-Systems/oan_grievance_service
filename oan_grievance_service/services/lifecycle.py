"""FR-04 Tracking and Status Management.

Every move goes through Frappe's workflow engine. `transition` asks
`frappe.model.workflow.apply_workflow` for an action by name; the engine checks the
Grievance Workflow record for that move from the case's current state and the
caller's role, then saves, submits or cancels the document. The Grievance controller
sees that save and, from it, writes the hash-chained Grievance Status History row,
the timeline event, the SLA change and the notification (hooks_handlers). Nothing
sets `status` directly and nothing here decides whether a move is legal.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C


def transition(
	grievance,
	action,
	*,
	reason=None,
	note=None,
	automated=False,
	notify=True,
	closure_type=None,
	sla_behaviour=None,
):
	"""Take a workflow action on a grievance. Returns the Grievance Status History row.

	`sla_behaviour` is what a Grievance Response asked of the clock -- `running`,
	`paused` or `stopped`. Left None, the site's paused-status list decides.

	`automated` is for moves the system makes on its own -- auto-routing, the
	auto-close job, an anonymity ruling's rejection. The Workflow allows those
	actions to officer roles, but the request they run in may be a submitter's, so
	they run with the system's authority and the history row records them as
	automated, with no user.

	Permission on the document itself was settled by whoever loaded it (`_load` in
	the API checks the caller can act on the case); the workflow's role check is
	the authorisation for the move. The save inside the engine is therefore told
	not to re-check DocPerms, which for a submitter's own confirm would otherwise
	refuse `submit` -- a permission type the FSD roles never hold directly.
	"""
	context = frappe._dict(
		action=action,
		reason=reason,
		note=note,
		automated=automated,
		notify=notify,
		closure_type=closure_type,
		sla_behaviour=sla_behaviour,
		history=None,
	)
	outer = frappe.flags.grievance_transition
	frappe.flags.grievance_transition = context
	user = frappe.session.user
	grievance.flags.ignore_permissions = True
	try:
		if automated and user != "Administrator":
			# Audited: a system move (auto-route, auto-close, anonymity ruling) taken
			# with the system's authority; the caller's user is restored in `finally`.
			frappe.set_user("Administrator")  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
		apply_workflow(grievance, action)
	finally:
		if frappe.session.user != user:
			frappe.set_user(user)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
		frappe.flags.grievance_transition = outer
	return context.history


def actions_available(grievance):
	"""The actions the Workflow offers the current user from where the case is now."""
	from frappe.model.workflow import get_transitions

	return [row["action"] for row in get_transitions(grievance)]


STATUS_EVENT = {
	C.IN_PROGRESS: C.EVENT_STATUS_IN_PROGRESS,
	C.MORE_INFO_NEEDED: C.EVENT_MORE_INFO_REQUESTED,
	C.PENDING_SUBMITTER: C.EVENT_CONFIRMATION_WINDOW,
	C.RESOLVED: C.EVENT_CONFIRMED,
	C.CLOSED: C.EVENT_CLOSED,
}


def confirmation_window_days():
	"""FSD 3.6: configurable, default 7 days."""
	return frappe.conf.get("grievance_confirmation_window_days") or C.DEFAULT_CONFIRMATION_DAYS


def submit(grievance):
	"""FSD 4.1 step 5: the case leaves Draft. The document is submitted with it."""
	return transition(grievance, C.ACTION_SUBMIT)


def assign(grievance, note=None, automated=False):
	"""FSD 4.1 step 7 / 8b: the case reaches a department, by rule or by hand."""
	return transition(grievance, C.ACTION_ASSIGN, note=note, automated=automated)


def accept(grievance):
	"""FSD 4.2 step 2: the officer accepts and begins work."""
	return transition(grievance, C.ACTION_START_WORK, note="Accepted by department officer")


def request_more_info(grievance, question):
	"""FSD 4.2 step 3: the officer asks the submitter for more detail."""
	from oan_grievance_service.services import notifications

	# Recorded before the move, because the move's guard looks for it.
	GrievanceTimeline.record(
		grievance=grievance.name,
		entry_type="info_request",
		is_internal=False,
		body=question,
		author_user=frappe.session.user,
	)

	# Legacy comment record for backward compatibility
	comment = frappe.get_doc(
		{
			"doctype": "Grievance Comment",
			"grievance": grievance.name,
			"comment_type": "Information Request",
			"is_internal": 0,
			"body": question,
			"author_user": frappe.session.user,
			"created_on": now_datetime(),
		}
	).insert(ignore_permissions=True)

	transition(grievance, C.ACTION_REQUEST_MORE_INFO, note="Additional information requested")
	notifications.queue(grievance, C.EVENT_MORE_INFO_REQUESTED)
	return comment


def submitter_replies(grievance, body):
	"""FSD Appendix C: the submitter answers, the case returns to In Progress."""
	from oan_grievance_service.services import notifications

	# Record in unified timeline
	GrievanceTimeline.record(
		grievance=grievance.name,
		entry_type="info_response",
		is_internal=False,
		body=body,
		author_submitter=grievance.submitter,
	)

	# Legacy comment record for backward compatibility
	comment = frappe.get_doc(
		{
			"doctype": "Grievance Comment",
			"grievance": grievance.name,
			"comment_type": "Information Response",
			"is_internal": 0,
			"body": body,
			"author_submitter": grievance.submitter,
			"created_on": now_datetime(),
		}
	).insert(ignore_permissions=True)

	if grievance.status == C.MORE_INFO_NEEDED:
		transition(grievance, C.ACTION_SUBMITTER_REPLY, note="Submitter provided the requested information")
	notifications.queue(grievance, C.EVENT_SUBMITTER_RESPONDED)
	return comment


def confirm_resolution(grievance):
	"""FSD 3.6 / UC-03: the submitter confirms, so the case resolves then closes."""
	transition(
		grievance, C.ACTION_CONFIRM_RESOLUTION, note="Confirmed by submitter", closure_type="confirmed"
	)
	grievance.db_set("closure_reason", "Confirmed by submitter", update_modified=False)
	return transition(
		grievance,
		C.ACTION_CLOSE_CASE,
		note="Closed after submitter confirmation",
		closure_type="confirmed",
	)


def reopen(grievance, reason):
	"""FSD 3.6: reopen inside the confirmation window. The reason is mandatory, and
	the history row is what insists on it."""
	from oan_grievance_service.services import notifications

	history = transition(grievance, C.ACTION_REOPEN, reason=reason, notify=False)
	grievance.db_set("reopen_count", (grievance.reopen_count or 0) + 1, update_modified=False)
	notifications.queue(grievance, C.EVENT_REOPENED)
	return history


def auto_close(grievance):
	"""FSD 3.6: no response inside the confirmation window closes the case."""
	from oan_grievance_service.services import notifications

	grievance.db_set("closure_reason", "Closed - no objection received", update_modified=False)
	history = transition(
		grievance,
		C.ACTION_AUTO_CLOSE,
		note="Closed - no objection received",
		automated=True,
		notify=False,
		closure_type="auto_closed",
	)
	notifications.queue(grievance, C.EVENT_AUTO_CLOSED)
	return history


def reject(grievance, reason, automated=False):
	"""FSD 3.4: a terminal state for an invalid or out-of-scope grievance."""
	return transition(grievance, C.ACTION_REJECT, reason=reason, automated=automated, closure_type="rejected")


def display_group_filters(group):
	"""FSD 3.11.3: map a work-queue card to canonical statuses."""
	statuses = C.DISPLAY_GROUPS.get(group)
	if statuses is None:
		return {}
	return {"status": ["in", list(statuses)]}
