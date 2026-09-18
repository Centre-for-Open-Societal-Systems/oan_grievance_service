"""Reference data for the FSD 3.11.5 submission wizard.

The location cascade lives in `administrative_area.get_areas`, which walks the
Administrative Area tree. What remains here is everything else the wizard needs
before it can render: the enums, the service taxonomy, and the ticket preview.

Everything in this module is read-only reference data. Nothing here writes.
"""

import frappe
from frappe import _
from oan_auth_service.api.utils import handle_api_errors, success_response

from oan_grievance_service.services import ticket_number

from .grievance import active_channels

# Reference data changes on a governance timescale and is read on every form load.
CACHE_TTL = 3600


def _cached(key, builder):
	cache = frappe.cache()
	cached = cache.get_value(key)
	if cached is None:
		cached = builder()
		cache.set_value(key, cached, expires_in_sec=CACHE_TTL)
	return cached


@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def form_meta():
	"""Everything the wizard needs that is not a lookup: one call, not five.

	The submitter types are read from the doctype rather than repeated here, so
	the form can never offer an option `submit` would reject.
	"""

	def build():
		return {
			"submission_channels": active_channels(),
			"submitter_types": frappe.get_all(
				"Grievance Submitter Type",
				filters={"is_active": 1}
				if frappe.get_meta("Grievance Submitter Type").has_field("is_active")
				else None,
				pluck="name",
			),
			"priorities": ["Low", "Medium", "High"],
			"min_description_length": 20,
			"mobile_country_code": "+251",
			"consent_required": True,
		}

	return success_response(data=_cached("grievance:form_meta", build))


@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def categories():
	"""FSD 3.2.2 step 3, level 1."""

	def build():
		return frappe.get_all(
			"Grievance Service Category",
			filters={"is_active": 1},
			fields=["name as value", "category_name as label", "code"],
			order_by="sort_order, category_name",
		)

	return success_response(data=_cached("grievance:categories", build))


@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def grievance_types(service_category: str):
	"""FSD 3.2.2 step 3, level 2: types are loaded per category.

	`Grievance.validate_grievance_type_category` enforces the same relationship on
	save, so a client that ignores this endpoint still cannot submit a mismatch.
	"""
	if not service_category:
		frappe.throw(_("Service category is required."), title=_("Missing Category"))

	def build():
		return frappe.get_all(
			"Grievance Type",
			filters={"service_category": service_category, "is_active": 1},
			fields=["name as value", "type_name as label"],
			order_by="type_name",
		)

	return success_response(data=_cached(f"grievance:types:{service_category}", build))


@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def ticket_preview(administrative_area: str | None = None, service_category: str | None = None):
	"""The characters this submission's ticket number is already known to carry.

	The wizard's review step shows this so the submitter recognises the ticket in
	the acknowledgement. The sequence is deliberately absent: it is allocated at
	insert, and showing a number here that a concurrent submission then takes
	would be worse than showing none. `pattern` masks it with `#`, which is not
	in the ticket alphabet, so a placeholder can never be mistaken for a real
	ticket number.
	"""
	parts = ticket_number.segments(administrative_area, service_category)
	masked = "#" * ticket_number.SEQUENCE_WIDTH
	return success_response(
		data={
			# Region and category open every ticket filed from here.
			"prefix": f"{parts['region']}{parts['category']}",
			"region": parts["region"],
			"category": parts["category"],
			"year": parts["year"],
			"pattern": ticket_number.display(f"{parts['region']}{parts['category']}{masked}{parts['year']}"),
		}
	)


def clear_reference_cache(doc=None, method=None):
	"""Drop the cached lookups. Wired to master changes in hooks.

	Frappe calls doc_events handlers with (doc, method); neither is needed here
	because the whole prefix is dropped rather than one key.
	"""
	frappe.cache().delete_keys("grievance:")
