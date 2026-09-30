# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance Administrative Area: the administrative area tree.

What belongs here: the tree itself (naming, path codes) and queries over it, such as
nested-set bounds and whether one area sits inside another.

What does not belong here: which areas a user may see. Scope is an RBAC assignment
fact, and the access rule built on it lives in permissions.py.
"""

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet

from oan_grievance_service.services import ticket_number


class GrievanceAdministrativeArea(NestedSet):
	nsm_parent_field = "parent_administrative_area"

	def autoname(self):
		if getattr(self, "name", None):
			return
		self.set_tree_metadata()
		if self.path_code:
			self.name = self.path_code
		elif self.code and self.parent_administrative_area:
			self.name = f"{self.parent_administrative_area}.{self.code}"
		else:
			self.name = self.area_name

	def validate(self):
		self.set_tree_metadata()
		self.validate_ticket_code()

	def validate_ticket_code(self):
		"""Check the ticket character here, where an admin can still fix it.

		Without this the error surfaces at the far end: the record saves, and
		the first farmer to file a grievance in the region is the one who gets
		the failure. A duplicate is worse than an invalid one, because it saves
		and works — two regions would then share a ticket prefix and their
		numbers would be indistinguishable after the fact.
		"""
		if not self.ticket_code:
			return

		self.ticket_code = self.ticket_code.strip().upper()

		if self.level_name != ticket_number.REGION_LEVEL:
			frappe.throw(
				_("Only regions carry a Ticket Code; {0} is a {1}.").format(
					frappe.bold(self.area_name), self.level_name or _("different level")
				),
				title=_("Ticket Code Not Applicable"),
			)

		if len(self.ticket_code) != ticket_number.REGION_WIDTH:
			frappe.throw(
				_("Ticket Code must be exactly {0} character(s); {1} is {2}.").format(
					ticket_number.REGION_WIDTH, frappe.bold(self.ticket_code), len(self.ticket_code)
				),
				title=_("Invalid Ticket Code"),
			)

		if self.ticket_code not in ticket_number.ALPHABET:
			frappe.throw(
				_(
					"Ticket Code {0} is not a valid character. I, L, O and U are excluded because they are misread."
				).format(frappe.bold(self.ticket_code)),
				title=_("Invalid Ticket Code"),
			)

		clash = frappe.db.get_value(
			"Grievance Administrative Area",
			{
				"ticket_code": self.ticket_code,
				"level_name": ticket_number.REGION_LEVEL,
				"name": ["!=", self.name],
			},
			"area_name",
		)
		if clash:
			frappe.throw(
				_("Ticket Code {0} already belongs to {1}. Every region needs its own.").format(
					frappe.bold(self.ticket_code), frappe.bold(clash)
				),
				title=_("Duplicate Ticket Code"),
			)

	def set_tree_metadata(self):
		"""Compute depth, country, and materialized path_code."""
		if not self.parent_administrative_area:
			if self.area_name == "World":
				self.depth = 0
				self.is_group = 1
				self.path_code = self.code or "WORLD"
			else:
				self.depth = 0
				self.path_code = self.code or self.area_name
			return

		parent = frappe.get_doc("Grievance Administrative Area", self.parent_administrative_area)
		self.depth = (parent.depth or 0) + 1

		# Derive country if not set
		if not self.country:
			if parent.level_name == "Country" or parent.depth == 1:
				self.country = parent.area_name
			elif parent.country:
				self.country = parent.country

		# Materialize path_code: e.g. ET.OROM.BISH.K01
		segment = (self.code or self.area_name).replace(" ", "_").upper()
		if parent.path_code and parent.path_code != "WORLD":
			self.path_code = f"{parent.path_code}.{segment}"
		else:
			self.path_code = segment


def on_doctype_update():
	frappe.db.add_index("Grievance Administrative Area", ["lft"])
	frappe.db.add_index("Grievance Administrative Area", ["rgt"])
	frappe.db.add_index("Grievance Administrative Area", ["path_code"])
	frappe.db.add_index("Grievance Administrative Area", ["depth"])


def get_area_bounds(area_name: str) -> tuple[int | None, int | None]:
	"""Return (lft, rgt) for an administrative area."""
	if not area_name:
		return (None, None)
	row = frappe.db.get_value("Grievance Administrative Area", area_name, ["lft", "rgt"], as_dict=True)
	if row and row.lft is not None and row.rgt is not None:
		return (int(row.lft), int(row.rgt))
	return (None, None)


def is_in_area_subtree(target_area_or_lft, ancestor_area: str) -> bool:
	"""Check if target_area (name or lft int) falls within ancestor_area's subtree."""
	if not ancestor_area or target_area_or_lft is None:
		return False
	anc_lft, anc_rgt = get_area_bounds(ancestor_area)
	if anc_lft is None or anc_rgt is None:
		return False
	if isinstance(target_area_or_lft, int) or (
		isinstance(target_area_or_lft, str) and target_area_or_lft.isdigit()
	):
		target_lft = int(target_area_or_lft)
	else:
		target_lft, _ = get_area_bounds(str(target_area_or_lft))
	if target_lft is None:
		return False
	return anc_lft <= target_lft <= anc_rgt


