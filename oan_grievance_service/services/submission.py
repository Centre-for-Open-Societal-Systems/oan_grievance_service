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

# FSD 3.11.8 fixes the country to Ethiopia. Ethiopian subscriber numbers are nine
# digits after the country code; mobile ranges open with 9 or 7.
COUNTRY_CODE = "251"
MOBILE_PATTERN = re.compile(r"^[79]\d{8}$")

# The prototype's number field defaults to +255, which is Tanzania. Reject it
# explicitly rather than letting it through as an unrecognised prefix, because a
# wrong-but-plausible country code silently sends every SMS to another network.
CONFUSABLE_CODES = {"255": "Tanzania", "254": "Kenya", "252": "Somalia", "249": "Sudan"}


def normalise_mobile(value):
	"""Return a mobile number as +251XXXXXXXXX, or raise.

	Accepts the three forms a submitter actually types: the international form,
	the national form with a leading zero, and the bare subscriber number.
	"""
	raw = re.sub(r"[^\d+]", "", value or "")
	if not raw:
		frappe.throw(_("A contact mobile number is required."), title=_("Missing Mobile"))

	digits = raw.lstrip("+")

	for code, country in CONFUSABLE_CODES.items():
		if digits.startswith(code) and len(digits) > len(code):
			frappe.throw(
				_("+{0} is the country code for {1}. Ethiopian numbers begin +251.").format(code, country),
				title=_("Wrong Country Code"),
			)

	if digits.startswith(COUNTRY_CODE):
		subscriber = digits[len(COUNTRY_CODE) :]
	elif digits.startswith("0"):
		subscriber = digits[1:]
	else:
		subscriber = digits

	if not MOBILE_PATTERN.match(subscriber):
		frappe.throw(
			_(
				"{0} is not a valid Ethiopian mobile number. Expected nine digits "
				"beginning 9 or 7, for example +251911234567."
			).format(value),
			title=_("Invalid Mobile Number"),
		)

	return f"+{COUNTRY_CODE}{subscriber}"


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

	existing = frappe.db.get_value("Submitter Profile", {"contact_mobile": mobile}, "name")
	if existing:
		return existing

	profile = frappe.get_doc(
		{
			"doctype": "Submitter Profile",
			"submitter_type": payload.get("submitter_type"),
			"submitter_name": payload.get("submitter_name"),
			"contact_mobile": mobile,
			"contact_email": payload.get("contact_email"),
			"region": payload.get("region"),
			"zone": payload.get("zone"),
			"woreda": payload.get("woreda"),
			"kebele": payload.get("kebele"),
			"active": 1,
		}
	)
	profile.insert(ignore_permissions=True)
	return profile.name


def attach_draft_files(draft, grievance):
	"""Move the files uploaded against a draft onto the grievance it became.

	Attachments are uploaded while the wizard is still on step 4, before any
	Grievance row exists, so they are parented to the draft and re-parented here.
	The File rows are updated rather than copied: re-uploading would double the
	storage and break any URL the client is already showing.
	"""
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Grievance Draft", "attached_to_name": draft},
		pluck="name",
	)
	for name in files:
		frappe.db.set_value(
			"File",
			name,
			{"attached_to_doctype": "Grievance", "attached_to_name": grievance},
			update_modified=False,
		)
	return len(files)


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
