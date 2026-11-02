# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance RBAC Assignment: an officer's scope and place in the reporting chain.

What belongs here: queries over active assignments and their officer rows. Who holds a
scope, which rung a user sits on, who a user reports to, who reports to them.

What does not belong here: deciding whether a user may see or act on a case. These
functions return facts; permissions.py turns them into access rules.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import Order

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	is_in_area_subtree,
)
from oan_grievance_service.services.constants import ROLE_LEVEL_REVIEW_OFFICER

# A Reviewer sits on a department's desks too, with read-only access to that department.
OFFICER_ROLES = {"Grievance Officer", "Grievance Admin", "Grievance Review Officer"}
# Scope fields that make a desk more specific than a category-only desk. Area is not one:
# it sits on each officer row, because an officer is assigned to a region, not a desk.
NARROWING_SCOPES = ("grievance_type_scope", "service_provider_scope")
UNBOUNDED_SPAN = 999999999


class GrievanceRBACAssignment(Document):
	def validate(self):
		# Type- and provider-scoped desks predate these rules and are left alone.
		if self.is_unnarrowed():
			self.validate_no_repeated_officer()
			self.reject_duplicate_desk()
			if self.active and self.links_changed():
				self.validate_links()

	def is_unnarrowed(self) -> bool:
		return not any(self.get(field) for field in NARROWING_SCOPES)

	def is_category_only(self) -> bool:
		return bool(self.category_scope) and not any(self.get(field) for field in NARROWING_SCOPES)

	def validate_no_repeated_officer(self):
		seen = set()
		for row in self.officers:
			if row.user in seen:
				frappe.throw(_("Officer '{0}' is listed more than once.").format(row.user))
			seen.add(row.user)

	def reject_duplicate_desk(self):
		"""One category-only desk per (category, department), inactive desks included.

		A desk with no department serves the category in every department, and there is
		one of those per category too.

		A check-then-insert race would let two concurrent saves both pass, and no unique
		index can cover this partial scope. So the category row is locked first: the second
		save waits for the first to commit, then its locking read sees the new desk.
		"""
		if self.category_scope:
			frappe.db.get_value("Grievance Service Category", self.category_scope, "name", for_update=True)
		filters = {
			"category_scope": self.category_scope or ["is", "not set"],
			"department_scope": self.department_scope or ["is", "not set"],
			"grievance_type_scope": ["is", "not set"],
			"service_provider_scope": ["is", "not set"],
		}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.get_value("Grievance RBAC Assignment", filters, "name", for_update=True)
		if existing:
			frappe.throw(
				_("A category assignment already exists for {0} in {1} ({2}).").format(
					self.category_scope or _("every category"),
					self.department_scope or _("every department"),
					existing,
				),
				frappe.DuplicateEntryError,
			)

	def links_changed(self) -> bool:
		"""Whether a save touches what the link checks cover, so a routine save is never blocked by drift."""
		before = self.get_doc_before_save()
		if self.is_new() or not before:
			return True
		if any(
			before.get(field) != self.get(field) for field in ("category_scope", "department_scope", "active")
		):
			return True
		return _active_users(before.officers) != _active_users(self.officers)

	def validate_links(self):
		for row in self.officers:
			if row.active:
				_assert_officer(row.user)
		if self.category_scope:
			_assert_active(
				"Grievance Service Category", self.category_scope, "is_active", _("Service category")
			)
		if self.department_scope and not frappe.db.exists("Grievance Department", self.department_scope):
			frappe.throw(
				_("Department '{0}' does not exist.").format(self.department_scope), frappe.ValidationError
			)


def _active_users(rows) -> set[str]:
	return {row.user for row in rows if row.active}


def _assert_officer(user: str):
	label = _("Officer")
	if not frappe.db.exists("User", user):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, user), frappe.ValidationError)
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} '{1}' is disabled.").format(label, user), frappe.ValidationError)
	if not OFFICER_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(
			_("{0} '{1}' must be a Grievance Officer, Grievance Admin or Grievance Review Officer.").format(
				label, user
			),
			frappe.ValidationError,
		)


def _assert_active(doctype: str, name: str, flag_field: str, label: str):
	if not frappe.db.get_value(doctype, name, flag_field):
		frappe.throw(_("{0} '{1}' is inactive.").format(label, name), frappe.ValidationError)


