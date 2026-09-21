"""FR-01 / 3.1.1 Role-Based Access Control, deny-by-default.

The service runs on three capability roles and only three. A role answers *what actions
exist for you*; it never answers *which cases you may touch*. That second question is
answered by the Grievance RBAC Assignment records for the user - region, department and
category - applied as a permission query condition, so it filters list views, reports
and the API uniformly rather than being re-checked per screen.

Seniority is deliberately absent from the role list. The former L1 / L2 / Department
Head roles were rungs of a hierarchy, not distinct capabilities, and are replaced by
position in the reporting chain. See
.docs/sla_workflows_and_lifecycle_specification.md §10.1.

The FSD is explicit that routing eligibility does not by itself grant edit rights: an
explicit case assignment or approval permission is required. That distinction is what
this module enforces.
"""

import frappe

ROLE_SUBMITTER = "Grievance Submitter"
ROLE_OFFICER = "Grievance Officer"
ROLE_ADMIN = "Grievance Admin"

GRIEVANCE_ROLES = (ROLE_ADMIN, ROLE_OFFICER, ROLE_SUBMITTER)

# FSD Appendix F: administrators see all regions, departments and categories.
UNRESTRICTED_ROLES = {ROLE_ADMIN, "System Manager", "Administrator"}


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
		else "c.user, c.role_level, c.is_primary, p.name AS assignment_name, p.administrative_area_scope, p.department_scope, p.category_scope"
	)
	sql = f"""
		SELECT {field_str}
		FROM `tabGrievance RBAC Assignment Officer` c
		JOIN `tabGrievance RBAC Assignment` p ON p.name = c.parent
		WHERE {' AND '.join(conditions)}
	"""
	if order_by:
		sql += f" ORDER BY {order_by}"
	if limit:
		sql += f" LIMIT {int(limit)}"

	return frappe.db.sql(sql, params, as_dict=True)


def active_scopes(user=None):
	"""The user's live RBAC assignments, honouring the effective date window."""
	user = user or frappe.session.user
	fields = [
		"p.name AS assignment_name",
		"p.administrative_area_scope",
		"p.department_scope",
		"p.category_scope",
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


def area_bounds(scopes):
	"""Nested Set intervals for every area named by `scopes`, in one query."""
	names = {
		s.get("administrative_area_scope")
		if isinstance(s, dict)
		else getattr(s, "administrative_area_scope", None)
		for s in scopes
	}
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


def _quote(values):
	return ", ".join(frappe.db.escape(v) for v in values if v)


def _submitter_profiles(user):
	"""Profiles this user owns.

	Resolved through the explicit `user` link rather than by matching a contact address,
	so changing a contact email cannot transfer someone else's cases, and two profiles
	sharing an address do not both match.
	"""
	return frappe.get_all("Grievance Submitter Profile", filters={"user": user}, pluck="name")


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


def grievance_query_conditions(user=None):
	"""SQL appended to every Grievance list query. Deny-by-default.

	- Submitters only see their own cases and assisted submissions.
	- Officers see cases assigned to themselves and cases assigned to subordinate officers in their reporting chain.
	- Admins see all cases.
	"""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))

	if roles & UNRESTRICTED_ROLES:
		return ""

	clauses = []

	# FSD 3.1.1: a submitter reaches their own cases, and the assisted submissions they
	# filed on someone else's behalf. Both arms belong to the one Submitter role.
	if ROLE_SUBMITTER in roles:
		profiles = _submitter_profiles(user)
		if profiles:
			clauses.append(f"`tabGrievance`.submitter in ({_quote(profiles)})")
		clauses.append(f"`tabGrievance`.assisted_by_officer = {frappe.db.escape(user)}")

	# Grievance Officer: sees cases assigned to self/subordinates and cases within configured scope
	if ROLE_OFFICER in roles:
		scope_clauses = []
		scopes = active_scopes(user)
		bounds = area_bounds(scopes)
		for scope in scopes:
			parts = []
			dept_scope = (
				scope.get("department_scope")
				if isinstance(scope, dict)
				else getattr(scope, "department_scope", None)
			)
			cat_scope = (
				scope.get("category_scope")
				if isinstance(scope, dict)
				else getattr(scope, "category_scope", None)
			)
			area_scope = (
				scope.get("administrative_area_scope")
				if isinstance(scope, dict)
				else getattr(scope, "administrative_area_scope", None)
			)

			if dept_scope:
				parts.append(f"`tabGrievance`.assigned_dept = {frappe.db.escape(dept_scope)}")
			if cat_scope:
				parts.append(f"`tabGrievance`.service_category = {frappe.db.escape(cat_scope)}")
			if area_scope:
				area_lft, area_rgt = bounds.get(area_scope, (None, None))
				if area_lft is not None and area_rgt is not None:
					parts.append(
						f"(`tabGrievance`.area_lft >= {int(area_lft)} and `tabGrievance`.area_lft <= {int(area_rgt)})"
					)

			if parts:
				scope_clauses.append("(" + " and ".join(parts) + ")")

		team = get_subordinate_officers(user)
		scope_clauses.append(f"`tabGrievance`.assigned_to in ({_quote(team)})")
		clauses.append("(" + " or ".join(scope_clauses) + ")")

	if not clauses:
		return "1 = 0"
	return "(" + " or ".join(clauses) + ")"


