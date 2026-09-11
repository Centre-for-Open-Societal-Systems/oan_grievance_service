"""Reference data for the FSD 3.11.5 submission wizard.

The wizard cannot render without these: step 1 needs the channel and submitter
enums, step 2 walks Region > Zone > Woreda, step 3 loads grievance types for the
chosen service category. Each is a separate call because each fires at a different
moment -- the woreda list is meaningless until a zone is picked, and shipping all
1141 of them up front to avoid a round trip would cost more than the round trip.

Everything here is read-only reference data. Nothing in this module writes.
"""

import frappe
from frappe import _

from oan_grievance_service.grievance_management.doctype.grievance.grievance import segment

from .grievance import CHANNELS, envelope

SUBMITTER_TYPES = (
	"Individual Farmer",
	"Cooperative",
	"FPO",
	"NGO",
	"Woreda/Kebele Body",
	"Development Agent",
)

# Reference data changes on a governance timescale and is read on every form load.
CACHE_TTL = 3600


def _cached(key, builder):
	cache = frappe.cache()
	cached = cache.get_value(key)
	if cached is None:
		cached = builder()
		cache.set_value(key, cached, expires_in_sec=CACHE_TTL)
	return cached


@frappe.whitelist()
def form_meta():
	"""Everything the wizard needs that is not a lookup: one call, not five.

	These are Select options on the Grievance doctype rather than tables, so they
	are served from the enum definitions and cannot drift from what `submit` will
	accept.
	"""
	return envelope(
		{
			"submission_channels": list(CHANNELS),
			"submitter_types": list(SUBMITTER_TYPES),
			"priorities": ["Low", "Medium", "High"],
			"min_description_length": 20,
			"mobile_country_code": "+251",
			"consent_required": True,
		}
	)


@frappe.whitelist()
def regions():
	"""FSD 3.2.2 step 2, level 1. Fourteen rows -- no paging."""

	def build():
		return frappe.get_all(
			"Region",
			filters={"is_active": 1},
			fields=["name as value", "region_name as label", "code", "pcode"],
			order_by="region_name",
		)

	return envelope(_cached("grievance:regions", build))


@frappe.whitelist()
def zones(region):
	"""FSD 3.2.2 step 2, level 2. At most 22 rows (Oromia)."""
	if not region:
		frappe.throw(_("Region is required."), title=_("Missing Region"))

	def build():
		return frappe.get_all(
			"Zone",
			filters={"region": region, "is_active": 1},
			fields=["name as value", "zone_name as label", "code", "pcode"],
			order_by="zone_name",
		)

	return envelope(_cached(f"grievance:zones:{region}", build))


@frappe.whitelist()
def woredas(zone):
	"""FSD 3.2.2 step 2, level 3.

	Scoped to one zone deliberately. The unscoped list is 1141 rows, and a
	dropdown that loads all of them is both a slow first paint and a worse
	choice for the user than one that has already been narrowed twice.
	"""
	if not zone:
		frappe.throw(_("Zone is required."), title=_("Missing Zone"))

	def build():
		return frappe.get_all(
			"Woreda",
			filters={"zone": zone, "is_active": 1},
			fields=["name as value", "woreda_name as label", "code", "pcode"],
			order_by="woreda_name",
		)

	return envelope(_cached(f"grievance:woredas:{zone}", build))


@frappe.whitelist()
def search_woredas(query, limit=20):
	"""Type-ahead across all woredas, for submitters who know the woreda but not
	the zone. Call centre and IVR operators work this way."""
	query = (query or "").strip()
	if len(query) < 2:
		return envelope([])

	rows = frappe.get_all(
		"Woreda",
		filters={"is_active": 1, "woreda_name": ["like", f"%{query}%"]},
		fields=["name as value", "woreda_name as label", "region", "zone", "pcode"],
		order_by="woreda_name",
		limit_page_length=min(int(limit), 50),
	)
	return envelope(rows)


@frappe.whitelist()
def categories():
	"""FSD 3.2.2 step 3, level 1."""

	def build():
		return frappe.get_all(
			"Service Category",
			filters={"is_active": 1},
			fields=["name as value", "category_name as label", "code"],
			order_by="sort_order, category_name",
		)

	return envelope(_cached("grievance:categories", build))


@frappe.whitelist()
def grievance_types(service_category):
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

	return envelope(_cached(f"grievance:types:{service_category}", build))


@frappe.whitelist()
def ticket_preview(region=None, woreda=None, service_category=None):
	"""The FSD 3.2.3 ticket prefix the submission would receive.

	The wizard's review step shows this so the submitter recognises the ticket in
	the acknowledgement. The sequence is deliberately absent: it is allocated at
	insert, and showing a number here that a concurrent submission then takes
	would be worse than showing none.
	"""
	prefix = "-".join(
		[
			segment("Region", region),
			segment("Woreda", woreda),
			segment("Service Category", service_category),
		]
	)
	return envelope({"prefix": prefix, "example": f"{prefix}-00001"})


def clear_reference_cache(doc=None, method=None):
	"""Drop the cached lookups. Wired to master changes in hooks.

	Frappe calls doc_events handlers with (doc, method); neither is needed here
	because the whole prefix is dropped rather than one key.
	"""
	frappe.cache().delete_keys("grievance:")
