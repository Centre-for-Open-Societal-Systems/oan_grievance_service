# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Service Provider: the directory behind the grievance form's provider dropdown.

What belongs here: the record's own rules (a parent chain without loops) and the queries
over the directory, such as search.

What does not belong here: who may call the search. That is the endpoint guard in
api/v1/service_provider.py.

No role may delete a provider by hand; deactivate it instead. The sync removes a provider its
source dropped, but deactivates one that something still links to, so a grievance that names
a provider always resolves.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Coalesce, Count
from pypika import Case, Order

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	area_path_labels,
	get_area_bounds,
)
from oan_grievance_service.services.resolvers import resolve_administrative_area

DOCTYPE = "Grievance Service Provider"

# Must match the Select options in the DocType JSON; test_grievance_service_provider pins it.
PROVIDER_TYPES = (
	"Bank",
	"Microfinance",
	"Cooperative",
	"Cooperative Union",
	"Input Store",
	"Market",
	"Government Office",
	"Other",
)

MIN_SEARCH_LENGTH = 2
# The dropdown shows a short list; a long one means the user should type more.
MAX_PAGE_SIZE = 50


# A provider that came from a source is that source's record: its own fields change at the
# source, and the next sync would overwrite a local edit. Only `is_active` is the admin's.
SYNC_OWNED_FIELDS = (
	"provider_name",
	"provider_type",
	"parent_provider",
	"administrative_area",
	"registered_region",
	"registration_number",
	"logo",
	"source",
	"external_ref",
)


class GrievanceServiceProvider(Document):
	def validate(self):
		self.provider_name = (self.provider_name or "").strip()
		self.validate_parent_chain()
		self.validate_sync_owned_fields()

	def validate_sync_owned_fields(self):
		if self.is_new() or not self.source or frappe.flags.in_service_provider_sync:
			return
		before = self.get_doc_before_save()
		changed = [f for f in SYNC_OWNED_FIELDS if (self.get(f) or None) != (before.get(f) or None)]
		if changed:
			frappe.throw(
				_(
					"{0} comes from the source {1} and is changed there. Only Active can be edited here."
				).format(frappe.bold(self.name), frappe.bold(self.source)),
				frappe.ValidationError,
			)

	def validate_parent_chain(self):
		"""A provider can't be its own parent, and the parent chain must not loop."""
		if not self.parent_provider:
			return
		if self.parent_provider == self.name:
			frappe.throw(_("A provider cannot be its own parent."), frappe.ValidationError)

		seen = {self.name}
		current = self.parent_provider
		while current:
			if current in seen:
				frappe.throw(
					_("Parent Provider {0} would make the parent chain loop.").format(
						frappe.bold(self.parent_provider)
					),
					frappe.ValidationError,
				)
			seen.add(current)
			current = frappe.db.get_value(DOCTYPE, current, "parent_provider")


def on_doctype_update():
	# The dropdown filters on is_active and sorts by name.
	frappe.db.add_index(DOCTYPE, ["is_active", "provider_name"], index_name="is_active_provider_name")
	# Lets the sync upsert safely. Hand-made providers have no source, and NULLs never collide.
	frappe.db.add_unique(DOCTYPE, ["source", "external_ref"], constraint_name="unique_source_external_ref")


def search_providers(
	search: str | None = None,
	provider_type: str | None = None,
	area: str | None = None,
	page: int = 1,
	page_size: int = 20,
) -> tuple[list[dict], int]:
	"""Active providers matching the filters, one page of rows and the total count.

	`search` matches the provider's name, its registration number and its parent's name.
	`area` keeps providers inside that area or in any area above it, so a kebele-level
	user still sees the woreda, zone, region and national providers. A provider with no
	area is nationwide and always matches. The most local providers sort first, after
	names that start with the search text.
	"""
	area_id = _resolve_area(area)
	Provider, Parent, Area = _tables()
	matching = _filtered(Provider, Parent, Area, search, provider_type, area_id)

	total = matching.select(Count("*")).run()[0][0]
	if not total:
		return [], 0

	rows = (
		_ordered(matching.select(*_columns(Provider, Parent, Area)), Provider, Area, search)
		.limit(page_size)
		.offset((page - 1) * page_size)
		.run(as_dict=True)
	)
	return rows, total


