"""Profile resolution and hook integration for OAN Grievance Service."""

import frappe
from frappe import _

from oan_grievance_service.permissions import (
	ROLE_ADMIN,
	ROLE_OFFICER,
	ROLE_SUBMITTER,
	active_scopes,
)


def resolve_user_profile_hook(user_doc, roles=None) -> tuple[str, dict | None]:
	"""Hook subscriber for oan_auth_service.api.v1.auth.get_me.

	Returns ("grievance", profile_data) namespaced under data["profiles"]["grievance"].
	"""
	user = user_doc.name
	user_roles = set(roles or frappe.get_roles(user))

	# 1. Check if user has an associated Grievance Submitter Profile
	profile_name = frappe.db.get_value("Grievance Submitter Profile", {"user": user}, "name")
	if profile_name:
		doc = frappe.get_doc("Grievance Submitter Profile", profile_name)
		scheme = doc.identity_scheme
		ident_val = doc.identity_value

		return "grievance", {
			"profile_id": doc.name,
			"full_name": doc.submitter_name,
			"type": doc.submitter_type,
			"role": ROLE_SUBMITTER,
			"identity_scheme": scheme,
			"identity_value": ident_val,
			"fayda_id": ident_val if scheme == "fayda" else None,
			"registration_number": ident_val if scheme == "org" else None,
			"contact_mobile": doc.contact_mobile,
			"contact_email": doc.contact_email,
			"preferred_language": getattr(user_doc, "language", None)
			or frappe.db.get_value("User", user, "language"),
			"administrative_area": getattr(doc, "administrative_area", None),
			"administrative_unit": doc.administrative_unit,
			"active": doc.active,
			"is_blocked": doc.is_blocked,
		}

	# 2. Check if user is staff (Officer, Admin, or System Manager)
	is_admin = user == "Administrator" or "System Manager" in user_roles or ROLE_ADMIN in user_roles
	is_officer = ROLE_OFFICER in user_roles

	if not (is_admin or is_officer):
		return "grievance", None

	user_type = "Admin" if is_admin else "Officer"
	primary_role = ROLE_ADMIN if is_admin else ROLE_OFFICER

	scopes = active_scopes(user)
	primary_scope = next((s for s in scopes if s.get("is_primary")), scopes[0] if scopes else {})

	return "grievance", {
		"profile_id": user,
		"full_name": user_doc.full_name or f"{user_doc.first_name or ''} {user_doc.last_name or ''}".strip(),
		"type": user_type,
		"role": primary_role,
		"contact_email": user_doc.email,
		"contact_mobile": user_doc.mobile_no or user_doc.phone,
		"preferred_language": getattr(user_doc, "language", None),
		"role_level": primary_scope.get("role_level"),
		"department": primary_scope.get("department_scope"),
		"administrative_area": primary_scope.get("administrative_area_scope"),
		"category": primary_scope.get("category_scope"),
		"active_assignments": scopes,
		"active": 1 if user_doc.enabled else 0,
	}
