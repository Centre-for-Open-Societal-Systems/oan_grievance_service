"""Submitter profile registration, options and management endpoints."""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	SafeEmail,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1._options import (
	get_grievance_types,
	get_identity_schemes,
	get_phone_extensions,
	get_preferred_languages,
	get_service_categories,
	get_submission_types,
	get_submitter_types,
)
from oan_grievance_service.grievance_management.doctype.grievance.grievance import (
	MIN_DESCRIPTION_LENGTH,
)
from oan_grievance_service.grievance_masters.doctype.grievance_submitter_profile.grievance_submitter_profile import (
	split_dedupe_key,
)
from oan_grievance_service.services.constants import STAFF_ROLES
from oan_grievance_service.services.identity import (
	derive_dedupe_key,
	validate_mobile,
)

route = prefixed("/api/v1/submitters")


class RegisterSubmitterRequest(BaseModel):
	model_config = {"extra": "forbid"}

	submitter_type: str = Field(
		default="Individual Farmer",
		description="Submitter type (e.g. Individual Farmer, Cooperative, Development Agent, etc.)",
	)
	submitter_name: str | None = Field(default=None, description="Submitter name or organization name")
	contact_mobile: str | None = Field(default=None, description="Contact mobile number")
	contact_email: SafeEmail | None = Field(default=None, description="Contact email address")
	administrative_area: str | None = Field(default=None, description="Administrative area Link")
	administrative_unit: str | None = Field(default=None, description="Administrative unit / woreda / branch")
	preferred_language: str | None = Field(default=None, description="Preferred notification language")
	fayda_id: str | None = Field(default=None, description="Fayda National ID (16 digits)")
	national_id: str | None = Field(default=None, description="National ID alias")
	registration_number: str | None = Field(
		default=None, description="Official organization registration/certificate number"
	)
	org_number: str | None = Field(default=None, description="Organization number alias")
	farmer_id: str | None = Field(default=None, description="Farmer ID alias")
	dedupe_key: str | None = Field(default=None, description="Explicit dedupe key")


class BlockSubmitterRequest(BaseModel):
	model_config = {"extra": "forbid"}

	reason: str = Field(..., min_length=1, description="Reason for blocking submitter")


def _is_farmer_submitter_type(submitter_type: str | None) -> bool:
	"""Return True if submitter type represents an Individual Farmer."""
	if not submitter_type:
		return False
	return submitter_type.strip() == "Individual Farmer"


