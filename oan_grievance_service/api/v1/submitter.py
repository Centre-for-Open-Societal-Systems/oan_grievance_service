"""Submitter profile endpoints: options (dropdowns), registration, and current profile (me)."""

import frappe
from frappe import _

from oan_grievance_service.api import version_meta
from oan_grievance_service.grievance_masters.doctype.submitter_profile.submitter_profile import (
	build_dedupe_key,
)
from oan_grievance_service.services import identity

from . import VERSION


@frappe.whitelist(allow_guest=True)
def options():
	"""Dropdown options and reference data needed for submitter registration.

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


@frappe.whitelist(allow_guest=True)
def register(**kwargs):
	"""Register or resolve a Submitter Profile.

	Required fields:
	    submitter_type   - Submitter Type link (e.g. 'Individual Farmer')
	    submitter_name   - Full Name or Organization Name
	    contact_mobile   - Phone number (+251...)

	Optional fields:
	    fayda_id         - National Digital ID (for citizens / farmers)
	    registration_number - Official Registration Number (for organizations)
	    contact_email    - Email address
	    preferred_language - 'am' or 'en' (default: 'am')
	    region           - Region link
	    administrative_unit - Administrative body / unit / woreda
	"""
	required = ("submitter_type", "submitter_name", "contact_mobile")
	missing = [field for field in required if not (kwargs.get(field) or "").strip()]
	if missing:
		frappe.throw(
			_("Missing required registration fields: {0}").format(", ".join(missing)),
			title=_("Incomplete Registration"),
		)

	submitter_type = kwargs["submitter_type"].strip()
	if not frappe.db.exists("Submitter Type", submitter_type):
		frappe.throw(
			_("Submitter Type '{0}' does not exist.").format(submitter_type),
			title=_("Invalid Submitter Type"),
		)

	# Automatically derive dedupe key from inputs
	dedupe_key = identity.derive_dedupe_key(
		submitter_type=submitter_type,
		mobile=kwargs.get("contact_mobile"),
		fayda_id=kwargs.get("fayda_id"),
		national_id=kwargs.get("national_id"),
		registration_number=kwargs.get("registration_number"),
		org_number=kwargs.get("org_number"),
		dedupe_key=kwargs.get("dedupe_key"),
	)
	if not dedupe_key:
		dedupe_key = build_dedupe_key(identity.SCHEME_PHONE, kwargs["contact_mobile"])

	# Check if already registered
	existing_name = frappe.db.get_value("Submitter Profile", {"dedupe_key": dedupe_key}, "name")
	if existing_name:
		doc = frappe.get_doc("Submitter Profile", existing_name)
		is_new = False
	else:
		doc = frappe.new_doc("Submitter Profile")
		doc.submitter_type = submitter_type
		doc.submitter_name = kwargs["submitter_name"].strip()
		doc.contact_mobile = kwargs["contact_mobile"].strip()
		doc.dedupe_key = dedupe_key
		doc.contact_email = (kwargs.get("contact_email") or "").strip() or None
		doc.preferred_language = kwargs.get("preferred_language") or "am"
		doc.region = kwargs.get("region")
		doc.administrative_unit = kwargs.get("administrative_unit")

		if frappe.session.user and frappe.session.user != "Guest":
			doc.user = frappe.session.user

		doc.insert(ignore_permissions=True)
		is_new = True

	return envelope(
		{
			"profile_id": doc.name,
			"dedupe_key": doc.dedupe_key,
			"submitter_type": doc.submitter_type,
			"submitter_name": doc.submitter_name,
			"contact_mobile": doc.contact_mobile,
			"contact_email": doc.contact_email,
			"preferred_language": doc.preferred_language,
			"region": doc.region,
			"administrative_unit": doc.administrative_unit,
			"active": doc.active,
			"is_blocked": doc.is_blocked,
			"is_new": is_new,
		}
	)


@frappe.whitelist()
def me():
	"""Get the Submitter Profile associated with the currently authenticated user."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Authentication required to access current profile."), title=_("Unauthorized"))

	name = frappe.db.get_value("Submitter Profile", {"user": user}, "name")
	if not name:
		frappe.throw(_("No submitter profile associated with your user account."), title=_("Profile Not Found"))

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
			"region": doc.region,
			"administrative_unit": doc.administrative_unit,
			"active": doc.active,
			"is_blocked": doc.is_blocked,
		}
	)


def envelope(data):
	"""Wrap response data with the API version metadata."""
	return {"meta": version_meta(VERSION), "data": data}
