"""FR-02 submission and FR-06 submitter actions, exposed for the mobile app, web
portal, IVR and call centre channels described in FSD 3.2.1.

Every entry point is whitelisted, validates its own input, and routes through the
service layer so the audit trail and notifications cannot be bypassed.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime
from oan_auth_service.api.utils import handle_api_errors, require_role, success_response

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import audit, lifecycle, routing, sla, submission
from oan_grievance_service.services import constants as C

# Aliased: several entry points take a `ticket_number` argument, which would
# otherwise shadow the module inside them.
from oan_grievance_service.services import ticket_number as tn

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

# Staff may file on another person's behalf; a submitter may only file as themselves.
STAFF_ROLES = frozenset({"Grievance Officer", "Grievance Admin", "System Manager", "Administrator"})

# Columns the server derives on submission. Accepting any of these from the caller
# would let a client forge case ownership -- permissions.py scopes read and write
# access on `submitter` and `assisted_by_officer` -- or rewrite the filing-time
# area snapshot that Grievance.set_administrative_area_metadata is meant to own.
CLIENT_IMMUTABLE_FIELDS = frozenset(
	{
		"submitter",
		"submitter_name",
		"contact_mobile",
		"contact_email",
		"assisted_by_officer",
		"area_lft",
		"area_path_code",
		"status",
	}
)


def _resolve_submitter_identity(kwargs):
	"""Decide who a grievance belongs to, server-side, and snapshot their profile.

	A submitter always files as themselves: the session decides the owning profile,
	never the request. Staff may file on someone's behalf for the assisted and call
	centre channels, in which case the named submitter stays the owner and the staff
	member is recorded as the assisting officer for the audit trail.

	The name and contact columns are a filing-time copy of the profile, so a case
	keeps showing who reported it even after they later change their phone number.
	Staff taking a walk-in or IVR report from someone with no profile yet supply
	those details directly, because there is nothing to copy from.
	"""
	user = frappe.session.user
	is_staff = bool(set(frappe.get_roles(user)) & STAFF_ROLES)

	if is_staff:
		profile_name = kwargs.get("submitter")
		identity = {"submitter": profile_name, "assisted_by_officer": user}
	else:
		profile_name = frappe.db.get_value("Grievance Submitter Profile", {"user": user}, "name")
		if not profile_name:
			frappe.throw(
				_("No submitter profile associated with your user account."),
				title=_("Profile Not Found"),
			)
		identity = {"submitter": profile_name, "assisted_by_officer": None}

	if not profile_name:
		identity.update(
			{
				"submitter_name": kwargs.get("submitter_name"),
				"contact_mobile": kwargs.get("contact_mobile"),
				"contact_email": kwargs.get("contact_email"),
			}
		)
		return identity

	profile = frappe.db.get_value(
		"Grievance Submitter Profile",
		profile_name,
		["submitter_type", "submitter_name", "contact_mobile", "contact_email", "active", "is_blocked"],
		as_dict=True,
	)
	if not profile:
		frappe.throw(_("Unknown submitter profile."), title=_("Invalid Submitter"))

	# The schema records is_blocked as blocking new submissions while leaving existing
	# cases visible, so it is enforced here rather than in the permission layer.
	if profile.is_blocked or not profile.active:
		frappe.throw(
			_("This submitter profile cannot file new grievances."),
			title=_("Submitter Blocked"),
		)

	identity.update(
		{
			"submitter_type": profile.submitter_type,
			"submitter_name": profile.submitter_name,
			"contact_mobile": profile.contact_mobile,
			"contact_email": profile.contact_email,
		}
	)
	return identity


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def submit(**kwargs):
	"""FSD 4.1: validate, generate the ticket, acknowledge, then route.

	Returns the ticket number and the acknowledgement outcome, which is what the
	FSD 3.11.5 wizard success state displays.
	"""
	from oan_grievance_service.services import notifications

	# Identity is resolved first so the required-field check sees the profile snapshot:
	# a submitter filing for themselves need not send back their own name and number.
	identity = _resolve_submitter_identity(kwargs)
	resolved = {
		**{field: value for field, value in kwargs.items() if field not in CLIENT_IMMUTABLE_FIELDS},
		**identity,
	}

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
	missing = [field for field in required if not resolved.get(field)]
	if missing:
		frappe.throw(
			_("Missing required fields: {0}").format(", ".join(missing)),
			title=_("Incomplete Submission"),
		)

	if resolved["submission_channel"] not in CHANNELS:
		frappe.throw(_("Unknown submission channel."), title=_("Invalid Channel"))

	# A retry must not lodge a second case. This read settles the ordinary retry --
	# one that arrives after the first attempt committed. It cannot settle two
	# retries in flight at once, because both would read nothing and both would
	# insert; that case is caught on the unique index at insert time below.
	client_uuid = kwargs.get("client_submission_uuid")
	if client_uuid:
		original = _existing_submission(client_uuid)
		if original:
			return original

	kwargs["contact_mobile"] = submission.normalise_mobile(kwargs.get("contact_mobile"))

	doc = frappe.new_doc("Grievance")
	for field, value in resolved.items():
		if doc.meta.has_field(field):
			doc.set(field, value)
	doc.status = C.SUBMITTED
	# FR-02 duplicate detection matches on the submitter, so a grievance without one
	# can never be found to duplicate anything. Only fall back to creating a profile
	# when identity resolution found none -- staff taking a walk-in or IVR report
	# from someone who has never registered. Overwriting unconditionally would throw
	# away the session-resolved profile and let a submitter file against a profile of
	# their own choosing by varying contact_mobile.
	if not doc.submitter:
		doc.submitter = submission.find_or_create_submitter(kwargs)
	submission.record_consent(doc)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# Another retry carrying the same client_submission_uuid committed while this
		# one was building its document. The index is the only thing that can settle
		# that race, and it just did: hand back the ticket the winner created rather
		# than a 500 the client cannot act on.
		if not client_uuid:
			raise
		frappe.db.rollback()
		original = _existing_submission(client_uuid)
		if not original:
			raise
		return original

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

	return success_response(
		data={
			"ticket_number": doc.ticket_number,
			"status": doc.status,
			"assigned_department": doc.assigned_dept,
			"auto_routed": bool(rule),
			"sla_due_date": doc.sla_due_date,
			"possible_duplicates": [d.duplicate_of for d in duplicates],
			"area_path_code": doc.area_path_code,
			"attachments": attachments,
			"duplicate_submission": False,
		},
		message=_("Grievance submitted successfully"),
	)


def _existing_submission(client_uuid):
	"""The response for an already-lodged submission, or None if there isn't one.

	Shared by the pre-insert check and the unique-index recovery so a retry gets the
	same answer whichever of the two settles it.
	"""
	existing = frappe.db.get_value(
		"Grievance",
		{"client_submission_uuid": client_uuid},
		["name", "ticket_number", "status"],
		as_dict=True,
	)
	if not existing:
		return None

	return success_response(
		data={
			"ticket_number": existing.ticket_number,
			"status": existing.status,
			"duplicate_submission": True,
		},
		message=_("Grievance already submitted"),
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
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def track(ticket_number: str):
	"""Submitter-facing status lookup for the portal and IVR."""
	doc = _load(ticket_number)
	audit.record_access(audit.ACTION_VIEW_DETAIL, grievance=doc.name)

	return success_response(
		data={
			"ticket_number": doc.ticket_number,
			"ticket_number_display": tn.display(doc.ticket_number),
			"status": doc.status,
			"escalated": bool(doc.escalated),
			"department": doc.assigned_dept,
			"sla_due_date": doc.sla_due_date,
			"sla_consumed_percent": sla.consumed_percent(doc),
			"confirmation_deadline": doc.confirmation_deadline,
			"submitted_on": doc.creation,
		},
		message=_("Grievance status retrieved successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def confirm(ticket_number: str, rating: int | str | None = None, comments: str | None = None):
	"""FSD 3.6 / UC-03: the submitter confirms the resolution."""
	doc = _load(ticket_number)
	if doc.status != C.PENDING_SUBMITTER:
		frappe.throw(_("This grievance is not awaiting your confirmation."))

	if rating:
		doc.db_set("satisfaction_rating", int(rating), update_modified=False)
	if comments:
		doc.db_set("satisfaction_comments", comments, update_modified=False)

	lifecycle.confirm_resolution(doc)
	return success_response(
		data={"ticket_number": doc.ticket_number, "status": C.CLOSED},
		message=_("Resolution confirmed successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def reopen(ticket_number: str, reason: str):
	"""FSD 3.6: reopen with a mandatory reason."""
	doc = _load(ticket_number)
	lifecycle.reopen(doc, reason)
	return success_response(
		data={"ticket_number": doc.ticket_number, "status": doc.status},
		message=_("Grievance reopened successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def escalate(ticket_number: str, reason: str):
	"""FSD 3.7: the submitter escalates once the SLA window has elapsed."""
	doc = _load(ticket_number)
	# Throws when the case is already at the top of the chain, so reaching the response
	# means it actually moved. The old code reported success either way.
	sla.manual_escalate(doc, reason, by_submitter=True)
	doc.reload()
	return success_response(
		data={"ticket_number": doc.ticket_number, "escalated": bool(doc.escalated)},
		message=_("Grievance escalated successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def reply(ticket_number: str, body: str):
	"""FSD Appendix C: the submitter answers a More Info Needed request."""
	doc = _load(ticket_number)
	lifecycle.submitter_replies(doc, body)
	return success_response(
		data={"ticket_number": doc.ticket_number, "status": doc.status},
		message=_("Reply submitted successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def timeline(
	ticket_number: str,
	is_internal: bool | str | None = None,
	limit: int | str = 20,
	cursor: str | None = None,
):
	"""Retrieve chronological unified conversation and activity timeline for a grievance.

	Submitters only see public entries (is_internal = 0).
	Staff (Officers, Admins) see all entries or can filter by is_internal flag.
	"""
	doc = _load(ticket_number)
	audit.record_access(audit.ACTION_VIEW_DETAIL, grievance=doc.name)

	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	is_staff = bool(roles & STAFF_ROLES)

	filters = {"grievance": doc.name}

	if not is_staff:
		filters["is_internal"] = 0
	elif is_internal is not None:
		filters["is_internal"] = 1 if str(is_internal).lower() in ("1", "true", "yes") else 0

	if cursor:
		filters["created_on"] = ["<", cursor]

	page_limit = int(limit)
	entries = frappe.get_all(
		"Grievance Timeline",
		filters=filters,
		fields=[
			"name",
			"entry_type",
			"is_internal",
			"body",
			"author_user",
			"author_submitter",
			"ref_doctype",
			"ref_docname",
			"created_on",
		],
		order_by="created_on desc, name desc",
		limit=page_limit + 1,
	)

	has_more = len(entries) > page_limit
	if has_more:
		entries = entries[:page_limit]

	next_cursor = entries[-1]["created_on"].isoformat() if (has_more and entries) else None

	for entry in entries:
		entry["is_internal"] = bool(entry.get("is_internal"))
		if entry["author_submitter"]:
			entry["author_type"] = "submitter"
			entry["author_name"] = doc.submitter_name or entry["author_submitter"]
		elif entry["author_user"]:
			entry["author_type"] = "officer"
			entry["author_name"] = (
				frappe.db.get_value("User", entry["author_user"], "full_name") or entry["author_user"]
			)
		else:
			entry["author_type"] = "system"
			entry["author_name"] = "System"

	return success_response(
		data={
			"ticket_number": doc.ticket_number,
			"timeline": entries,
			"has_more": has_more,
			"next_cursor": next_cursor,
		},
		message=_("Timeline retrieved successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(STAFF_ROLES)
def add_note(ticket_number: str, body: str, is_internal: bool | str = True):
	"""Staff-only endpoint to add an internal or public note to the case timeline."""
	doc = _load(ticket_number)
	internal = str(is_internal).lower() not in ("0", "false", "no")

	entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="note",
		is_internal=internal,
		body=body,
		author_user=frappe.session.user,
	)

	return success_response(
		data={
			"name": entry.name,
			"entry_type": entry.entry_type,
			"is_internal": bool(entry.is_internal),
			"author_type": "officer",
			"created_on": entry.created_on,
		},
		message=_("Note added successfully"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def message(ticket_number: str, body: str):
	"""Post a public message to the case conversation thread."""
	doc = _load(ticket_number)
	user = frappe.session.user
	is_staff = bool(set(frappe.get_roles(user)) & STAFF_ROLES)

	entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="message",
		is_internal=False,
		body=body,
		author_user=user if is_staff else None,
		author_submitter=doc.submitter if not is_staff else None,
	)

	return success_response(
		data={
			"name": entry.name,
			"entry_type": entry.entry_type,
			"is_internal": False,
			"author_type": "officer" if is_staff else "submitter",
			"created_on": entry.created_on,
		},
		message=_("Message posted successfully"),
	)


def _load(ticket_number):
	"""Fetch a grievance by ticket number as the submitter typed it.

	Normalised first: the number is printed grouped (3-001-002A-0) and read back
	over a phone line, so the hyphens, casing and the O/I/L substitutions the
	alphabet anticipates must not decide whether a farmer can reach their own
	case.
	"""
	name = frappe.db.get_value("Grievance", {"ticket_number": tn.normalize(ticket_number)}, "name")
	if not name:
		frappe.throw(_("No grievance found with that ticket number."), title=_("Not Found"))
	return frappe.get_doc("Grievance", name)


def get_status_options() -> list[dict]:
	"""Retrieve grievance lifecycle status options with open/terminal metadata."""
	all_statuses = [
		C.SUBMITTED,
		C.ASSIGNED,
		C.IN_PROGRESS,
		C.MORE_INFO_NEEDED,
		C.PENDING_SUBMITTER,
		C.RESOLVED,
		C.CLOSED,
		C.REJECTED,
	]

	return [
		{
			"status": status,
			"label": status,
			"is_open": 1 if status in C.OPEN_STATUSES else 0,
			"is_terminal": 1 if status in C.TERMINAL_STATUSES else 0,
		}
		for status in all_statuses
	]


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def options(service_category: str | None = None):
	"""Management and lookup options for submitters, grievance officers and admins.

	Returns reference lists for case filing, management, triage, and filtering,
	including departments, lifecycle statuses, categories, and types.

	Args:
	    service_category (str, optional): Filter grievance types by a specific service category (e.g. 'Inputs').

	Returns:
	    departments: Active grievance departments
	    statuses: Grievance lifecycle statuses with metadata
	    service_categories: Active service categories
	    grievance_types: Active grievance types (optionally filtered by service_category)
	    submission_channels: Active intake channels
	"""
	departments = frappe.get_all(
		"Grievance Department",
		filters={"active": 1},
		fields=["name as department_id", "dept_name as department_name", "email_account", "head_of_dept"],
		order_by="dept_name asc",
		ignore_permissions=True,
	)

	service_categories = frappe.get_all(
		"Grievance Service Category",
		filters={"is_active": 1},
		fields=["name as category_name", "code", "sort_order"],
		order_by="sort_order asc, name asc",
		ignore_permissions=True,
	)

	gtype_filters = [["is_active", "=", 1]]
	if service_category:
		gtype_filters.append(["service_category", "=", service_category])

	grievance_types = frappe.get_all(
		"Grievance Type",
		filters=gtype_filters,
		fields=["name as grievance_type_id", "type_name", "service_category"],
		order_by="type_name asc",
		ignore_permissions=True,
	)

	data = {
		"departments": departments,
		"statuses": get_status_options(),
		"service_categories": service_categories,
		"grievance_types": grievance_types,
		"submission_channels": list(CHANNELS),
	}

	return success_response(data=data, message=_("Grievance options fetched successfully"))
