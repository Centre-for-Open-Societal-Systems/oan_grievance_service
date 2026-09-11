"""FR-02 submission and FR-06 submitter actions, exposed for the mobile app, web
portal, IVR and call centre channels described in FSD 3.2.1.

Every entry point is whitelisted, validates its own input, and routes through the
service layer so the audit trail and notifications cannot be bypassed.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service.api import version_meta
from oan_grievance_service.services import audit, lifecycle, routing, sla, submission
from oan_grievance_service.services import constants as C

from . import VERSION

CHANNELS = (
	"Mobile App",
	"Web Portal",
	"Mobile Call",
	"IVR Helpline",
	"Development Agent Assisted",
)


@frappe.whitelist()
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
		"region",
		"woreda",
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

	# A retry must not lodge a second case. Checked before any write, so two
	# concurrent retries race on the unique index rather than on this read.
	client_uuid = kwargs.get("client_submission_uuid")
	if client_uuid:
		existing = frappe.db.get_value(
			"Grievance",
			{"client_submission_uuid": client_uuid},
			["name", "ticket_number", "status"],
			as_dict=True,
		)
		if existing:
			return envelope(
				{
					"ticket_number": existing.ticket_number,
					"status": existing.status,
					"duplicate_submission": True,
				}
			)

	kwargs["contact_mobile"] = submission.normalise_mobile(kwargs.get("contact_mobile"))

	# The zone is implied by the woreda. IVR and call-centre operators capture the
	# woreda directly, so requiring the intermediate level would be friction with
	# no information gained.
	if not kwargs.get("zone"):
		kwargs["zone"] = frappe.db.get_value("Woreda", kwargs["woreda"], "zone")

	doc = frappe.new_doc("Grievance")
	for field, value in kwargs.items():
		if doc.meta.has_field(field):
			doc.set(field, value)

	doc.status = C.SUBMITTED
	doc.submitter = submission.find_or_create_submitter(kwargs)
	doc.area_path_code = submission.resolve_area_path(
		region=doc.region, zone=doc.zone, woreda=doc.woreda, kebele=doc.kebele
	)
	submission.record_consent(doc)
	doc.insert(ignore_permissions=True)

	if kwargs.get("is_anonymous"):
		_request_anonymity(doc, kwargs.get("anonymity_justification"))

	attachments = _claim_draft(kwargs.get("client_uuid"), doc)
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
			"area_path_code": doc.area_path_code,
			"attachments": attachments,
			"duplicate_submission": False,
		}
	)


def _request_anonymity(doc, justification):
	"""FSD 9.2: anonymity is requested at submission and approved separately."""
	frappe.get_doc(
		{
			"doctype": "Grievance Anonymity Request",
			"grievance": doc.name,
			# The request and the grievance use different vocabularies: the request
			# is "Pending", the flag it drives on the grievance is "Pending Approval".
			"status": "Pending",
			"requested_at": now_datetime(),
			"justification": justification,
		}
	).insert(ignore_permissions=True)
	doc.db_set("anonymity_status", "Pending Approval", update_modified=False)


def _claim_draft(client_uuid, doc):
	"""Bind the draft this submission came from to the grievance it became, and
	move any files uploaded against it."""
	if not client_uuid:
		return 0

	draft = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	if not draft:
		return 0

	moved = submission.attach_draft_files(draft, doc.name)
	frappe.db.set_value("Grievance Draft", draft, "submitted_as", doc.name, update_modified=False)
	return moved


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
def reopen(ticket_number, reason):
	"""FSD 3.6: reopen with a mandatory reason."""
	doc = _load(ticket_number)
	lifecycle.reopen(doc, reason)
	return envelope({"ticket_number": doc.ticket_number, "status": doc.status})


@frappe.whitelist()
def escalate(ticket_number, reason):
	"""FSD 3.7: the submitter escalates once the SLA window has elapsed."""
	doc = _load(ticket_number)
	sla.manual_escalate(doc, reason, by_submitter=True)
	return envelope({"ticket_number": doc.ticket_number, "escalated": True})


@frappe.whitelist()
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