def _default_fields(c, p, u):
	return [
		c.user,
		c.role_level,
		c.is_primary,
		p.name.as_("assignment_name"),
		c.administrative_area.as_("administrative_area_scope"),
		p.department_scope,
		p.category_scope,
		p.grievance_type_scope,
		p.service_provider_scope,
		p.reassignment_requires_approval,
	]


def _default_order(c, p, u):
	return [(c.is_primary, Order.desc), (p.modified, Order.desc)]


def _matches_or_unscoped(column, value):
	return (column == str(value).strip()) | column.isnull() | (column == "")


def query_active_officer_assignments(
	user=None,
	role_level=None,
	reports_to_list=None,
	department=None,
	category=None,
	administrative_area=None,
	include_user_details=False,
	fields=_default_fields,
	order_by=_default_order,
	limit=None,
	include_reviewers=False,
	distinct=False,
):
	"""Consolidated query builder for active Grievance RBAC Assignments & Officers.

	`fields` and `order_by` take the officer row `c`, its desk `p` and, with
	`include_user_details`, the `User` `u`; `order_by` returns (column, Order) pairs.

	A Reviewer's rows are left out unless `include_reviewers`: a Reviewer is on a desk to read
	it, so is no assignee, supervisor or rung. Their own scopes are read with `active_scopes`.
	"""
	today = frappe.utils.today()
	c = frappe.qb.DocType("Grievance RBAC Assignment Officer", alias="c")
	p = frappe.qb.DocType("Grievance RBAC Assignment", alias="p")
	u = frappe.qb.DocType("User", alias="u") if include_user_details else None

	query = frappe.qb.from_(c).join(p).on(p.name == c.parent)
	if u:
		query = query.join(u).on(u.name == c.user)
	query = query.select(*fields(c, p, u)).where(
		(c.active == 1)
		& (p.active == 1)
		& (p.effective_from <= today)
		& (p.effective_to.isnull() | (p.effective_to >= today))
	)
	if distinct:
		query = query.distinct()
	if not include_reviewers:
		query = query.where(c.role_level != ROLE_LEVEL_REVIEW_OFFICER)
	if user:
		query = query.where(c.user == user)
	if role_level:
		query = query.where(c.role_level == role_level)
	if reports_to_list:
		query = query.where(c.reports_to.isin(list(reports_to_list)))
	if department:
		query = query.where(_matches_or_unscoped(p.department_scope, department))
	if category:
		query = query.where(_matches_or_unscoped(p.category_scope, category))
	for column, order in order_by(c, p, u) if order_by else ():
		query = query.orderby(column, order=order)
	if limit:
		query = query.limit(int(limit))

	rows = query.run(as_dict=True)

	if administrative_area and rows:
		target_lft = frappe.db.get_value("Grievance Administrative Area", administrative_area, "lft")
		if target_lft is not None:
			rows = [
				r
				for r in rows
				if not r.get("administrative_area_scope")
				or is_in_area_subtree(target_lft, r.get("administrative_area_scope"))
			]

	return rows


def active_scopes(user=None):
	"""The user's live RBAC assignments, honouring the effective date window."""
	user = user or frappe.session.user
	return query_active_officer_assignments(
		user=user,
		fields=lambda c, p, u: [
			p.name.as_("assignment_name"),
			c.administrative_area.as_("administrative_area_scope"),
			p.department_scope,
			p.category_scope,
			p.grievance_type_scope,
			p.service_provider_scope,
			c.role_level,
			c.is_primary,
			c.max_open_cases,
		],
		order_by=None,
		include_reviewers=True,
	)


