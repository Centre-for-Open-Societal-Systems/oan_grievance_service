"""Submitter profile options and reference data endpoints."""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, success_response

from oan_grievance_service.api.v1._options import (
	get_grievance_types,
	get_identity_schemes,
	get_phone_extensions,
	get_preferred_languages,
	get_service_categories,
	get_submission_types,
	get_submitter_types,
)
from oan_grievance_service.services.identity import MIN_DESCRIPTION_LENGTH

route = prefixed("/api/v1/submitters")


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
