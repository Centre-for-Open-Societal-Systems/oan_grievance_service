import re
from dataclasses import dataclass

import frappe
from frappe import _
from pydantic import ValidationError as PydanticValidationError

from oan_grievance_service.services import submission

# Identity schemes, matching the dedupe_key prefixes.
SCHEME_FAYDA = "fayda"
SCHEME_ORG = "org"
SCHEME_PHONE = "phone"

# Fields every submitter needs regardless of type.
# Identity-scheme fields (fayda_id / registration_number) live on the submitter
# profile's dedupe_key, not on the Grievance DocType — they are never required
# at grievance submission time.
COMMON_REQUIRED = ("submitter_name", "contact_mobile")
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
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
FAYDA_PATTERN = re.compile(r"^(?=.{6,30}$)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
REGISTRATION_PATTERN = re.compile(r"^(?=.{3,60}$)[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*$")

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


def validate_submission_payload(payload, allowed_channels=None):
	"""Enforce the server-side rules for a grievance submission payload.

	This is the guardrail that makes direct API calls observe the same business
	validation as the UI. Field-level failures are collected and raised as a
	PydanticValidationError so `@handle_api_errors` can return a details map.
	"""
	if not isinstance(payload, dict):
		frappe.throw(_("Submission payload must be an object."), title=_("Invalid Payload"))

	errors: list[dict] = []

	submitter_type = (payload.get("submitter_type") or "").strip()
	if not submitter_type:
		errors.append(_field_error("submitter_type", _("Submitter type is required.")))
		_raise_field_errors(errors)

	rule = rule_for(submitter_type)
	if not rule:
		errors.append(
			_field_error(
				"submitter_type",
				_("Submitter type '{0}' is not supported.").format(submitter_type),
				input_value=submitter_type,
			)
		)
		_raise_field_errors(errors)

	required_fields = tuple(
		dict.fromkeys((*required_fields_for(submitter_type), *SUBMISSION_REQUIRED_FIELDS))
	)
	for field in required_fields:
		if not (payload.get(field) or "").strip():
			label = frappe.unscrub(field).replace("_", " ")
			errors.append(_field_error(field, _("{0} is required.").format(label)))

	contact_mobile = (payload.get("contact_mobile") or "").strip()
	if contact_mobile:
		try:
			submission.normalise_mobile(contact_mobile)
		except frappe.ValidationError as exc:
			message = str(exc)
			if isinstance(exc.args, tuple) and exc.args:
				# frappe.throw stores the translated message in args[0]
				message = str(exc.args[0])
			errors.append(_field_error("contact_mobile", message, input_value=contact_mobile))

	submission_channel = (payload.get("submission_channel") or "").strip()
	if allowed_channels is not None and submission_channel:
		if submission_channel not in allowed_channels:
			errors.append(
				_field_error(
					"submission_channel",
					_("Submission channel '{0}' is not valid.").format(submission_channel),
					input_value=submission_channel,
				)
			)

	contact_email = (payload.get("contact_email") or "").strip()
	if contact_email and not EMAIL_PATTERN.match(contact_email):
		errors.append(
			_field_error(
				"contact_email",
				_("Contact email '{0}' is not valid.").format(contact_email),
				input_value=contact_email,
			)
		)

	# Optional identity fields — validated for format only when supplied.
	fayda_id = (payload.get("fayda_id") or "").strip()
	if fayda_id and not FAYDA_PATTERN.match(fayda_id):
		errors.append(
			_field_error(
				"fayda_id",
				_("Fayda ID '{0}' is not in a valid format.").format(fayda_id),
				input_value=fayda_id,
			)
		)

	registration_number = (payload.get("registration_number") or "").strip()
	if registration_number and not REGISTRATION_PATTERN.match(registration_number):
		errors.append(
			_field_error(
				"registration_number",
				_("Registration number '{0}' is not in a valid format.").format(registration_number),
				input_value=registration_number,
			)
		)

	description = (payload.get("description") or "").strip()
	if description and len(description) < 20:
		errors.append(
			_field_error(
				"description",
				_("Description must be at least {0} characters.").format(20),
				input_value=description,
			)
		)

	service_category = (payload.get("service_category") or "").strip()
	grievance_type = (payload.get("grievance_type") or "").strip()
	if service_category:
		allowed_categories = {"Inputs", "Schemes", "Payments", "Credit", "Markets"}
		if service_category not in allowed_categories:
			errors.append(
				_field_error(
					"service_category",
					_("Service category '{0}' is not valid.").format(service_category),
					input_value=service_category,
				)
			)
	if service_category and grievance_type:
		if not frappe.db.exists("Grievance Type", grievance_type):
			errors.append(
				_field_error(
					"grievance_type",
					_("Grievance type '{0}' does not exist.").format(grievance_type),
					input_value=grievance_type,
				)
			)
		else:
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
	if administrative_area:
		if not frappe.db.exists("Grievance Administrative Area", administrative_area):
			errors.append(
				_field_error(
					"administrative_area",
					_("Administrative area '{0}' does not exist.").format(administrative_area),
					input_value=administrative_area,
				)
			)
		else:
			area = frappe.get_doc("Grievance Administrative Area", administrative_area)
			if area.level_name not in ALLOWED_FILING_LEVELS or (
				not area.parent_administrative_area and area.is_group
			):
				errors.append(
					_field_error(
						"administrative_area",
						_(
							"Grievances cannot be attached to administrative level '{0}'. "
							"Please select an operational area such as a Woreda or Kebele."
						).format(area.level_name or _("Unknown")),
						input_value=administrative_area,
					)
				)
			elif area.valid_to and str(area.valid_to) <= frappe.utils.today():
				errors.append(
					_field_error(
						"administrative_area",
						_("The selected Administrative Area '{0}' has been dissolved or reorganized.").format(
							administrative_area
						),
						input_value=administrative_area,
					)
				)

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
	"""
	raw_key = (dedupe_key or "").strip()
	rule = rule_for(submitter_type)
	allowed_schemes = rule.schemes if rule else (SCHEME_FAYDA, SCHEME_ORG, SCHEME_PHONE)

	if raw_key:
		if ":" in raw_key:
			return raw_key
		default_scheme = allowed_schemes[0] if allowed_schemes else SCHEME_PHONE
		return f"{default_scheme}:{raw_key}"

	fayda = (fayda_id or national_id or "").strip()
	org = (registration_number or org_number or "").strip()
	phone = (mobile or "").strip()

	# 1. If type accepts SCHEME_ORG:
	if SCHEME_ORG in allowed_schemes:
		if org:
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
		return f"{SCHEME_FAYDA}:{fayda}"

	# 3. If SCHEME_PHONE is acceptable, fallback to mobile phone
	if SCHEME_PHONE in allowed_schemes and phone:
		return f"{SCHEME_PHONE}:{phone}"

	return None
