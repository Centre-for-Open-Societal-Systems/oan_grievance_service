"""FR-01 / 3.1.1 Role-Based Access Control, deny-by-default.

The FSD is explicit that routing eligibility does not by itself grant edit rights: an
explicit case assignment or approval permission is required. That distinction is what
this module enforces.

Scope comes from the Grievance RBAC Assignment records for the user — region,
department and category — and is applied as a permission query condition, so it filters
list views, reports and the API uniformly rather than being re-checked per screen.
"""

import frappe

ROLE_FARMER = "Farmer"
ROLE_ASSISTED = "Assisted-Submissions"
ROLE_L1 = "L1 Nodal Officer"
ROLE_L2 = "L2 Senior Nodal Officer"
ROLE_DEPT_HEAD = "Department Head"
ROLE_ADMIN = "OAN Administrator-ATI"

GRIEVANCE_ROLES = (ROLE_ADMIN, ROLE_L2, ROLE_DEPT_HEAD, ROLE_L1, ROLE_ASSISTED, ROLE_FARMER)

# FSD Appendix F: administrators see all regions, departments and categories.
UNRESTRICTED_ROLES = {ROLE_ADMIN, "System Manager", "Administrator"}


def active_scopes(user=None):
	"""The user's live RBAC assignments, honouring the effective date window."""
	user = user or frappe.session.user
	today = frappe.utils.today()
	return frappe.get_all(
		"Grievance RBAC Assignment",
		filters={
			"user": user,
			"active": 1,
			"effective_from": ["<=", today],
		},
		or_filters=[
			["effective_to", "is", "not set"],
			["effective_to", ">=", today],
		],
		fields=["role", "region_scope", "department_scope", "category_scope"],
	)


def _quote(values):
	return ", ".join(frappe.db.escape(v) for v in values if v)


def grievance_query_conditions(user=None):
	"""SQL appended to every Grievance list query. Deny-by-default.

	Returns a condition string. An empty string means unrestricted; a false condition
	means the user sees nothing, which is the default for a user with no scope.
	"""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))

	if roles & UNRESTRICTED_ROLES:
		return ""

	clauses = []

	# FSD 3.1.1: farmers access only their own grievances.
	if ROLE_FARMER in roles:
		profiles = frappe.get_all(
			"Submitter Profile", filters={"contact_email": user}, pluck="name"
		)
		if profiles:
			clauses.append(f"`tabGrievance`.submitter in ({_quote(profiles)})")

	# FSD 3.1.1: a Development Agent sees only their own assisted submissions.
	if ROLE_ASSISTED in roles:
		clauses.append(f"`tabGrievance`.assisted_by_officer = {frappe.db.escape(user)}")

	# FSD 3.1.1: officers act on assigned cases within their configured scope.
	if roles & {ROLE_L1, ROLE_L2, ROLE_DEPT_HEAD}:
		scopes = active_scopes(user)
		scope_clauses = []
		for scope in scopes:
			parts = [f"`tabGrievance`.assigned_to = {frappe.db.escape(user)}"]
			if scope.department_scope:
				parts = [f"`tabGrievance`.assigned_dept = {frappe.db.escape(scope.department_scope)}"]
			if scope.region_scope:
				parts.append(f"`tabGrievance`.region = {frappe.db.escape(scope.region_scope)}")
			if scope.category_scope:
				parts.append(
					f"`tabGrievance`.service_category = {frappe.db.escape(scope.category_scope)}"
				)
			scope_clauses.append("(" + " and ".join(parts) + ")")

		# An assigned case is always visible to its own officer.
		scope_clauses.append(f"`tabGrievance`.assigned_to = {frappe.db.escape(user)}")
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

	if ROLE_FARMER in roles and doc.submitter:
		owns = frappe.db.get_value("Submitter Profile", doc.submitter, "contact_email") == user
		if owns:
			# FSD Appendix F: a farmer may read and respond, never assign or configure.
			return ptype in ("read", "write") if doc.status else ptype == "read"

	if ROLE_ASSISTED in roles and doc.assisted_by_officer == user:
		return ptype in ("read", "write")

	if doc.assigned_to == user:
		return True

	for scope in active_scopes(user):
		if scope.department_scope and doc.assigned_dept != scope.department_scope:
			continue
		if scope.region_scope and doc.region != scope.region_scope:
			continue
		if scope.category_scope and doc.service_category != scope.category_scope:
			continue
		# FSD 3.1.1: scope grants visibility; editing still needs the case assigned.
		return True if ptype == "read" else doc.assigned_to == user

	return False


def can_approve_reassignment(user=None):
	"""FSD 3.3.1: only an L2 Senior Nodal Officer approves a reassignment."""
	roles = set(frappe.get_roles(user or frappe.session.user))
	return bool(roles & ({ROLE_L2} | UNRESTRICTED_ROLES))


def can_approve_deferral(user=None):
	"""FSD 3.11.7: L2 approval unless policy explicitly permits self-approval."""
	roles = set(frappe.get_roles(user or frappe.session.user))
	return bool(roles & ({ROLE_L2, ROLE_DEPT_HEAD} | UNRESTRICTED_ROLES))
