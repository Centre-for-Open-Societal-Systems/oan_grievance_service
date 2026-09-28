"""Profile resolution and hook integration for OAN Grievance Service."""

import frappe

from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	active_scopes,
)
from oan_grievance_service.permissions import ROLE_OFFICER


def resolve_user_profile_hook(user_doc, roles=None) -> tuple[str, dict | None]:
	"""Hook subscriber for oan_auth_service.api.v1.auth.get_me.

	Returns ("grievance", profile_data) namespaced under data["profiles"]["grievance"]
	containing only domain attributes that auth_service does not already provide.
	"""
	user = user_doc.name
	user_roles = set(roles or frappe.get_roles(user))

	# 1. Officer Profile (operational bounds from active scopes)
	if ROLE_OFFICER in user_roles:
		scopes = active_scopes(user)
		if scopes:
			primary_scope = next((s for s in scopes if s.get("is_primary")), scopes[0])
			return "grievance", {
				"type": primary_scope.get("role_level") or ROLE_OFFICER,
				"department": primary_scope.get("department_scope"),
				"administrative_area": primary_scope.get("administrative_area_scope"),
				"category": primary_scope.get("category_scope"),
			}

	# 2. Submitter Profile (citizen / organization identity attributes)
	fields = [
		"name",
		"submitter_type",
		"submitter_name",
		"contact_mobile",
		"contact_email",
		"dedupe_key",
		"administrative_area",
		"administrative_unit",
	]
	profile = frappe.db.get_value("Grievance Submitter Profile", {"user": user}, fields, as_dict=True)
	if profile:
		from oan_auth_service.api.utils import split_phone_number

		from oan_grievance_service.grievance_management.doctype.grievance_submitter_profile.grievance_submitter_profile import (
			split_dedupe_key,
		)

		scheme, ident_val = split_dedupe_key(profile.get("dedupe_key"))
		identities = [{"scheme": scheme, "value": ident_val}] if scheme and ident_val else []
		phone_cc, phone_nat = (
			split_phone_number(profile.get("contact_mobile"))
			if profile.get("contact_mobile")
			else (None, None)
		)

		return "grievance", {
			"profile_id": profile.get("name"),
			"type": profile.get("submitter_type"),
			"submitter_name": profile.get("submitter_name"),
			"contact_mobile": profile.get("contact_mobile"),
			"country_code": phone_cc,
			"phone_number": phone_nat,
			"contact_email": profile.get("contact_email"),
			"identities": identities,
			"administrative_area": profile.get("administrative_area"),
			"administrative_unit": profile.get("administrative_unit"),
		}

	return "grievance", None


def on_user_registered_hook(user_doc, role=None, roles=None, **kwargs) -> None:
	"""Hook subscriber for oan_auth_service.api.v1.auth.register_user (on_user_registered).

	Automatically provisions or links a Grievance Submitter Profile when a user
	registers with role 'Grievance Submitter' (or default registration role).
	"""
	user_roles = set(roles or ([role] if role else []))
	if "Grievance Submitter" not in user_roles:
		return

	user_name = user_doc.name if hasattr(user_doc, "name") else user_doc.get("name")
	full_name = user_doc.full_name if hasattr(user_doc, "full_name") else user_doc.get("full_name")
	mobile_no = user_doc.mobile_no if hasattr(user_doc, "mobile_no") else user_doc.get("mobile_no")

	# Assemble phone number if provided split
	incoming_phone = (
		mobile_no or kwargs.get("contact_mobile") or kwargs.get("phone_number") or kwargs.get("phone")
	)
	country_code = kwargs.get("country_code") or kwargs.get("phone_country_code")
	if incoming_phone:
		from oan_auth_service.api.utils import assemble_phone_number

		contact_mobile = assemble_phone_number(incoming_phone, country_code=country_code)
	else:
		contact_mobile = None

	# Extract real email if present
	email = None
	raw_email = getattr(user_doc, "email", None) or (
		user_doc.get("email") if isinstance(user_doc, dict) else None
	)
	login_email = (
		getattr(user_doc, "oan_login_email", None)
		or getattr(user_doc, "user_email", None)
		or (
			user_doc.get("oan_login_email") or user_doc.get("user_email")
			if isinstance(user_doc, dict)
			else None
		)
	)
	if login_email and "@" in str(login_email):
		email = str(login_email)
	elif raw_email and "@id.openagrinet.internal" not in raw_email:
		email = raw_email
	if not email:
		email = kwargs.get("contact_email") or kwargs.get("email")

	from oan_grievance_service.api.v1.submitter import _create_or_update_submitter_profile

	_create_or_update_submitter_profile(
		user=user_name,
		submitter_type=kwargs.get("submitter_type") or "Individual Farmer",
		submitter_name=full_name or kwargs.get("submitter_name"),
		contact_mobile=contact_mobile,
		contact_email=email,
		administrative_area=kwargs.get("administrative_area") or kwargs.get("region"),
		administrative_unit=kwargs.get("administrative_unit") or kwargs.get("woreda"),
		preferred_language=kwargs.get("preferred_language"),
		fayda_id=kwargs.get("fayda_id"),
		national_id=kwargs.get("national_id"),
		registration_number=kwargs.get("registration_number"),
		org_number=kwargs.get("org_number"),
		farmer_id=kwargs.get("farmer_id"),
		dedupe_key=kwargs.get("dedupe_key"),
	)
