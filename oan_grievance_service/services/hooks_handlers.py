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


def on_user_registered(user_doc, role=None, roles=None, **kwargs):
	"""Handle user registration event broadcast from oan_auth_service.

	If 'Grievance Submitter' is among the assigned roles:
	1. Resolves submitter_type (defaulting to 'Individual Farmer').
	2. Validates that the submitter type exists.
	3. Derives and validates the canonical dedupe_key based on scheme rules.
	4. Populates general contact and submitter-type-specific fields.
	5. Creates or updates and links the Submitter Profile record to the User.
	"""
	from oan_grievance_service.grievance_masters.doctype.grievance_submitter_profile.grievance_submitter_profile import (
		build_dedupe_key,
	)
	from oan_grievance_service.services import identity

	assigned_roles = set(roles or [])
	if role:
		assigned_roles.add(role)

	# Only process if user is registering as a Grievance Submitter
	if "Grievance Submitter" not in assigned_roles:
		return None

	submitter_type = (kwargs.get("submitter_type") or "Individual Farmer").strip()

	if not frappe.db.exists("Grievance Submitter Type", submitter_type):
		frappe.throw(
			_("Submitter Type '{0}' does not exist.").format(submitter_type),
			frappe.ValidationError,
		)

	submitter_name = (
		kwargs.get("submitter_name")
		or kwargs.get("full_name")
		or f"{user_doc.first_name or ''} {user_doc.last_name or ''}".strip()
		or user_doc.name
	)

	contact_mobile = (
		kwargs.get("contact_mobile") or kwargs.get("phone_number") or user_doc.mobile_no or ""
	).strip()

	contact_email = (kwargs.get("contact_email") or kwargs.get("email") or "").strip() or None

	if not contact_email and user_doc.email and not user_doc.email.endswith("@id.openagrinet.internal"):
		contact_email = user_doc.email

	# Notification language belongs on the User record, the one identity primitive shared by
	# submitters and staff. Guarded because a bench need not have the Language record seeded.
	preferred_language = kwargs.get("preferred_language")
	if preferred_language and frappe.db.exists("Language", preferred_language):
		user_doc.db_set("language", preferred_language, update_modified=False)

	# Automatically derive dedupe_key from inputs (fayda_id, registration_number, farmer_id, phone, etc.)
	dedupe_key = identity.derive_dedupe_key(
		submitter_type=submitter_type,
		mobile=contact_mobile,
		fayda_id=kwargs.get("fayda_id"),
		national_id=kwargs.get("national_id"),
		registration_number=kwargs.get("registration_number"),
		org_number=kwargs.get("org_number"),
		farmer_id=kwargs.get("farmer_id"),
		dedupe_key=kwargs.get("dedupe_key"),
	)

	if not dedupe_key:
		frappe.throw(
			_("Submitter registration requires a valid contact phone number or identifier."),
			frappe.ValidationError,
		)

	# Check if a Submitter Profile already exists with this dedupe_key
	existing_name = frappe.db.get_value("Grievance Submitter Profile", {"dedupe_key": dedupe_key}, "name")
	if existing_name:
		profile = frappe.get_doc("Grievance Submitter Profile", existing_name)
		if profile.user and profile.user != user_doc.name:
			frappe.throw(
				_(
					"A Submitter Profile with dedupe key '{0}' is already registered under another account."
				).format(dedupe_key),
				frappe.DuplicateEntryError,
			)
		profile.user = user_doc.name
		if submitter_name:
			profile.submitter_name = submitter_name
		if contact_mobile:
			profile.contact_mobile = contact_mobile
		if contact_email:
			profile.contact_email = contact_email
		admin_area = kwargs.get("administrative_area") or kwargs.get("region")
		if admin_area:
			profile.administrative_area = admin_area
		if kwargs.get("administrative_unit") or kwargs.get("woreda"):
			profile.administrative_unit = kwargs.get("administrative_unit") or kwargs.get("woreda")
		profile.save(ignore_permissions=True)
	else:
		profile = frappe.new_doc("Grievance Submitter Profile")
		profile.user = user_doc.name
		profile.submitter_type = submitter_type
		profile.submitter_name = submitter_name
		profile.contact_mobile = contact_mobile
		profile.contact_email = contact_email
		profile.dedupe_key = dedupe_key
		profile.administrative_area = kwargs.get("administrative_area") or kwargs.get("region")
		profile.administrative_unit = kwargs.get("administrative_unit") or kwargs.get("woreda")
		profile.active = 1

		profile.insert(ignore_permissions=True)

	return profile
