"""Document event handlers registered in hooks.py.

These are the joins between a saved record and the workflow, kept
out of the doctype controllers so the sequence is readable in one place.
"""

import frappe
from frappe.model.workflow import get_workflow
from frappe.utils import now_datetime

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import notifications, sla

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
	# action joins the two states, so the trail names it -- unless several do
	# (Resolve and Partially Resolve), where guessing would record the wrong one.
	if not context.action:
		joining = {
			row.action
			for row in get_workflow(doc.doctype).transitions
			if row.state == from_state and row.next_state == to_state
		}
		context.action = joining.pop() if len(joining) == 1 else None

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
			"action": context.action,
			"is_automated": 1 if context.automated else 0,
			"changed_by": user,
			"timestamp": now_datetime(),
			"reason": context.reason,
			"notes": context.note,
		}
	).insert(ignore_permissions=True)
	context.history = history

	sla.on_status_change(doc, to_state, from_state=from_state)
	sla.arm_state_timer(doc, to_state)

	stamp_resolution(doc, to_state)

	if context.get("notify", True):
		if to_state == C.STATE_IN_PROGRESS:
			if from_state in (C.STATE_ASSIGNED, C.STATE_SUBMITTED):
				notifications.queue(doc, C.EVENT_STATUS_IN_PROGRESS)
			elif from_state == C.STATE_RESOLVED or context.get("action") == "Reopen":
				notifications.queue(doc, C.EVENT_REOPENED)
		elif to_state == C.STATE_MORE_INFO_NEEDED:
			notifications.queue(doc, C.EVENT_MORE_INFO_REQUESTED)
		elif to_state == C.STATE_RESOLVED:
			# Asks the submitter to confirm the resolution or reopen within the window.
			notifications.queue(doc, C.EVENT_RESPONSE_SENT)
		elif to_state == C.STATE_CLOSED:
			if from_state == C.STATE_RESOLVED and context.get("action") == "Auto Close":
				notifications.queue(doc, C.EVENT_AUTO_CLOSED)
			elif context.get("confirmed"):
				notifications.queue(doc, C.EVENT_CONFIRMED)
			else:
				notifications.queue(doc, C.EVENT_CLOSED)
		elif to_state == C.STATE_REJECTED:
			notifications.queue(doc, C.EVENT_STATUS_REJECTED)


def stamp_resolution(doc, to_state):
	"""Keep `resolved_at` on the moment the case last reached Resolved or Closed.

	Resolved then Closed keeps the first stamp: the case was resolved when the
	submitter confirmed it, not when they later closed it. A reopen clears it, so a
	case resolved twice counts once, on the day it was finally resolved.
	"""
	if to_state in C.RESOLVED_STATES:
		if not doc.resolved_at:
			doc.db_set("resolved_at", now_datetime(), update_modified=False)
	elif doc.resolved_at and to_state != C.STATE_REJECTED:
		doc.db_set("resolved_at", None, update_modified=False)
