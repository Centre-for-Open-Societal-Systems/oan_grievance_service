"""FR-02 submission and FR-06 submitter actions, exposed for the mobile app, web
portal, IVR and call centre channels described in FSD 3.2.1.

Every entry point is whitelisted, validates its own input, and routes through the
service layer so the audit trail and notifications cannot be bypassed.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime
from oan_auth_service.api.utils import handle_api_errors, require_role

from oan_grievance_service.api import version_meta
from oan_grievance_service.services import audit, lifecycle, routing, sla
from oan_grievance_service.services import constants as C

from . import VERSION

ALLOWED_GRIEVANCE_ROLES = [
	"Grievance Submitter",
	"Grievance Officer",
	"Grievance Admin",
	"System Manager",
	"Administrator",
]

CHANNELS = (
	"Mobile App",
	"Web Portal",
	"Mobile Call",
	"IVR Helpline",
	"Development Agent Assisted",
)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def submit(**kwargs):
	"""FSD 4.1: validate, generate the ticket, acknowledge, then route.

	Returns the ticket number and the acknowledgement outcome, which is what the
	FSD 3.11.5 wizard success state displays.
	"""
	from oan_grievance_service.services import notifications

	required = (
		"submitter_type",
		"submitter_name",
		"contact_mobile",
		"submission_channel",
		"administrative_area",
		"service_category",
		"grievance_type",
		"description",
	)
	missing = [field for field in required if not kwargs.get(field)]
	if missing:
		frappe.throw(
			_("Missing required fields: {0}").format(", ".join(missing)),
			title=_("Incomplete Submission"),
		)

	if kwargs["submission_channel"] not in CHANNELS:
		frappe.throw(_("Unknown submission channel."), title=_("Invalid Channel"))

	doc = frappe.new_doc("Grievance")
	for field, value in kwargs.items():
		if doc.meta.has_field(field):
			doc.set(field, value)
	doc.status = C.SUBMITTED
	doc.insert(ignore_permissions=True)

	duplicates = detect_duplicates(doc)

	# FSD 4.1 step 6: acknowledge before routing, so the submitter always gets a ticket.
	notifications.queue(doc, C.EVENT_SUBMISSION_RECEIVED)
	if duplicates:
		notifications.queue(doc, C.EVENT_DUPLICATE_DETECTED)

	# FSD 4.1 step 7: routing decides auto-assignment or the manual queue.
	rule = routing.apply_routing(doc)
	doc.reload()

	return envelope(
		{
			"ticket_number": doc.ticket_number,
			"status": doc.status,
			"assigned_department": doc.assigned_dept,
			"auto_routed": bool(rule),
			"sla_due_date": doc.sla_due_date,
			"possible_duplicates": [d.duplicate_of for d in duplicates],
		}
	)


def detect_duplicates(grievance, window_days=7):
	"""FSD 3.2.3 / E3: match on submitter identity, grievance type and time proximity."""
	if not grievance.submitter:
		return []

	candidates = frappe.get_all(
		"Grievance",
		filters={
			"name": ["!=", grievance.name],
			"submitter": grievance.submitter,
			"grievance_type": grievance.grievance_type,
			"creation": [">=", frappe.utils.add_days(now_datetime(), -window_days)],
		},
		pluck="name",
	)

	rows = []
	for candidate in candidates:
		rows.append(
			frappe.get_doc(
				{
					"doctype": "Grievance Duplicate",
					"grievance": grievance.name,
					"duplicate_of": candidate,
					"detected_at": now_datetime(),
					"detection_method": "Identity + Type + Time Proximity",
					"similarity_score": 1.0,
				}
			).insert(ignore_permissions=True)
		)
	return rows


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def track(ticket_number):
	"""Submitter-facing status lookup for the portal and IVR."""
	name = frappe.db.get_value("Grievance", {"ticket_number": ticket_number}, "name")
	if not name:
		frappe.throw(_("No grievance found with that ticket number."), title=_("Not Found"))

	doc = frappe.get_doc("Grievance", name)
	audit.record_access(audit.ACTION_VIEW_DETAIL, grievance=name)

	return envelope(
		{
			"ticket_number": doc.ticket_number,
			"status": doc.status,
			"escalated": bool(doc.escalated),
			"department": doc.assigned_dept,
			"sla_due_date": doc.sla_due_date,
			"sla_consumed_percent": sla.consumed_percent(doc),
			"confirmation_deadline": doc.confirmation_deadline,
			"submitted_on": doc.creation,
		}
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def confirm(ticket_number, rating=None, comments=None):
	"""FSD 3.6 / UC-03: the submitter confirms the resolution."""
	doc = _load(ticket_number)
	if doc.status != C.PENDING_SUBMITTER:
		frappe.throw(_("This grievance is not awaiting your confirmation."))

	if rating:
		doc.db_set("satisfaction_rating", int(rating), update_modified=False)
	if comments:
		doc.db_set("satisfaction_comments", comments, update_modified=False)

	lifecycle.confirm_resolution(doc)
	return envelope({"ticket_number": doc.ticket_number, "status": C.CLOSED})


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def reopen(ticket_number, reason):
	"""FSD 3.6: reopen with a mandatory reason."""
	doc = _load(ticket_number)
	lifecycle.reopen(doc, reason)
	return envelope({"ticket_number": doc.ticket_number, "status": doc.status})


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def escalate(ticket_number, reason):
	"""FSD 3.7: the submitter escalates once the SLA window has elapsed."""
	doc = _load(ticket_number)
	sla.manual_escalate(doc, reason, by_submitter=True)
	return envelope({"ticket_number": doc.ticket_number, "escalated": True})


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def reply(ticket_number, body):
	"""FSD Appendix C: the submitter answers a More Info Needed request."""
	doc = _load(ticket_number)
	lifecycle.submitter_replies(doc, body)
	return envelope({"ticket_number": doc.ticket_number, "status": doc.status})


def envelope(data):
	"""Every v1 response carries the contract version it was served under, so a
	support ticket can name the contract rather than guess at it."""
	return {"meta": version_meta(VERSION), "data": data}


def _load(ticket_number):
	name = frappe.db.get_value("Grievance", {"ticket_number": ticket_number}, "name")
	if not name:
		frappe.throw(_("No grievance found with that ticket number."), title=_("Not Found"))
	return frappe.get_doc("Grievance", name)
