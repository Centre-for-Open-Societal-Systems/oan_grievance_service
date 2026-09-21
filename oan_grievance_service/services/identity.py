import re
from dataclasses import dataclass

import frappe
from frappe import _
from oan_auth_service.api.utils import validate_phone_string
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.services import submission

# Identity schemes, matching the dedupe_key prefixes.
SCHEME_FAYDA = "fayda"
SCHEME_ORG = "org"
SCHEME_PHONE = "phone"

# Fields every submitter needs regardless of type (profile registration, not grievance).
COMMON_REQUIRED = ("submitter_name", "contact_mobile")

# Format rules for profile/dedupe identity values — not Grievance DocType fields.
FAYDA_PATTERN = re.compile(r"^(?=.{6,30}$)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
REGISTRATION_PATTERN = re.compile(r"^(?=.{3,60}$)[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*$")

MIN_DESCRIPTION_LENGTH = 20

# Operational levels that may own a grievance. Macro containers (Country/Region/Zone)
# are rejected even when is_group=0; Woreda may be is_group=1 when it has child kebeles.
ALLOWED_FILING_LEVELS = frozenset(
	{
		"Woreda",
		"Kebele",
		"Village",
		"Ward",
		"Taluka",
		"Sub-County",
		"County",
		"District",
	}
)


@dataclass(frozen=True)
class IdentityRule:
	"""Registration and identity requirements for one submitter type."""

	schemes: tuple
	"""Acceptable dedupe schemes."""

	required: tuple
	"""Fields that must be present before the profile can be saved."""


SUBMITTER_TYPE_RULES = {
	"Individual Farmer": IdentityRule(
		schemes=(SCHEME_FAYDA, SCHEME_PHONE),
		required=COMMON_REQUIRED,
	),
	"Development Agent": IdentityRule(
		schemes=(SCHEME_FAYDA, SCHEME_PHONE),
		required=COMMON_REQUIRED,
	),
	"Cooperative": IdentityRule(
		schemes=(SCHEME_ORG,),
		required=COMMON_REQUIRED,
	),
	"FPO": IdentityRule(
		schemes=(SCHEME_ORG,),
		required=COMMON_REQUIRED,
	),
	"NGO": IdentityRule(
		schemes=(SCHEME_ORG,),
		required=COMMON_REQUIRED,
	),
	"Woreda/Kebele Body": IdentityRule(
		schemes=(SCHEME_ORG,),
		required=COMMON_REQUIRED,
	),
}


def rule_for(submitter_type):
	"""The rule for a type, or None if the type is unknown to this map."""
	return SUBMITTER_TYPE_RULES.get(submitter_type)


def required_fields_for(submitter_type: str | None):
	"""Return the required fields for a submitter type (common baseline only).

	Scheme-specific identity (fayda_id / registration_number) is owned by the
	submitter profile dedupe_key and is not required on grievance submission.
	"""
	rule = rule_for(submitter_type)
	if rule:
		return tuple(rule.required)
	return tuple(COMMON_REQUIRED)


def _field_error(field: str, message: str, *, input_value=None):
	"""Build a pydantic-compatible error detail for one field."""
	return {
		"type": "value_error",
		"loc": (field,),
		"input": input_value,
		"ctx": {"error": ValueError(message)},
	}


def _raise_field_errors(errors: list[dict]):
	"""Raise so handle_api_errors can return a per-field details map."""
	if not errors:
		return
	raise PydanticValidationError.from_exception_data("SubmissionPayload", errors)


def _validate_fayda_id(fayda_id: str):
	if not FAYDA_PATTERN.match(fayda_id):
		frappe.throw(
			_("Fayda ID '{0}' is not in a valid format.").format(fayda_id),
			title=_("Invalid Fayda ID"),
		)


def _validate_registration_number(registration_number: str):
	if not REGISTRATION_PATTERN.match(registration_number):
		frappe.throw(
			_("Registration number '{0}' is not in a valid format.").format(registration_number),
			title=_("Invalid Registration Number"),
		)


def validate_filing_area(administrative_area: str):
	"""Domain rules for where a grievance may be filed.

	Link existence is Frappe's job; this enforces filing level and dissolved dates.
	"""
	area = frappe.get_doc("Grievance Administrative Area", administrative_area)
	if area.level_name not in ALLOWED_FILING_LEVELS or (
		not area.parent_administrative_area and area.is_group
	):
		frappe.throw(
			_(
				"Grievances cannot be attached to administrative level '{0}'. "
				"Please select an operational area such as a Woreda or Kebele."
			).format(area.level_name or _("Unknown")),
			title=_("Invalid Administrative Area"),
		)
	if area.valid_to and str(area.valid_to) <= frappe.utils.today():
		frappe.throw(
			_("The selected Administrative Area '{0}' has been dissolved or reorganized.").format(
				administrative_area
			),
			title=_("Dissolved Administrative Area"),
		)
	return area


# Fields that must be present on the resolved API submit payload (STG-321 / STG-328).
# Checked here — not only by DocType MandatoryError — so the client gets a per-field
# `details` map. Desk / DocType `validate` passes a subset and leaves presence to Frappe.
SUBMISSION_REQUIRED_FIELDS = (
	"submitter_type",
	"submitter_name",
	"contact_mobile",
	"submission_channel",
	"administrative_area",
	"service_category",
	"grievance_type",
	"description",
)


def validate_required_submission_fields(payload):
	"""Raise per-field errors when resolved submit fields are still missing.

	Call after identity and area resolution so profile-filled and woreda/kebele
	values are visible. Consent is included: `record_consent` would otherwise
	throw a single title-only error without a field key.
	"""
	if not isinstance(payload, dict):
		frappe.throw(_("Submission payload must be an object."), title=_("Invalid Payload"))

	errors: list[dict] = []
	for field in SUBMISSION_REQUIRED_FIELDS:
		value = payload.get(field)
		if value is None or (isinstance(value, str) and not str(value).strip()):
			errors.append(_field_error(field, _("This field is required."), input_value=value))

	if not payload.get("consent_given"):
		errors.append(
			_field_error(
				"consent_given",
				_("The submitter must consent to the processing of their personal data."),
				input_value=payload.get("consent_given"),
			)
		)

	_raise_field_errors(errors)
	return True


def validate_submission_payload(payload, *, require_presence: bool = False):
	"""Domain rules Frappe reqd / Link / Select do not cover.

	Required fields, Link targets, and Select options are enforced by the Grievance
	DocType (and by `@validate_request` at the API edge). This function only adds:
	- Ethiopian mobile normalisation + shared auth phone shape check
	- Description minimum length (FSD 3.2.2)
	- Grievance type belonging to the chosen service category
	- Filing-level / dissolved administrative area rules

	When `require_presence` is True (API submit path), also emit per-field errors
	for missing required fields and consent (STG-328).
	"""
	if not isinstance(payload, dict):
		frappe.throw(_("Submission payload must be an object."), title=_("Invalid Payload"))

	if require_presence:
		validate_required_submission_fields(payload)

	errors: list[dict] = []

	contact_mobile = (payload.get("contact_mobile") or "").strip()
	if contact_mobile:
		try:
			# Ethiopia-specific canonical form first (bare 9-digit / 0-prefix),
			# then shared auth phone shape check on the +251… result.
			canonical_mobile = submission.normalise_mobile(contact_mobile)
			validate_phone_string(canonical_mobile)
		except frappe.ValidationError as exc:
			message = str(exc.args[0]) if isinstance(exc.args, tuple) and exc.args else str(exc)
			errors.append(_field_error("contact_mobile", message, input_value=contact_mobile))
		except ValueError as exc:
			errors.append(_field_error("contact_mobile", str(exc), input_value=contact_mobile))

	description = (payload.get("description") or "").strip()
	if description and len(description) < MIN_DESCRIPTION_LENGTH:
		errors.append(
			_field_error(
				"description",
				_("Description must be at least {0} characters.").format(MIN_DESCRIPTION_LENGTH),
				input_value=description,
			)
		)

	service_category = (payload.get("service_category") or "").strip()
	grievance_type = (payload.get("grievance_type") or "").strip()
	# Category membership is the DocType source of truth (Link). Only check the
	# cross-field rule that Link alone cannot express.
	if service_category and grievance_type and frappe.db.exists("Grievance Type", grievance_type):
		parent = frappe.db.get_value("Grievance Type", grievance_type, "service_category")
		if parent != service_category:
			errors.append(
				_field_error(
					"grievance_type",
					_("Grievance type {0} belongs to category {1}, not {2}.").format(
						frappe.bold(grievance_type),
						frappe.bold(parent),
						frappe.bold(service_category),
					),
					input_value=grievance_type,
				)
			)

	administrative_area = (payload.get("administrative_area") or "").strip()
	if administrative_area and frappe.db.exists("Grievance Administrative Area", administrative_area):
		try:
			validate_filing_area(administrative_area)
		except frappe.ValidationError as exc:
			message = str(exc.args[0]) if isinstance(exc.args, tuple) and exc.args else str(exc)
			errors.append(_field_error("administrative_area", message, input_value=administrative_area))

	_raise_field_errors(errors)
	return True


def derive_dedupe_key(
	submitter_type: str,
	mobile: str | None = None,
	fayda_id: str | None = None,
	national_id: str | None = None,
	registration_number: str | None = None,
	org_number: str | None = None,
	farmer_id: str | None = None,
	dedupe_key: str | None = None,
) -> str | None:
	"""Automatically derive the canonical scheme-prefixed dedupe key from user inputs.

	Driven directly by the allowed schemes in SUBMITTER_TYPE_RULES:
	- If raw dedupe_key is provided with scheme -> preserves it.
	- If type accepts SCHEME_ORG -> uses registration_number (required if only SCHEME_ORG).
	- If type accepts SCHEME_FAYDA -> uses fayda_id if provided.
	- If type accepts SCHEME_PHONE -> falls back to mobile phone.

	Fayda / registration format checks live here (profile identity), not on grievance submit.
	"""
	raw_key = (dedupe_key or "").strip()
	rule = rule_for(submitter_type)
	allowed_schemes = rule.schemes if rule else (SCHEME_FAYDA, SCHEME_ORG, SCHEME_PHONE)

	if raw_key:
		if ":" in raw_key:
			scheme, _sep, value = raw_key.partition(":")
			if scheme == SCHEME_FAYDA and value:
				_validate_fayda_id(value)
			elif scheme == SCHEME_ORG and value:
				_validate_registration_number(value)
			return raw_key
		default_scheme = allowed_schemes[0] if allowed_schemes else SCHEME_PHONE
		return f"{default_scheme}:{raw_key}"

	fayda = (fayda_id or national_id or "").strip()
	org = (registration_number or org_number or "").strip()
	phone = (mobile or "").strip()

	# 1. If type accepts SCHEME_ORG:
	if SCHEME_ORG in allowed_schemes:
		if org:
			_validate_registration_number(org)
			return f"{SCHEME_ORG}:{org}"
		# If this type strictly only accepts SCHEME_ORG (e.g. organizations)
		if allowed_schemes == (SCHEME_ORG,):
			frappe.throw(
				_("An official Organization Registration / Certificate Number is required for {0}.").format(
					submitter_type
				),
				frappe.ValidationError,
			)

	# 2. If type accepts SCHEME_FAYDA and fayda ID was provided:
	if SCHEME_FAYDA in allowed_schemes and fayda:
		_validate_fayda_id(fayda)
		return f"{SCHEME_FAYDA}:{fayda}"

	# 3. If SCHEME_PHONE is acceptable, fallback to mobile phone
	if SCHEME_PHONE in allowed_schemes and phone:
		return f"{SCHEME_PHONE}:{phone}"

	return None
