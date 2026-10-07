"""Tracking and status management.

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
	confirmed=False,
):
	"""Take a workflow action on a grievance. Returns the Grievance Status History row.

	`automated` is for moves the system makes on its own -- auto-routing, the
	auto-close job, an anonymity ruling's rejection. The Workflow allows those
	actions to officer roles, but the request they run in may be a submitter's, so
	they run with the system's authority and the history row records them as
	automated, with no user.

	`confirmed` marks a Close Case the submitter took, which earns the confirmation
	notification rather than the plain closure one.

	Permission on the document itself was settled by whoever loaded it (`_load` in
	the API checks the caller can act on the case); the workflow's role check is
	the authorisation for the move. The save inside the engine is therefore told
	not to re-check DocPerms, which for a submitter's own confirm would otherwise
	refuse `submit` -- a permission type the grievance roles never hold directly.
	"""
	context = frappe._dict(
		action=action,
		reason=reason,
		note=note,
		automated=automated,
		notify=notify,
		confirmed=confirmed,
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
	"""The actions the Workflow offers the current user from where the case is now.

	A move into a state that waits on the submitter -- one whose SLA category is
	Paused -- is withheld when the submitter has no contact details: nobody could
	tell them, so the case would only sit there until it timed out. Which states
	wait is the Workflow's own setting, not a list kept here.
	"""
	import frappe
	from frappe.model.workflow import get_transitions

	from oan_grievance_service import permissions
	from oan_grievance_service.services import identity, sla

	user = frappe.session.user
	if permissions.is_staff(user) and not permissions.is_unrestricted(user):
		if grievance.assigned_to and grievance.assigned_to != user:
			return []

	transitions = get_transitions(grievance)
	if transitions and not identity.is_reachable(grievance):
		waiting = sla.states_in_category(sla.PAUSED)
		transitions = [row for row in transitions if row["next_state"] not in waiting]
	actions = list(dict.fromkeys(row["action"] for row in transitions))
	if "In Progress" in actions and "Start Work" in actions:
		actions.remove("Start Work")
	return actions
