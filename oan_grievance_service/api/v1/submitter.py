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


@frappe.whitelist(allow_guest=True)
@handle_api_errors
def options():
	"""Dropdown options and reference data needed for submitters and public registration.

	Returns:
	    submitter_types: Active submitter types (e.g. Individual Farmer, DA, Cooperative, NGO, etc.)
	    submission_types: Active intake channels (e.g. Mobile App, Web Portal, etc.)
	    preferred_languages: Supported notification languages
	"""
	submitter_types = frappe.get_all(
		"Submitter Type",
		filters={"is_active": 1},
		fields=["name as type_name", "code", "description"],
		order_by="name asc",
		ignore_permissions=True,
	)

	submission_types = frappe.get_all(
		"Submission Type",
		filters={"is_active": 1},
		fields=["name as type_name", "code", "description"],
		order_by="name asc",
		ignore_permissions=True,
	)

	preferred_languages = [
		{"code": "am", "label": "Amharic"},
		{"code": "en", "label": "English"},
	]

	return envelope(
		{
			"submitter_types": submitter_types,
			"submission_types": submission_types,
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
	scheme = doc.identity_scheme
	ident_val = doc.identity_value

	return envelope(
		{
			"profile_id": doc.name,
			"identity_scheme": scheme,
			"identity_value": ident_val,
			"fayda_id": ident_val if scheme == "fayda" else None,
			"registration_number": ident_val if scheme == "org" else None,
			"submitter_type": doc.submitter_type,
			"submitter_name": doc.submitter_name,
			"contact_mobile": doc.contact_mobile,
			"contact_email": doc.contact_email,
			# Language lives on the User record, not the profile, so submitters and staff
			# resolve it the same way.
			"preferred_language": frappe.db.get_value("User", user, "language"),
			"administrative_area": getattr(doc, "administrative_area", None),
			"administrative_unit": doc.administrative_unit,
			"active": doc.active,
			"is_blocked": doc.is_blocked,
		}
	)


def envelope(data):
	"""Wrap response data with the API version metadata."""
	return {"meta": version_meta(VERSION), "data": data}
