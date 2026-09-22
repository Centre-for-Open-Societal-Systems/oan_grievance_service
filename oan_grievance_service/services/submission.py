"""Support for FR-02 submission: contact normalisation, submitter identity, the
location snapshot and draft attachments.

These live behind the API rather than inside the Grievance controller because the
same rules apply to a draft that is not yet a Grievance, and to the submitter
profile that outlives any single case.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import now_datetime

# Bare / national numbers (no +ISD) are parsed against Ethiopia — primary
# jurisdiction and the historical FSD default. International numbers carry their
# own country code and are validated by that country's numbering plan.
DEFAULT_PHONE_REGION = "ET"


def normalise_mobile(value, default_region: str | None = None):
	"""Return an E.164 mobile number validated by libphonenumber, or raise.

	Uses Frappe's shipped `phonenumbers` (Google libphonenumber). Accepts
	international (+ISD…), national-with-leading-zero, and bare subscriber forms.
	Numbers without a country code use `default_region` (Ethiopia by default).
	The region must also appear in jurisdiction phone extensions when those are
	configured, so a plausible neighbouring ISD cannot slip SMS onto another network.
	"""
	from phonenumbers import (
		NumberParseException,
		PhoneNumberFormat,
		format_number,
		is_valid_number,
		parse,
		region_code_for_number,
	)

	raw = (value or "").strip()
	if not raw:
		frappe.throw(_("A contact mobile number is required."), title=_("Missing Mobile"))

	# Keep a leading +; drop spaces, hyphens, and desk-style separators.
	candidate = re.sub(r"[^\d+]", "", raw)
	if candidate.count("+") > 1 or (candidate and "+" in candidate[1:]):
		frappe.throw(
			_("{0} is not a valid phone number.").format(value),
			title=_("Invalid Mobile Number"),
			exc=frappe.InvalidPhoneNumberError,
		)

	region = (default_region or DEFAULT_PHONE_REGION).upper()
	try:
		# International form needs no default region; national form does.
		parsed = parse(candidate, None if candidate.startswith("+") else region)
	except NumberParseException as exc:
		if exc.error_type == NumberParseException.INVALID_COUNTRY_CODE:
			frappe.throw(
				_("Please include a country code, for example +251…."),
				title=_("Country Code Required"),
				exc=frappe.InvalidPhoneNumberError,
			)
		frappe.throw(
			_("{0} is not a valid phone number.").format(value),
			title=_("Invalid Mobile Number"),
			exc=frappe.InvalidPhoneNumberError,
		)

	if not is_valid_number(parsed):
		frappe.throw(
			_("{0} is not a valid phone number for its country.").format(value),
			title=_("Invalid Mobile Number"),
			exc=frappe.InvalidPhoneNumberError,
		)

	number_region = region_code_for_number(parsed)
	allowed = _jurisdiction_phone_regions()
	if allowed and number_region and number_region not in allowed:
		frappe.throw(
			_(
				"{0} belongs to a country outside the active jurisdiction. "
				"Use a number from: {1}."
			).format(value, ", ".join(sorted(allowed))),
			title=_("Wrong Country Code"),
			exc=frappe.InvalidPhoneNumberError,
		)

	return format_number(parsed, PhoneNumberFormat.E164)


def _jurisdiction_phone_regions() -> set[str]:
	"""ISO region codes from Administrative Area jurisdictions (phone_extensions)."""
	from oan_grievance_service.api.v1.submitter import get_phone_extensions

	return {ext["code"] for ext in get_phone_extensions() if ext.get("code")}


def find_or_create_submitter(payload):
	"""Resolve the Submitter Profile a grievance belongs to.

	FR-02 duplicate detection matches on the submitter, so a grievance without one
	can never be found to duplicate anything. Identity is the normalised mobile
	number: it is the one field present on every channel including IVR, where
	there is no account and no email.
	"""
	mobile = payload.get("contact_mobile")
	if not mobile:
		return None

	existing = frappe.db.get_value("Grievance Submitter Profile", {"contact_mobile": mobile}, "name")
	if existing:
		return existing

	profile = frappe.get_doc(
		{
			"doctype": "Grievance Submitter Profile",
			"submitter_type": payload.get("submitter_type"),
			"submitter_name": payload.get("submitter_name"),
			"contact_mobile": mobile,
			"contact_email": payload.get("contact_email"),
			# The four-level region/zone/woreda/kebele columns were replaced by a
			# single link into the Administrative Area tree. Frappe drops unknown
			# keys silently, so passing the old names looked like it worked and
			# left every new profile with no location at all.
			"administrative_area": payload.get("administrative_area"),
			"active": 1,
		}
	)
	profile.insert(ignore_permissions=True)
	return profile.name


def attach_draft_files(draft, grievance):
	"""Hand the draft's evidence to the case it became.

	The attachment rows are re-pointed, not rebuilt. Rebuilding them is what lost
	the metadata: the row was reconstructed from the File, which knows a name, a
	size and an MD5 -- so `checksum_sha256` was filled with a hash that is not one,
	`mime_type` came out null, and whatever the submitter had labelled the document
	was dropped. The bytes are only in front of us once, at upload; everything
	derived from them is recorded there and simply travels with the row.

	The File objects do not move at all. They are attached to the attachment row,
	which is what makes core's private-file permission check consult the scan
	verdict, and that row keeps its name across the change of owner.
	"""
	rows = frappe.get_all(
		"Grievance Attachment",
		filters={"draft": draft},
		fields=["name", "uploaded_by_user", "uploaded_by_submitter"],
	)
	submitter = frappe.db.get_value("Grievance", grievance, "submitter")

	for row in rows:
		values = {"grievance": grievance, "draft": None}
		# A guest's upload carried no uploader, because there was no one to name.
		# The case has an owner now, and FR-10 wants every file attributable.
		if not (row.uploaded_by_user or row.uploaded_by_submitter):
			values["uploaded_by_submitter"] = submitter
		frappe.db.set_value("Grievance Attachment", row.name, values, update_modified=False)

	return len(rows)


def record_consent(doc):
	"""FSD 9: consent is mandatory and its time of capture is part of the record."""
	if not doc.consent_given:
		frappe.throw(
			_("The submitter must consent to the processing of their personal data."),
			title=_("Consent Required"),
		)
	if not doc.consent_recorded_at:
		doc.consent_recorded_at = now_datetime()


def parse_payload(payload):
	"""Draft payloads arrive as a JSON string over HTTP and as a dict in tests."""
	if isinstance(payload, dict):
		return payload
	if not payload:
		return {}
	try:
		parsed = json.loads(payload)
	except (TypeError, ValueError):
		frappe.throw(_("Draft payload is not valid JSON."), title=_("Malformed Draft"))
	if not isinstance(parsed, dict):
		frappe.throw(_("Draft payload must be an object."), title=_("Malformed Draft"))
	return parsed


# Wizard / case field names shared by draft.payload and POST /api/v1/grievances.
# Keys are never renamed on save or on submit merge — only the envelope differs
# (draft wraps them in `payload`; submit sends them at the top level).
SHARED_SUBMISSION_FIELD_KEYS = (
	"submitter_type",
	"submitter_name",
	"contact_mobile",
	"contact_email",
	"submission_channel",
	"administrative_area",
	"administrative_unit",
	"woreda",
	"kebele",
	"service_category",
	"grievance_type",
	"description",
	"desired_outcome",
	"consent_given",
	"is_anonymous",
	"assisted_by_officer",
	"client_submission_uuid",
)


def merge_draft_into_submission(request_kwargs):
	"""Carry a saved draft's wizard state into the final submit payload (STG-328).

	Draft `payload` keys are copied as-is (same names as submit body fields).
	Non-null request values win so the client can correct a field on the review
	step without re-saving the draft. Returns `(merged_kwargs, already_submitted)`
	where `already_submitted` is the grievance name the draft became, or None.

	Ownership matches the draft API: a claimed draft is only usable by its owner.
	A missing draft is a no-op so `client_uuid` can still be sent for attachment
	claiming when the wizard state was already in the request body.
	"""
	kwargs = dict(request_kwargs or {})
	client_uuid = kwargs.get("client_uuid")
	if not client_uuid:
		return kwargs, None

	draft_name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	if not draft_name:
		return kwargs, None

	draft = frappe.get_doc("Grievance Draft", draft_name)
	_assert_draft_owner(draft)

	if draft.submitted_as:
		return kwargs, draft.submitted_as

	payload = parse_payload(draft.payload)
	# Shallow merge only — do not rename or remap keys.
	merged = {**payload}
	for key, value in kwargs.items():
		if value is not None:
			merged[key] = value
	merged["client_uuid"] = client_uuid
	return merged, None


def _assert_draft_owner(draft):
	"""Same rule as api.v1.draft: claimed drafts stay with their owner."""
	if not draft.owner_user:
		return
	user = frappe.session.user
	if user in (None, "Guest") or user != draft.owner_user:
		frappe.throw(
			_("This draft belongs to another user."),
			frappe.PermissionError,
			title=_("Forbidden"),
		)
