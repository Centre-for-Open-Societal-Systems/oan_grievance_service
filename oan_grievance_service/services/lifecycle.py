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
	if action == "Submit":
		from oan_grievance_service.services import ticket_number as tn

		grievance.flags.in_submit = True
		if grievance.name.startswith("DRAFT-") or not grievance.ticket_number:
			t_num = tn.generate(grievance.administrative_area, grievance.service_category)
			if grievance.name != t_num:
				frappe.rename_doc("Grievance", grievance.name, t_num, force=True)
				grievance = frappe.get_doc("Grievance", t_num)
			grievance.ticket_number = grievance.name
			grievance.flags.ignore_permissions = True
			grievance.flags.in_submit = True

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
	"In Progress": C.EVENT_STATUS_IN_PROGRESS,
	"More Info Needed": C.EVENT_MORE_INFO_REQUESTED,
	"Pending Submitter": C.EVENT_CONFIRMATION_WINDOW,
	"Resolved": C.EVENT_CONFIRMED,
	"Closed": C.EVENT_CLOSED,
}


def confirmation_window_days(service_category: str | None = None) -> int:
	"""FSD 3.6: Dynamic citizen confirmation/appeal window in days.

	Reads from the active Grievance SLA Configuration for the category if configured,
	falling back to site config `grievance_confirmation_window_days` or default 7 days.
	"""
	if service_category:
		appeal_days = frappe.db.get_value(
			"Grievance SLA Configuration",
			{"service_category": service_category, "active": 1},
			"appeal_window_days",
		)
		if appeal_days:
			return int(appeal_days)
	return int(frappe.conf.get("grievance_confirmation_window_days") or C.DEFAULT_CONFIRMATION_DAYS)