def _create_or_update_submitter_profile(
	user: str | None,
	submitter_type: str = "Individual Farmer",
	submitter_name: str | None = None,
	contact_mobile: str | None = None,
	contact_email: str | None = None,
	administrative_area: str | None = None,
	administrative_unit: str | None = None,
	preferred_language: str | None = None,
	fayda_id: str | None = None,
	national_id: str | None = None,
	registration_number: str | None = None,
	org_number: str | None = None,
	farmer_id: str | None = None,
	dedupe_key: str | None = None,
	**kwargs,
):
	"""Internal helper to validate inputs and persist a Submitter Profile."""
	submitter_type = (submitter_type or "Individual Farmer").strip()

	if not frappe.db.exists("Grievance Submitter Type", submitter_type):
		frappe.throw(
			_("Submitter Type '{0}' does not exist.").format(submitter_type),
			frappe.ValidationError,
			title=_("Invalid Submitter Type"),
		)

	user_doc = (
		frappe.get_doc("User", user) if user and user != "Guest" and frappe.db.exists("User", user) else None
	)

	if not submitter_name and user_doc:
		submitter_name = (
			f"{user_doc.first_name or ''} {user_doc.last_name or ''}".strip()
			or user_doc.full_name
			or user_doc.name
		)
	submitter_name = (submitter_name or "").strip()
	if not submitter_name:
		frappe.throw(_("Submitter Name is required."), frappe.ValidationError, title=_("Missing Name"))

	if not contact_mobile and user_doc:
		contact_mobile = user_doc.mobile_no or ""
	contact_mobile = (contact_mobile or "").strip()
	if not contact_mobile:
		frappe.throw(
			_("Contact mobile number is required."), frappe.ValidationError, title=_("Missing Mobile")
		)

	contact_mobile = validate_mobile(contact_mobile)

	if (
		not contact_email
		and user_doc
		and user_doc.email
		and not user_doc.email.endswith("@id.openagrinet.internal")
	):
		contact_email = user_doc.email
	contact_email = (contact_email or "").strip() or None

	if preferred_language and user_doc and frappe.db.exists("Language", preferred_language):
		user_doc.db_set("language", preferred_language, update_modified=False)

	# Derive dedupe key based on type and identifiers
	derived_key = derive_dedupe_key(
		submitter_type=submitter_type,
		mobile=contact_mobile,
		fayda_id=fayda_id,
		national_id=national_id,
		registration_number=registration_number,
		org_number=org_number,
		farmer_id=farmer_id,
		dedupe_key=dedupe_key,
	)
	if not derived_key:
		frappe.throw(
			_("Submitter registration requires a valid contact phone number or identifier."),
			frappe.ValidationError,
			title=_("Identifier Required"),
		)

	is_farmer = _is_farmer_submitter_type(submitter_type)
	is_blocked = 0 if is_farmer else 1
	blocked_reason = None if is_farmer else _("Pending admin verification for non-farmer submitter type.")

	admin_area = administrative_area or kwargs.get("region")
	if admin_area and frappe.db.exists("Grievance Administrative Area", admin_area):
		admin_area = str(admin_area).strip()
	else:
		admin_area = None

	admin_unit = administrative_unit or kwargs.get("woreda")
	if admin_unit:
		admin_unit = str(admin_unit).strip()

	existing_name = frappe.db.get_value("Grievance Submitter Profile", {"dedupe_key": derived_key}, "name")
	if not existing_name and user_doc:
		existing_name = frappe.db.get_value("Grievance Submitter Profile", {"user": user_doc.name}, "name")

	if existing_name:
		profile = frappe.get_doc("Grievance Submitter Profile", existing_name)
		if user_doc and profile.user and profile.user != user_doc.name:
			frappe.throw(
				_(
					"A Submitter Profile with dedupe key '{0}' is already registered under another account."
				).format(derived_key),
				frappe.DuplicateEntryError,
				title=_("Already Registered"),
			)
		if user_doc:
			profile.user = user_doc.name
		profile.submitter_type = submitter_type
		profile.submitter_name = submitter_name
		profile.contact_mobile = contact_mobile
		if contact_email:
			profile.contact_email = contact_email
		profile.dedupe_key = derived_key
		if admin_area:
			profile.administrative_area = admin_area
		if admin_unit:
			profile.administrative_unit = admin_unit
		profile.active = 1
		if not is_farmer and not profile.is_blocked:
			profile.is_blocked = 1
			profile.blocked_reason = blocked_reason
		profile.save(ignore_permissions=True)
	else:
		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"user": user_doc.name if user_doc else None,
				"submitter_type": submitter_type,
				"submitter_name": submitter_name,
				"contact_mobile": contact_mobile,
				"contact_email": contact_email,
				"dedupe_key": derived_key,
				"administrative_area": admin_area,
				"administrative_unit": admin_unit,
				"active": 1,
				"is_blocked": is_blocked,
				"blocked_reason": blocked_reason,
			}
		)
		profile.insert(ignore_permissions=True)

	return profile


@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"/options",
	methods=("GET",),
	allow_guest=True,
	summary="Dropdown options and reference data for submitters",
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def options(
	search_country: str | None = None,
	country: str | None = None,
	include_phone_extensions: bool | str = True,
	service_category: str | None = None,
):
	"""Dropdown options and reference data needed for submitters, intake and public registration.

	Args:
	    search_country (str, optional): Search term to filter country phone extensions (matches name, ISO code, or ISD).
	    country (str, optional): Exact country name or 2-letter ISO code (e.g. 'ET', 'Ethiopia').
	    include_phone_extensions (bool, optional): Whether to include country phone extensions (default True).
	    service_category (str, optional): Filter grievance types by a specific service category (e.g. 'Inputs').

	Returns:
	    submitter_types: Active submitter types (e.g. Individual Farmer, DA, Cooperative, NGO, etc.)
	    identity_schemes: Supported identity schemes (Fayda, Registration Number, Phone)
	    submission_types: Active intake channels (e.g. Mobile App, Web Portal, etc.)
	    preferred_languages: Supported notification languages
	    phone_extensions: Country phone extensions / dialing prefixes (ISD codes)
	    service_categories: Active service categories (e.g. Inputs, Schemes, Payments, etc.)
	    grievance_types: Active grievance types (optionally filtered by service_category)
	"""
	data = {
		"submitter_types": get_submitter_types(),
		"identity_schemes": get_identity_schemes(),
		"submission_types": get_submission_types(),
		"preferred_languages": get_preferred_languages(),
		"service_categories": get_service_categories(),
		"grievance_types": get_grievance_types(service_category=service_category),
		# The wizard validates the description client-side before it submits; the
		# server rule lives in services.identity and this is the same number.
		"min_description_length": MIN_DESCRIPTION_LENGTH,
	}

	should_include_phones = str(include_phone_extensions).lower() not in ("0", "false", "no")
	if should_include_phones:
		data["phone_extensions"] = get_phone_extensions(search=search_country, country=country)

	return success_response(data=data, message=_("Options fetched successfully"))


