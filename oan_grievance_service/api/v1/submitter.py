"""Submitter profile endpoints: options (dropdowns) and current profile (me)."""

import frappe
from frappe import _
from oan_auth_service.api.utils import handle_api_errors, require_role

from oan_grievance_service.api import version_meta

from . import VERSION

ALLOWED_SUBMITTER_ROLES = [
	"Grievance Submitter",
	"Grievance Officer",
	"Grievance Admin",
	"System Manager",
	"Administrator",
]


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_SUBMITTER_ROLES)
def options():
	"""Dropdown options and reference data needed for submitters.

	Returns:
	    submitter_types: Active submitter types (e.g. Individual Farmer, DA, Cooperative, NGO, etc.)
	    preferred_languages: Supported notification languages
	"""
	submitter_types = frappe.get_all(
		"Submitter Type",
		filters={"is_active": 1},
		fields=["name as type_name", "code", "description"],
		order_by="name asc",
	)

	preferred_languages = [
		{"code": "am", "label": "Amharic"},
		{"code": "en", "label": "English"},
	]

	return envelope(
		{
			"submitter_types": submitter_types,
			"preferred_languages": preferred_languages,
		}
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_SUBMITTER_ROLES)
def me():
	"""Get the Submitter Profile associated with the currently authenticated user."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Authentication required to access current profile."), title=_("Unauthorized"))

	name = frappe.db.get_value("Submitter Profile", {"user": user}, "name")
	if not name:
		frappe.throw(
			_("No submitter profile associated with your user account."), title=_("Profile Not Found")
		)

	doc = frappe.get_doc("Submitter Profile", name)
	return envelope(
		{
			"profile_id": doc.name,
			"dedupe_key": doc.dedupe_key,
			"submitter_type": doc.submitter_type,
			"submitter_name": doc.submitter_name,
			"contact_mobile": doc.contact_mobile,
			"contact_email": doc.contact_email,
			"preferred_language": doc.preferred_language,
			"administrative_area": getattr(doc, "administrative_area", None),
			"administrative_unit": doc.administrative_unit,
			"active": doc.active,
			"is_blocked": doc.is_blocked,
		}
	)


def envelope(data):
	"""Wrap response data with the API version metadata."""
	return {"meta": version_meta(VERSION), "data": data}
