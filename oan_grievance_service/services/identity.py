import re
from dataclasses import dataclass

import frappe
from frappe import _
from oan_auth_service.api.utils import validate_mobile, validate_phone_string
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

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


class GrievanceSubmissionPayload(BaseModel):
	model_config = {"extra": "allow"}

	contact_mobile: str | None = None
	description: str | None = Field(default=None, min_length=MIN_DESCRIPTION_LENGTH)
	service_category: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None

	@field_validator("contact_mobile")
	@classmethod
	def _validate_mobile(cls, v):
		if not v or not str(v).strip():
			return v
		raw = str(v).strip()
		try:
			validate_mobile(raw)
		except (frappe.ValidationError, Exception) as exc:
			message = str(exc.args[0]) if isinstance(exc.args, tuple) and exc.args else str(exc)
			raise ValueError(message) from exc
		return raw

	@field_validator("administrative_area")
	@classmethod
	def _validate_area(cls, v):
		if not v or not str(v).strip():
			return v
		area = str(v).strip()
		if frappe.db.exists("Grievance Administrative Area", area):
			try:
				validate_filing_area(area)
			except frappe.ValidationError as exc:
				message = str(exc.args[0]) if isinstance(exc.args, tuple) and exc.args else str(exc)
				raise ValueError(message) from exc
		return area

	@model_validator(mode="after")
	def _validate_category_and_type(self):
		cat = (self.service_category or "").strip()
		g_type = (self.grievance_type or "").strip()
		if cat and g_type and frappe.db.exists("Grievance Type", g_type):
			parent = frappe.db.get_value("Grievance Type", g_type, "service_category")
			if parent != cat:
				raise ValueError(
					_("Grievance type {0} belongs to category {1}, not {2}.").format(
						frappe.bold(g_type),
						frappe.bold(parent),
						frappe.bold(cat),
					)
				)
		return self


def validate_submission_payload(payload):
	"""Domain rules covered via GrievanceSubmissionPayload Pydantic schema."""
	if not isinstance(payload, dict):
		frappe.throw(_("Submission payload must be an object."), title=_("Invalid Payload"))
	GrievanceSubmissionPayload.model_validate(payload)
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


def find_or_create_submitter(payload: dict) -> str | None:
	"""Resolve or create the Submitter Profile a grievance belongs to.

	Used for assisted/walk-in or IVR intakes where an officer files for a citizen
	who does not have a user account. Derives dedupe_key from inputs and finds or
	creates a persistent Submitter Profile.
	"""
	mobile = payload.get("contact_mobile")
	submitter_type = payload.get("submitter_type") or "Individual Farmer"

	dedupe_key = derive_dedupe_key(
		submitter_type=submitter_type,
		mobile=mobile,
		fayda_id=payload.get("fayda_id"),
		registration_number=payload.get("registration_number"),
		dedupe_key=payload.get("dedupe_key"),
	)
	if not dedupe_key and not mobile:
		return None

	if dedupe_key:
		existing = frappe.db.get_value("Grievance Submitter Profile", {"dedupe_key": dedupe_key}, "name")
		if existing:
			return existing

	if mobile:
		existing = frappe.db.get_value("Grievance Submitter Profile", {"contact_mobile": mobile}, "name")
		if existing:
			return existing

	profile = frappe.get_doc(
		{
			"doctype": "Grievance Submitter Profile",
			"submitter_type": submitter_type,
			"submitter_name": payload.get("submitter_name") or "Citizen",
			"contact_mobile": mobile,
			"contact_email": payload.get("contact_email"),
			"administrative_area": payload.get("administrative_area"),
			"administrative_unit": payload.get("administrative_unit"),
			"dedupe_key": dedupe_key,
			"active": 1,
		}
	)
	profile.insert(ignore_permissions=True)
	return profile.name
