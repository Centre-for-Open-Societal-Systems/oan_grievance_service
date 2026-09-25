"""Document event handlers registered in hooks.py.

These are the joins between a saved record and the workflow the FSD describes, kept
out of the doctype controllers so the sequence is readable in one place.
"""

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

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
	"""Record the move and carry out what the FSD attaches to arriving in a state."""
	context = frappe.flags.grievance_transition or frappe._dict()
	to_state = doc.workflow_state

	# A desk button arrives with no context; the Workflow still knows which
	# action joins the two states, so the trail names it either way.
	if not context.action:
		context.action = _action_between(doc, from_state, to_state)

	user = None if context.automated else frappe.session.user
	if user == "Guest":
		user = None

	# Refuses, and with it the whole move, when the FSD wants a reason and none came.
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

	# FSD 4.2 step 1: the SLA clock starts when the case reaches a department.
	if to_state == "Assigned":
		if doc.assigned_to and not doc.assigned_dept:
			from oan_grievance_service.permissions import active_scopes

			scopes = active_scopes(doc.assigned_to)
			if scopes and scopes[0].get("department_scope"):
				doc.db_set("assigned_dept", scopes[0].get("department_scope"), update_modified=False)
		sla.start_clock(doc)

	# The clock stops while the case waits on the submitter and the deadline is
	# pushed out by the hold when they reply. A response says which it wants
	# (Grievance Response.sla_behaviour); every other move reads the site's list.
	# Terminal states freeze the clock as it stands: nothing resumes it.
	paused = sla.paused_statuses()
	if to_state in ("Closed", "Rejected"):
		pass
	elif context.sla_behaviour == "paused" or (not context.sla_behaviour and to_state in paused):
		sla.pause_clock(doc)
	elif context.sla_behaviour == "running" or (not context.sla_behaviour and from_state in paused):
		sla.resume_clock(doc)

	# FSD 3.6: entering Pending Submitter opens the confirmation window.
	if to_state == "Pending Submitter":
		doc.db_set(
			"confirmation_deadline",
			add_days(now_datetime(), lifecycle.confirmation_window_days(doc.service_category)),
			update_modified=False,
		)

	if context.get("notify", True):
		event = lifecycle.STATUS_EVENT.get(to_state)
		if event:
			notifications.queue(doc, event)


def _action_between(doc, from_state, to_state):
	from frappe.model.workflow import get_workflow

	for row in get_workflow(doc.doctype).transitions:
		if row.state == from_state and row.next_state == to_state:
			return row.action
	return None


def response_after_insert(doc, method=None):
	"""FSD 3.5 and Appendix D-2: the response outcome drives the next status."""
	grievance = frappe.get_doc("Grievance", doc.grievance)

	# D-3: response_date, responded_by, sequence and prior_status are filled in by the
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
			sla_behaviour=doc.sla_behaviour,
		)

	doc.db_set("new_status", grievance.status, update_modified=False)

	# FSD 4.3: a structured response clears the escalation flag.
	sla.clear_escalation(grievance)

	notifications.queue(grievance, C.EVENT_RESPONSE_SENT)
	doc.db_set({"notification_sent": 1, "notification_sent_at": now_datetime()}, update_modified=False)
