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

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	is_in_area_subtree,
)
from oan_grievance_service.services.constants import STAFF_DESK

OFFICER_ROLES = {"Grievance Officer", "Grievance Admin"}
# Scope fields that make a desk more specific than a category-only desk. Area is not one:
# it sits on each officer row, because an officer is assigned to a region, not a desk.
NARROWING_SCOPES = ("grievance_type_scope", "service_provider_scope")


class GrievanceRBACAssignment(Document):
	def autoname(self):
		"""The staff desk is found by name, so its seeder names it. Other desks use the series."""
		if self.flags.staff_desk:
			self.name = STAFF_DESK

	def validate(self):
		if self.name == STAFF_DESK:
			# Holds Admin and Reviewer accounts only. It stays inactive, which is what keeps
			# them out of routing, escalation and scope checks, and it has no category scope,
			# so the active-desk link checks below never apply to it.
			self.active = 0
			return
		# Type- and provider-scoped desks predate these rules and are left alone.
		if self.is_category_only():
			self.validate_no_repeated_officer()
			self.reject_duplicate_desk()
			if self.active and self.links_changed():
				self.validate_links()

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

		A check-then-insert race would let two concurrent saves both pass, and no unique
		index can cover this partial scope. So the category row is locked first: the second
		save waits for the first to commit, then its locking read sees the new desk.
		"""
		frappe.db.get_value("Grievance Service Category", self.category_scope, "name", for_update=True)
		filters = {
			"category_scope": self.category_scope,
			"department_scope": self.department_scope,
			"grievance_type_scope": ["is", "not set"],
			"service_provider_scope": ["is", "not set"],
		}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.get_value("Grievance RBAC Assignment", filters, "name", for_update=True)
		if existing:
			frappe.throw(
				_("A category assignment already exists for {0} in {1} ({2}).").format(
					self.category_scope, self.department_scope, existing
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
		_assert_active("Grievance Service Category", self.category_scope, "is_active", _("Service category"))
		_assert_active("Grievance Department", self.department_scope, "active", _("Department"))


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
			_("{0} '{1}' must be a Grievance Officer or Grievance Admin.").format(label, user),
			frappe.ValidationError,
		)


def _assert_active(doctype: str, name: str, flag_field: str, label: str):
	if not frappe.db.get_value(doctype, name, flag_field):
		frappe.throw(_("{0} '{1}' is inactive.").format(label, name), frappe.ValidationError)


def query_active_officer_assignments(
	user=None,
	role_level=None,
	reports_to_list=None,
	department=None,
	category=None,
	administrative_area=None,
	include_user_details=False,
	fields=None,
	order_by="c.is_primary DESC, p.modified DESC",
	limit=None,
):
	"""Consolidated query builder for active Grievance RBAC Assignments & Officers."""
	today = frappe.utils.today()
	conditions = [
		"c.active = 1",
		"p.active = 1",
		"p.effective_from <= %(today)s",
		"(p.effective_to IS NULL OR p.effective_to = '' OR p.effective_to >= %(today)s)",
	]
	params = {"today": today}

	if user:
		conditions.append("c.user = %(user)s")
		params["user"] = user
	if role_level:
		conditions.append("c.role_level = %(role_level)s")
		params["role_level"] = role_level
	if reports_to_list:
		conditions.append("c.reports_to IN %(reports_to_list)s")
		params["reports_to_list"] = tuple(reports_to_list)
	if department:
		conditions.append(
			"(p.department_scope = %(department)s OR p.department_scope IS NULL OR p.department_scope = '')"
		)
		params["department"] = str(department).strip()
	if category:
		conditions.append(
			"(p.category_scope = %(category)s OR p.category_scope IS NULL OR p.category_scope = '')"
		)
		params["category"] = str(category).strip()

	if include_user_details:
		field_str = (
			", ".join(fields)
			if fields
			else "DISTINCT c.user AS user_id, COALESCE(NULLIF(u.full_name, ''), u.name) AS full_name, u.email, c.role_level, c.is_primary, c.reports_to, c.administrative_area AS administrative_area_scope"
		)
		sql = f"""  # nosemgrep: frappe-sql-format-injection
			SELECT {field_str}
			FROM `tabGrievance RBAC Assignment Officer` c
			JOIN `tabGrievance RBAC Assignment` p ON p.name = c.parent
			JOIN `tabUser` u ON u.name = c.user
			WHERE {" AND ".join(conditions)}
		"""
	else:
		field_str = (
			", ".join(fields)
			if fields
			else "c.user, c.role_level, c.is_primary, p.name AS assignment_name, c.administrative_area AS administrative_area_scope, p.department_scope, p.category_scope, p.grievance_type_scope, p.service_provider_scope, p.reassignment_requires_approval"
		)
		sql = f"""  # nosemgrep: frappe-sql-format-injection
			SELECT {field_str}
			FROM `tabGrievance RBAC Assignment Officer` c
			JOIN `tabGrievance RBAC Assignment` p ON p.name = c.parent
			WHERE {" AND ".join(conditions)}
		"""
	if order_by:
		sql += f" ORDER BY {order_by}"
	if limit:
		sql += f" LIMIT {int(limit)}"

	rows = frappe.db.sql(sql, params, as_dict=True)  # nosemgrep: frappe-sql-format-injection

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
	fields = [
		"p.name AS assignment_name",
		"c.administrative_area AS administrative_area_scope",
		"p.department_scope",
		"p.category_scope",
		"p.grievance_type_scope",
		"p.service_provider_scope",
		"c.role_level",
		"c.is_primary",
		"c.max_open_cases",
	]
	return query_active_officer_assignments(user=user, fields=fields, order_by=None)


def find_officer_by_role_level(role_level, department=None, administrative_area=None):
	"""Dynamically resolve an officer user from active Grievance RBAC Assignments.

	Honours role_level, geographic jurisdiction (area tree interval), line department
	(NULL for nodal officers who cover all departments in an area), and primary post priority.
	"""
	fields = [
		"c.user",
		"c.is_primary",
		"c.administrative_area AS administrative_area_scope",
		"p.department_scope",
	]
	officers = query_active_officer_assignments(role_level=role_level, fields=fields)
	if not officers:
		return None

	target_lft = None
	if administrative_area:
		target_lft = frappe.db.get_value("Grievance Administrative Area", administrative_area, "lft")

	for o in officers:
		# Department filter: if assignment specifies a department, it must match.
		if department and o.department_scope and o.department_scope != department:
			continue
		# Area filter: if assignment specifies an area, target area must be in its subtree.
		if target_lft is not None and o.administrative_area_scope:
			if not is_in_area_subtree(target_lft, o.administrative_area_scope):
				continue
		return o.user

	return None


def top_rung():
	"""The name of the last entry in GrievanceRoleLevel.get_chain(), or None."""
	from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
		GrievanceRoleLevel,
	)

	chain = GrievanceRoleLevel.get_chain()
	return chain[-1].name if chain else None


def department_head_of(department, area=None):
	"""Find the department head officer user for this department and area."""
	top = top_rung()
	if not top:
		return None
	return find_officer_by_role_level(top, department=department, administrative_area=area)


def holds_rung(user, role_level, department=None, area=None):
	"""Whether `user` has a live desk at `role_level` covering `department` and `area`."""
	if not user or not role_level:
		return False
	rows = query_active_officer_assignments(
		user=user,
		role_level=role_level,
		fields=["p.department_scope", "c.administrative_area AS administrative_area_scope"],
		order_by=None,
	)
	if not rows:
		return False
	target_lft = None
	if area:
		target_lft = frappe.db.get_value("Grievance Administrative Area", area, "lft")
	for r in rows:
		if department and r.department_scope and r.department_scope != department:
			continue
		if target_lft is not None and r.administrative_area_scope:
			if not is_in_area_subtree(target_lft, r.administrative_area_scope):
				continue
		return True
	return False


def get_subordinate_officers(user):
	"""Find all officers who report directly or indirectly to `user` (bottom-to-top hierarchy)."""
	if not user:
		return set()
	subordinates = {user}
	frontier = {user}
	while frontier:
		rows = query_active_officer_assignments(
			reports_to_list=frontier,
			fields=["DISTINCT c.user"],
			order_by=None,
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
		fields=["c.role_level"],
		limit=1,
	)
	rows = [r for r in rows if r.role_level]
	return rows[0].role_level if rows else None


def get_officer_supervisor(user, department=None, administrative_area=None, strict=False):
	"""Find direct supervisor (reports_to) configured on the officer's RBAC assignment.

	With `strict`, only an assignment covering the given department and area counts,
	which is what an approval check needs. Otherwise any supervisor is better than
	none, so escalation falls back to the officer's first assignment.
	"""
	if not user:
		return None

	rows = query_active_officer_assignments(
		user=user,
		fields=["c.reports_to", "p.department_scope", "c.administrative_area AS administrative_area_scope"],
	)
	rows = [r for r in rows if r.reports_to]
	if not rows:
		return None

	target_lft = None
	if administrative_area:
		target_lft = frappe.db.get_value("Grievance Administrative Area", administrative_area, "lft")

	for r in rows:
		if department and r.department_scope and r.department_scope != department:
			continue
		if target_lft is not None and r.administrative_area_scope:
			if not is_in_area_subtree(target_lft, r.administrative_area_scope):
				continue
		return r.reports_to

	return None if strict else rows[0].reports_to