def covering_rows(rows, department=None, category=None, administrative_area=None):
	"""The rows whose desk covers the case, most specific first. Empty scopes are wildcards."""
	areas = {row.get("administrative_area_scope") for row in rows} | {administrative_area}
	areas.discard(None)
	areas.discard("")
	bounds = (
		{
			area.name: (area.lft, area.rgt)
			for area in frappe.get_all(
				"Grievance Administrative Area",
				filters={"name": ["in", list(areas)]},
				fields=["name", "lft", "rgt"],
			)
			if area.lft is not None and area.rgt is not None
		}
		if areas and administrative_area
		else {}
	)
	target_lft = bounds.get(administrative_area, (None, None))[0] if administrative_area else None

	ranked = []
	for index, row in enumerate(rows):
		dept, cat = row.get("department_scope"), row.get("category_scope")
		if department and dept and dept != department:
			continue
		if category and cat and cat != category:
			continue
		span = UNBOUNDED_SPAN
		area = row.get("administrative_area_scope")
		if target_lft is not None and area:
			lft, rgt = bounds.get(area, (None, None))
			if lft is None or not lft <= target_lft <= rgt:
				continue
			span = rgt - lft
		specificity = bool(department and dept) + bool(category and cat)
		ranked.append((-specificity, span, index, row))
	ranked.sort(key=lambda item: item[:3])
	return [row for *_key, row in ranked]


def find_officer_by_role_level(role_level, department=None, administrative_area=None, category=None):
	"""The officer at `role_level` whose desk covers the case most specifically (`covering_rows`)."""
	officers = query_active_officer_assignments(
		role_level=role_level,
		fields=lambda c, p, u: [
			c.user,
			c.is_primary,
			c.administrative_area.as_("administrative_area_scope"),
			p.department_scope,
			p.category_scope,
		],
		department=department,
		category=category,
	)
	best = covering_rows(officers, department, category, administrative_area)
	return best[0].user if best else None


def top_rung():
	"""The name of the last entry in GrievanceRoleLevel.get_chain(), or None."""
	from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
		GrievanceRoleLevel,
	)

	chain = GrievanceRoleLevel.get_chain()
	return chain[-1].name if chain else None


def department_head_of(department, area=None, category=None):
	"""Find the department head officer user for this department, category and area."""
	top = top_rung()
	if not top:
		return None
	return find_officer_by_role_level(top, department=department, administrative_area=area, category=category)


def holds_rung(user, role_level, department=None, area=None, category=None):
	"""Whether `user` has a live desk at `role_level` covering `department`, `category` and `area`."""
	if not user or not role_level:
		return False
	rows = query_active_officer_assignments(
		user=user,
		role_level=role_level,
		fields=lambda c, p, u: [
			p.department_scope,
			p.category_scope,
			c.administrative_area.as_("administrative_area_scope"),
		],
		order_by=None,
		department=department,
		category=category,
	)
	return bool(covering_rows(rows, department, category, area))


def get_subordinate_officers(user):
	"""Find all officers who report directly or indirectly to `user` (bottom-to-top hierarchy)."""
	if not user:
		return set()
	subordinates = {user}
	frontier = {user}
	while frontier:
		rows = query_active_officer_assignments(
			reports_to_list=frontier,
			fields=lambda c, p, u: [c.user],
			order_by=None,
			distinct=True,
		)
		new_users = {r.user for r in rows if r.user and r.user not in subordinates}
		if not new_users:
			break
		subordinates.update(new_users)
		frontier = new_users
	return subordinates


def current_level_of(user):
	"""The rung a user sits on, read from their RBAC assignment.

	The rung is deliberately not stored on the grievance. A case sits at whatever level
	its current assignee occupies, so escalating *is* the reassignment and the two can
	never disagree. This follows DIGIT PGR, which keys escalation off the assignee's
	designation rather than a counter on the complaint.
	"""
	if not user:
		return None

	rows = query_active_officer_assignments(
		user=user,
		fields=lambda c, p, u: [c.role_level],
		limit=1,
	)
	rows = [r for r in rows if r.role_level]
	return rows[0].role_level if rows else None


def get_officer_supervisor(user, department=None, administrative_area=None, strict=False, category=None):
	"""Find direct supervisor (reports_to) configured on the officer's RBAC assignment.

	With `strict`, only an assignment covering the given department and area counts,
	which is what an approval check needs. Otherwise any supervisor is better than
	none, so escalation falls back to the officer's first assignment.
	"""
	if not user:
		return None

	rows = query_active_officer_assignments(
		user=user,
		fields=lambda c, p, u: [
			c.reports_to,
			p.department_scope,
			p.category_scope,
			c.administrative_area.as_("administrative_area_scope"),
		],
	)
	rows = [r for r in rows if r.reports_to]
	if not rows:
		return None

	best = covering_rows(rows, department, category, administrative_area)
	if best:
		return best[0].reports_to
	return None if strict else rows[0].reports_to
