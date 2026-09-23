"""Profile resolution and hook integration for OAN Grievance Service."""

import frappe

from oan_grievance_service.permissions import (
	ROLE_OFFICER,
	active_scopes,
)


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
		"dedupe_key",
		"administrative_area",
		"administrative_unit",
	]
	profile = frappe.db.get_value("Grievance Submitter Profile", {"user": user}, fields, as_dict=True)
	if profile:
		from oan_grievance_service.grievance_masters.doctype.grievance_submitter_profile.grievance_submitter_profile import (
			split_dedupe_key,
		)

		scheme, ident_val = split_dedupe_key(profile.get("dedupe_key"))
		identities = [{"scheme": scheme, "value": ident_val}] if scheme and ident_val else []

		return "grievance", {
			"profile_id": profile.get("name"),
			"type": profile.get("submitter_type"),
			"identities": identities,
			"administrative_area": profile.get("administrative_area"),
			"administrative_unit": profile.get("administrative_unit"),
		}

	return "grievance", None