def area_bounds(scopes):
	"""Nested Set intervals for every area named by `scopes`, in one query."""
	names = {s.get("administrative_area_scope") for s in scopes}
	names = {n for n in names if n}
	if not names:
		return {}
	return {
		a.name: (a.lft, a.rgt)
		for a in frappe.get_all(
			"Grievance Administrative Area",
			filters={"name": ["in", list(names)]},
			fields=["name", "lft", "rgt"],
		)
		if a.lft is not None and a.rgt is not None
	}


def search_areas(parents=None, level_name=None, search=None, limit=100) -> list[dict]:
	"""Search and filter active administrative areas."""
	limit = min(int(limit or 100), 500)
	parents = [p for p in (parents if isinstance(parents, list) else [parents]) if p]

	subtree_parents = []
	direct_parents = []

	if parents:
		parent_docs = frappe.get_all(
			"Grievance Administrative Area",
			or_filters=[
				{"name": ["in", parents]},
				{"path_code": ["in", parents]},
				{"code": ["in", parents]},
				{"area_name": ["in", parents]},
			],
			fields=["name", "path_code", "code", "area_name", "level_name", "lft", "rgt"],
		)
		resolved_map = {}
		for doc in parent_docs:
			for key in (doc.name, doc.path_code, doc.code, doc.area_name):
				if key and key in parents:
					resolved_map[key] = doc

		for p in parents:
			doc = resolved_map.get(p)
			if doc:
				if level_name and doc.level_name != level_name:
					subtree_parents.append(doc)
				else:
					direct_parents.append(doc.name)
			else:
				direct_parents.append(p)

	Area = frappe.qb.DocType("Grievance Administrative Area")
	query = (
		frappe.qb.from_(Area)
		.select(
			Area.name.as_("area_id"),
			Area.area_name,
			Area.code,
			Area.path_code,
			Area.level_name,
			Area.parent_administrative_area,
			Area.is_group,
			Area.depth,
		)
		.where(Area.is_active == 1)
		.orderby(Area.area_name)
		.limit(limit)
	)

	if parents:
		parent_conditions = []
		if direct_parents:
			parent_conditions.append(Area.parent_administrative_area.isin(direct_parents))
		for sp in subtree_parents:
			parent_conditions.append((Area.lft > sp.lft) & (Area.rgt < sp.rgt))
		if parent_conditions:
			from pypika import Criterion

			query = query.where(Criterion.any(parent_conditions))
	elif not search and not level_name:
		query = query.where(Area.level_name == "Region")

	if level_name:
		query = query.where(Area.level_name == level_name)

	if search:
		query = query.where(Area.area_name.like(f"%{search.strip()}%"))

	return query.run(as_dict=True)