@route(
	"/register",
	methods=("POST",),
	summary="Register or update submitter profile for authenticated user",
)
@route(
	"",
	methods=("POST",),
	summary="Register or update submitter profile for authenticated user",
)
@frappe.whitelist()
@validate_request(RegisterSubmitterRequest)
@handle_api_errors
def register_submitter(
	submitter_type: str = "Individual Farmer",
	submitter_name: str | None = None,
	contact_mobile: str | None = None,
	contact_email: str | None = None,
	administrative_area: str | None = None,
	administrative_unit: str | None = None,
	preferred_language: str | None = None,
	fayda_id: str | None = None,
	national_id: str | None = None,
	registration_number: str | None = None,
	org_number: str | None = None,
	farmer_id: str | None = None,
	dedupe_key: str | None = None,
	**kwargs,
):
	"""Register a Submitter Profile for the authenticated user.

	Only creates/updates the Submitter Profile record and links it to the logged-in User.
	Non-farmer submitter types (Cooperative, Development Agent, NGO, FPO, etc.) are registered in
	a blocked state (is_blocked=1) and require admin verification before filing grievances.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(
			_("Authentication is required to register a submitter profile."),
			frappe.PermissionError,
			title=_("Authentication Required"),
		)

	profile = _create_or_update_submitter_profile(
		user=frappe.session.user,
		submitter_type=submitter_type,
		submitter_name=submitter_name,
		contact_mobile=contact_mobile,
		contact_email=contact_email,
		administrative_area=administrative_area,
		administrative_unit=administrative_unit,
		preferred_language=preferred_language,
		fayda_id=fayda_id,
		national_id=national_id,
		registration_number=registration_number,
		org_number=org_number,
		farmer_id=farmer_id,
		dedupe_key=dedupe_key,
	)

	scheme, ident_val = split_dedupe_key(profile.dedupe_key)
	identities = [{"scheme": scheme, "value": ident_val}] if scheme and ident_val else []

	data = {
		"profile_id": profile.name,
		"submitter_type": profile.submitter_type,
		"submitter_name": profile.submitter_name,
		"contact_mobile": profile.contact_mobile,
		"contact_email": profile.contact_email,
		"dedupe_key": profile.dedupe_key,
		"identities": identities,
		"administrative_area": profile.administrative_area,
		"administrative_unit": profile.administrative_unit,
		"active": bool(profile.active),
		"is_blocked": bool(profile.is_blocked),
		"blocked_reason": profile.blocked_reason,
	}

	msg = (
		_("Submitter profile registered successfully.")
		if not profile.is_blocked
		else _(
			"Submitter profile registered. Non-farmer accounts require admin verification before submitting grievances."
		)
	)
	return success_response(data=data, message=msg)


@route(
	"/<profile_id>/unblock",
	methods=("POST",),
	summary="Unblock a submitter profile (Staff/Admin only)",
)
@frappe.whitelist()
@handle_api_errors
@require_role(STAFF_ROLES)
def unblock_submitter(profile_id: str, **kwargs):
	"""Unblock a submitter profile, allowing them to file grievances."""
	if not frappe.db.exists("Grievance Submitter Profile", profile_id):
		frappe.throw(
			_("Submitter Profile '{0}' does not exist.").format(profile_id), frappe.DoesNotExistError
		)

	profile = frappe.get_doc("Grievance Submitter Profile", profile_id)
	profile.db_set({"is_blocked": 0, "blocked_reason": None}, update_modified=False)

	return success_response(
		data={
			"profile_id": profile.name,
			"is_blocked": False,
			"blocked_reason": None,
			"active": bool(profile.active),
		},
		message=_("Submitter profile unblocked successfully."),
	)


@route(
	"/<profile_id>/block",
	methods=("POST",),
	summary="Block a submitter profile (Staff/Admin only)",
)
@frappe.whitelist()
@validate_request(BlockSubmitterRequest)
@handle_api_errors
@require_role(STAFF_ROLES)
def block_submitter(profile_id: str, reason: str, **kwargs):
	"""Block a submitter profile, preventing new grievance submissions."""
	if not frappe.db.exists("Grievance Submitter Profile", profile_id):
		frappe.throw(
			_("Submitter Profile '{0}' does not exist.").format(profile_id), frappe.DoesNotExistError
		)

	profile = frappe.get_doc("Grievance Submitter Profile", profile_id)
	profile.db_set({"is_blocked": 1, "blocked_reason": reason.strip()}, update_modified=False)

	return success_response(
		data={
			"profile_id": profile.name,
			"is_blocked": True,
			"blocked_reason": reason.strip(),
			"active": bool(profile.active),
		},
		message=_("Submitter profile blocked successfully."),
	)
