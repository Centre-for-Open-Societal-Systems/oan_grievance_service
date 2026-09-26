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


class GrievanceRBACAssignment(Document):
	pass


def query_active_officer_assignments(
	user=None,
	role_level=None,
	reports_to_list=None,
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

	field_str = (
		", ".join(fields)
		if fields
		else "c.user, c.role_level, c.is_primary, p.name AS assignment_name, p.administrative_area_scope, p.department_scope, p.category_scope, p.grievance_type_scope, p.service_provider_scope"
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

	return frappe.db.sql(sql, params, as_dict=True)  # nosemgrep: frappe-sql-format-injection


def active_scopes(user=None):
	"""The user's live RBAC assignments, honouring the effective date window."""
	user = user or frappe.session.user
	fields = [
		"p.name AS assignment_name",
		"p.administrative_area_scope",
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
	fields = ["c.user", "c.is_primary", "p.administrative_area_scope", "p.department_scope"]
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
		fields=["c.reports_to", "p.department_scope", "p.administrative_area_scope"],
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