def get_provider(provider_id: str) -> dict:
	"""One provider row, active or not, so an old grievance can still show its name."""
	Provider, Parent, Area = _tables()
	rows = (
		_joined(Provider, Parent, Area)
		.select(*_columns(Provider, Parent, Area))
		.where(Provider.name == provider_id)
		.run(as_dict=True)
	)
	if not rows:
		frappe.throw(_("Service provider not found."), frappe.DoesNotExistError)
	return rows[0]


def provider_items(rows: list[dict], with_status: bool = False) -> list[dict]:
	"""API items for provider rows, with every area label read in one query pair.

	Search only returns active providers, so it leaves `is_active` out; the single
	lookup includes it because that one can return an inactive provider.
	"""
	labels = area_path_labels({row.administrative_area for row in rows})
	items = []
	for row in rows:
		item = {
			"id": row.name,
			"name": row.provider_name,
			"type": row.provider_type,
			"parent": {"id": row.parent_id, "name": row.parent_name} if row.parent_id else None,
			"area": {
				"id": row.administrative_area,
				"name": row.area_name,
				"path_label": labels.get(row.administrative_area, row.area_name),
			}
			if row.administrative_area
			else None,
			"registration_number": row.registration_number or None,
			"external_ref": row.external_ref or None,
			"registered_region": row.registered_region or None,
			"logo": row.logo or None,
		}
		if with_status:
			item["is_active"] = bool(row.is_active)
		items.append(item)
	return items


def _tables():
	return (
		frappe.qb.DocType(DOCTYPE),
		frappe.qb.DocType(DOCTYPE, alias="parent"),
		frappe.qb.DocType("Grievance Administrative Area"),
	)


def _columns(Provider, Parent, Area):
	return (
		Provider.name,
		Provider.provider_name,
		Provider.provider_type,
		Provider.registration_number,
		Provider.administrative_area,
		Provider.registered_region,
		Provider.logo,
		Provider.external_ref,
		Provider.is_active,
		Parent.name.as_("parent_id"),
		Parent.provider_name.as_("parent_name"),
		Area.area_name,
	)


def _joined(Provider, Parent, Area):
	return (
		frappe.qb.from_(Provider)
		.left_join(Parent)
		.on(Parent.name == Provider.parent_provider)
		.left_join(Area)
		.on(Area.name == Provider.administrative_area)
	)


def _resolve_area(area: str | None) -> str | None:
	if not area:
		return None
	area_id = resolve_administrative_area(area)
	if not area_id:
		frappe.throw(_("Administrative area not found."), frappe.DoesNotExistError)
	return area_id


def _filtered(Provider, Parent, Area, search, provider_type, area_id):
	"""Active providers narrowed by the filters."""
	query = _joined(Provider, Parent, Area).where(Provider.is_active == 1)
	if provider_type:
		query = query.where(Provider.provider_type == provider_type)
	if search:
		pattern = f"%{_escape_like(search)}%"
		query = query.where(
			Provider.provider_name.like(pattern)
			| Provider.registration_number.like(pattern)
			| Parent.provider_name.like(pattern)
		)
	if area_id:
		lft, rgt = get_area_bounds(area_id)
		inside = (Area.lft >= lft) & (Area.rgt <= rgt)
		above = (Area.lft <= lft) & (Area.rgt >= rgt)
		nationwide = Provider.administrative_area.isnull() | (Provider.administrative_area == "")
		query = query.where(nationwide | inside | above)
	return query


def _ordered(query, Provider, Area, search):
	"""Name prefix first, then the most local area, then name; `name` breaks ties."""
	if search:
		prefix = Case().when(Provider.provider_name.like(f"{_escape_like(search)}%"), 0).else_(1)
		query = query.orderby(prefix)
	# Depth, not lft: lft is a position in the tree, not how local the area is.
	# A provider with no area is nationwide, so it sorts last.
	return query.orderby(Coalesce(Area.depth, -1), order=Order.desc).orderby(
		Provider.provider_name, Provider.name
	)


def _escape_like(text: str) -> str:
	"""Escape LIKE wildcards so a search for "100%" matches the text, not everything."""
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
