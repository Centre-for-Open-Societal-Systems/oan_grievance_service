"""Filing a grievance: what follows the insert, and the intake that needs only a phone number.

`record_submission` and `dispatch_filed` are the two halves every channel shares around
`doc.insert` (the first timeline entry; then notification, duplicate flagging and routing).
`file_from_ivr` is the IVR webhook's whole filing: the caller's profile, the case and what follows.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_submitter_profile.grievance_submitter_profile import (
	build_dedupe_key,
)
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import duplicates, identity, notifications, routing

PROFILE = "Grievance Submitter Profile"


def record_submission(doc):
	"""The timeline entry that opens a filed case, written by whoever filed it."""
	submitter_user = frappe.db.get_value(PROFILE, doc.submitter, "user") if doc.submitter else None
	return GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="submission",
		is_internal=False,
		body=(doc.description or "").strip() or f"Grievance submitted ({doc.ticket_number})",
		author_user=doc.assisted_by_officer
		or submitter_user
		or (frappe.session.user if frappe.session.user != "Guest" else None),
		author_type="submitter",
		ref_doctype="Grievance",
		ref_docname=doc.name,
	)


def dispatch_filed(doc, flag_duplicates=True):
	"""Tell the submitter, flag look-alikes and queue routing, once the case is in."""
	notifications.queue(doc, C.EVENT_SUBMISSION_RECEIVED)
	# Flags only; an officer rules on it. The submitter is told nothing until then.
	if flag_duplicates:
		duplicates.enqueue_flagging(doc.name)
	routing.enqueue_routing(doc.name)


def file_from_ivr(
	phone_number: str,
	country_code: str | None = None,
	call_id: str | None = None,
	description: str | None = None,
	uploads: list[dict] | None = None,
	consent_given: bool = True,
):
	"""File a grievance for an IVR caller from nothing but their number.

	The caller gets an Individual Farmer profile on first contact, keyed on the phone
	number; a later call from the same number files under the same profile. Fayda ID,
	area and every other detail stay empty until the farmer or an officer supplies them.
	The category and type are the default ones; the message is a fixed note unless the
	IVR platform sends one. Consent is recorded as given unless the platform says otherwise.

	`call_id` makes a retried webhook file once: a second delivery returns the case the
	first one made. `uploads` are the call's files, already checked by `prepare_uploads`, and
	are stored on the opening timeline entry. Returns `(grievance, submission, attachments)`:
	the timeline entry this call wrote and the files stored, or None and [] for a replayed delivery.
	"""
	from oan_auth_service.api.utils import assemble_phone_number

	from oan_grievance_service.api.v1.attachment import store_uploads
	from oan_grievance_service.grievance_masters.doctype.grievance_service_category.grievance_service_category import (
		get_default_category,
	)
	from oan_grievance_service.grievance_masters.doctype.grievance_type.grievance_type import (
		get_fallback_type,
	)

	mobile = identity.validate_mobile(assemble_phone_number(phone_number, country_code=country_code))

	# Namespaced so a call id can never collide with another channel's submission uuid.
	submission_uuid = f"ivr:{call_id}" if call_id else frappe.generate_hash(length=32)

	category = get_default_category()
	if not category:
		frappe.throw(
			_("No default service category is set, so an IVR grievance cannot be classified."),
			title=_("Default Category Missing"),
		)

	profile = _caller_profile(mobile)
	if profile.is_blocked or not profile.active:
		frappe.throw(_("This submitter profile cannot file new grievances."), title=_("Submitter Blocked"))

	doc = frappe.new_doc("Grievance")
	doc.update(
		{
			"client_submission_uuid": submission_uuid,
			"submission_channel": C.CHANNEL_IVR,
			"consent_given": 1 if consent_given else 0,
			"consent_recorded_at": now_datetime() if consent_given else None,
			"service_category": category,
			"grievance_type": get_fallback_type(category),
			"description": (description or "").strip() or C.IVR_DEFAULT_DESCRIPTION,
			"submitter": profile.name,
			"submitter_type": profile.submitter_type,
			"submitter_name": profile.submitter_name,
			"contact_mobile": profile.contact_mobile,
			"contact_email": profile.contact_email,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# The unique client_submission_uuid is the replay check: this call id is already filed.
		frappe.db.rollback()
		existing = frappe.db.get_value("Grievance", {"client_submission_uuid": submission_uuid}, "name")
		if not existing:
			raise
		return frappe.get_doc("Grievance", existing), None, []
	submission = record_submission(doc)
	attachments = store_uploads(doc, uploads, timeline_entry=submission.name) if uploads else []
	# Every call without a description carries the same note, so look-alike scoring would
	# flag each repeat call from one number.
	dispatch_filed(doc, flag_duplicates=bool((description or "").strip()))
	return doc, submission, attachments


def _caller_profile(mobile: str):
	"""The Individual Farmer profile of this number, created with the caller's number alone.

	An existing profile is found by its phone key, or by the number on a profile keyed on a
	Fayda ID, so a farmer who registered with eSignet is not given a second profile.
	"""
	from oan_grievance_service.api.v1.submitter import _create_or_update_submitter_profile

	filters = {"dedupe_key": build_dedupe_key(identity.SCHEME_PHONE, mobile)}
	name = frappe.db.get_value(PROFILE, filters, "name") or frappe.db.get_value(
		PROFILE,
		{"contact_mobile": mobile, "submitter_type": "Individual Farmer"},
		"name",
		# A usable profile wins over a blocked one, but a number whose only profile is blocked
		# stays blocked instead of being handed a fresh profile.
		order_by="active desc, is_blocked asc, creation asc, name asc",
	)
	if not name:
		try:
			return _create_or_update_submitter_profile(
				user=None,
				submitter_type="Individual Farmer",
				submitter_name=C.IVR_CALLER_NAME,
				contact_mobile=mobile,
				submission_channel=C.CHANNEL_IVR,
			)
		except frappe.DuplicateEntryError:
			# A second call from the same number landed between the lookup and the insert.
			name = frappe.db.get_value(PROFILE, filters, "name")
	return frappe.get_doc(PROFILE, name)