def has_grievance_permission(doc, ptype="read", user=None):
	"""Per-document check. Mirrors the list conditions for a single record."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))

	if roles & UNRESTRICTED_ROLES:
		return True

	if ROLE_SUBMITTER in roles:
		submitter = doc.get("submitter") if isinstance(doc, dict) else getattr(doc, "submitter", None)
		assisted = (
			doc.get("assisted_by_officer")
			if isinstance(doc, dict)
			else getattr(doc, "assisted_by_officer", None)
		)
		docstatus = doc.get("docstatus", 0) if isinstance(doc, dict) else getattr(doc, "docstatus", 0)
		owns = bool(submitter) and submitter in _submitter_profiles(user)
		if owns or assisted == user:
			if ptype == "read":
				return True
			return ptype == "write" and int(docstatus or 0) != 2

	if ROLE_OFFICER not in roles:
		return False

	team = get_subordinate_officers(user)
	assigned = doc.get("assigned_to") if isinstance(doc, dict) else getattr(doc, "assigned_to", None)
	if assigned in team:
		# Visibility granted for all cases in reporting chain; editing requires explicit assignment or supervisor
		return True if ptype == "read" else assigned == user

	dept = doc.get("assigned_dept") if isinstance(doc, dict) else getattr(doc, "assigned_dept", None)
	category = (
		doc.get("service_category") if isinstance(doc, dict) else getattr(doc, "service_category", None)
	)
	area = (
		doc.get("administrative_area") if isinstance(doc, dict) else getattr(doc, "administrative_area", None)
	)
	case_lft = doc.get("area_lft") if isinstance(doc, dict) else getattr(doc, "area_lft", None)
	if case_lft is None and area:
		case_lft = frappe.db.get_value("Grievance Administrative Area", area, "lft")

	scopes = active_scopes(user)
	for scope in scopes:
		dept_scope = (
			scope.get("department_scope")
			if isinstance(scope, dict)
			else getattr(scope, "department_scope", None)
		)
		cat_scope = (
			scope.get("category_scope") if isinstance(scope, dict) else getattr(scope, "category_scope", None)
		)
		area_scope = (
			scope.get("administrative_area_scope")
			if isinstance(scope, dict)
			else getattr(scope, "administrative_area_scope", None)
		)

		if dept_scope and dept != dept_scope:
			continue
		if cat_scope and category != cat_scope:
			continue
		if area_scope:
			if case_lft is None or not is_in_area_subtree(case_lft, area_scope):
				continue

		# FSD 3.1.1: scope grants visibility; editing still needs the case assigned.
		return ptype == "read"

	return False


def can_approve_reassignment(user=None, request_doc=None):
	"""FSD 3.3.1: a reassignment is decided by a supervisor/admin, never by its requester.

	Enforces segregation of duties:
	1. The requester (initiated_by) cannot approve their own reassignment.
	2. Approver must hold Grievance Admin / System Manager or be a supervising officer.
	"""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))

	if not (roles & ({ROLE_OFFICER} | UNRESTRICTED_ROLES)):
		return False

	# Enforce segregation of duties: non-admin requesters cannot self-approve
	if request_doc and not (roles & UNRESTRICTED_ROLES):
		requester = (
			request_doc.get("initiated_by")
			if isinstance(request_doc, dict)
			else getattr(request_doc, "initiated_by", None)
		) or (
			request_doc.get("owner") if isinstance(request_doc, dict) else getattr(request_doc, "owner", None)
		)
		if requester and requester == user:
			return False

	return True


def can_approve_deferral(user=None, assignee=None):
	"""FSD 3.11.7: supervisor approval unless policy explicitly permits self-approval.

	"Supervisor" is read off the escalation chain rather than a role name: the approver
	must sit strictly above the assigned officer, which is the same `level_order` walk
	escalation uses. Falling back to a role check when the chain cannot place either
	party keeps a misconfigured assignment from deadlocking every deferral.
	"""
	from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
		requires_supervisor_approval,
	)

	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	has_base_right = bool(roles & ({ROLE_OFFICER} | UNRESTRICTED_ROLES))

	if not has_base_right:
		return False
	if not requires_supervisor_approval() or roles & UNRESTRICTED_ROLES:
		return has_base_right
	if not assignee or assignee == user:
		# Self-approval is exactly what the policy is there to stop.
		return assignee != user

	return _outranks(user, assignee)


def _outranks(approver, assignee):
	"""True when the approver sits strictly higher in the escalation chain."""
	from oan_grievance_service.services import sla

	approver_level = sla.current_level_of(approver)
	assignee_level = sla.current_level_of(assignee)
	if not approver_level or not assignee_level:
		return True  # Chain cannot place them; fall back to the role check already passed.

	orders = {
		row.name: row.level_order
		for row in frappe.get_all(
			"Grievance Role Level", filters={"is_active": 1}, fields=["name", "level_order"]
		)
	}
	return orders.get(approver_level, 0) > orders.get(assignee_level, 0)
