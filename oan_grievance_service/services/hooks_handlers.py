"""Document event handlers registered in hooks.py.

These are the joins between a saved record and the workflow, kept
out of the doctype controllers so the sequence is readable in one place.
"""

import frappe
from frappe.model.workflow import get_workflow
from frappe.utils import now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle, notifications, sla

# Workflow moves
# --------------
# Frappe's engine drives a move by saving, submitting or cancelling the Grievance, so
# the Grievance controller calls `after_workflow_action` once the new
# state is written.


def after_workflow_action(doc, from_state):
	"""Record the move and carry out what arriving in a state requires."""
	context = frappe.flags.grievance_transition or frappe._dict()
	to_state = doc.workflow_state

	# A desk button arrives with no context; the Workflow still knows which
	# action joins the two states, so the trail names it either way.
	if not context.action:
		context.action = next(
			(
				row.action
				for row in get_workflow(doc.doctype).transitions
				if row.state == from_state and row.next_state == to_state
			),
			None,
		)

	user = None if context.automated else frappe.session.user
	if user == "Guest":
		user = None

	# Refuses, and with it the whole move, when a reason is required and none came.
	history = frappe.get_doc(
		{
			"doctype": "Grievance Status History",
			"grievance": doc.name,
			"from_status": from_state,
			"to_status": to_state,
			"transition": context.action,
			"closure_type": context.closure_type,
			"is_automated": 1 if context.automated else 0,
			"changed_by": user,
			"timestamp": now_datetime(),
			"reason": context.reason,
			"notes": context.reason or context.note,
		}
	).insert(ignore_permissions=True)
	context.history = history

	if to_state == C.STATE_ASSIGNED and doc.assigned_to and not doc.assigned_dept:
		from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
			active_scopes,
		)

		scopes = active_scopes(doc.assigned_to)
		if scopes and scopes[0].get("department_scope"):
			doc.db_set("assigned_dept", scopes[0].get("department_scope"), update_modified=False)

	sla.on_status_change(doc, to_state)
	sla.arm_state_timer(doc, to_state)

	if context.get("notify", True):
		event = lifecycle.STATUS_EVENT.get(to_state)
		if event:
			notifications.queue(doc, event)


def response_after_insert(doc, method=None):
	"""The response outcome drives the next status."""
	grievance = frappe.get_doc("Grievance", doc.grievance)

	# response_date, responded_by, sequence and prior_status are filled in by the
	# controller before validation, because they are mandatory. The IP is captured here
	# because it is only meaningful for a request that actually reached the server.
	if getattr(frappe.local, "request_ip", None):
		doc.db_set("ip_address", frappe.local.request_ip, update_modified=False)

	# Record formal response in unified timeline spine
	GrievanceTimeline.record(
		grievance=grievance.name,
		entry_type="response",
		is_internal=False,
		body=doc.resolution_summary or doc.action_taken or f"Formal Response ({doc.response_type})",
		author_user=doc.responded_by or frappe.session.user,
		ref_doctype="Grievance Response",
		ref_docname=doc.name,
	)

	# Dynamic Master Resolution: the linked Grievance Response Type names the action.
	action = None
	if doc.response_type and frappe.db.exists("Grievance Response Type", doc.response_type):
		action = frappe.db.get_value("Grievance Response Type", doc.response_type, "workflow_action")

	if action and action in lifecycle.actions_available(grievance):
		lifecycle.transition(
			grievance,
			action,
			note=f"Response {doc.name} ({doc.response_type})",
		)

	doc.db_set("new_status", grievance.status, update_modified=False)

	# A response clears the escalation flag only. `next_escalation_at` keeps running:
	# an officer who answers and then sits on the case again must still be overtaken.
	if grievance.escalated:
		grievance.db_set("escalated", 0, update_modified=False)

	notifications.queue(grievance, C.EVENT_RESPONSE_SENT)
	doc.db_set({"notification_sent": 1, "notification_sent_at": now_datetime()}, update_modified=False)
