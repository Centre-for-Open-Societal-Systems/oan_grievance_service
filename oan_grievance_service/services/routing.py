"""Routing and assignment with nearest-ancestor Administrative Area matching.

Auto-routing rules consider service category, grievance type, service provider, and
administrative area (tree hierarchy) configured on Grievance RBAC Assignment desks.
Where a rule matches, the grievance is assigned and the status advances to Assigned.
Where none matches, it stays Submitted and sits in the nodal officer's manual queue.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import frappe

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	get_area_bounds,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import sla

UNBOUNDED = 999999999
DESK = "Grievance RBAC Assignment"
OFFICER_ROW = "Grievance RBAC Assignment Officer"

MATCH_FIELDS = (
	("category_scope", "service_category"),
	("grievance_type_scope", "grievance_type"),
	("service_provider_scope", "associated_service_provider"),
)


@dataclass(frozen=True, slots=True)
class OfficerCandidate:
	"""Evaluated officer candidate with current case load and capacity."""

	user: str
	officer_doc: Any
	open_count: int
	max_cap: int = 0

	@property
	def is_at_capacity(self) -> bool:
		return self.max_cap > 0 and self.open_count >= self.max_cap


def _extract_case_area_bounds(grievance):
	"""Extract area and (lft, rgt) bounds for the grievance case."""
	case_area = (
		grievance.get("administrative_area")
		if isinstance(grievance, dict) or hasattr(grievance, "get")
		else getattr(grievance, "administrative_area", None)
	)
	case_lft = (
		grievance.get("area_lft")
		if isinstance(grievance, dict) or hasattr(grievance, "get")
		else getattr(grievance, "area_lft", None)
	)
	case_rgt = None
	if case_area and case_lft is None:
		case_lft, case_rgt = get_area_bounds(case_area)
	elif case_area and case_lft is not None:
		_, case_rgt = get_area_bounds(case_area)
	return case_area, case_lft, case_rgt


def _evaluate_ancestor_area(case_area, case_lft, case_rgt, rule_area):
	"""Evaluate administrative area containment and return (is_match, area_span, has_constraint)."""
	if not rule_area:
		return (True, 999999999, False)
	if not case_area or case_lft is None:
		return (False, 999999999, True)

	anc_lft, anc_rgt = get_area_bounds(rule_area)
	if anc_lft is None or anc_rgt is None:
		return (False, 999999999, True)

	if not (anc_lft <= int(case_lft) and anc_rgt >= int(case_rgt or case_lft)):
		return (False, 999999999, True)

	return (True, int(anc_rgt) - int(anc_lft), True)


def _score_desk(desk, case_area, case_lft, case_rgt):
	"""Score a desk against a case area.

	Returns (matched, area_span, specificity).
	Specificity counts matching constraints on the desk.
	"""
	area_match, area_span, has_area_constraint = _evaluate_ancestor_area(
		case_area, case_lft, case_rgt, desk.get("administrative_area_scope")
	)
	if not area_match:
		return False, 999999999, 0

	return True, area_span, _specificity(desk, has_area_constraint)


def _specificity(desk, has_area_constraint):
	"""Number of constrained dimensions on a desk, counting the area when an officer row sets one."""
	fields = ("category_scope", "grievance_type_scope", "service_provider_scope")
	return sum(1 for field in fields if desk.get(field)) + (1 if has_area_constraint else 0)


def _desk_area_fits(desk_names, case_area, case_lft, case_rgt):
	"""Best area fit per desk as {desk: (span, constrained)}, in one query.

	The area lives on each officer row. A desk fits when one of its active rows covers the
	case's area, and the narrowest covering row sets the span. A row with no area covers
	everything. A desk nobody staffs still names its department, so it fits unconstrained.
	"""
	if not desk_names:
		return {}
	areas = defaultdict(list)
	for row in frappe.get_all(
		OFFICER_ROW,
		filters={"parent": ["in", list(desk_names)], "parenttype": DESK, "active": 1},
		fields=["parent", "administrative_area"],
	):
		areas[row.parent].append(row.administrative_area)

	fits = {}
	for name in desk_names:
		if not areas[name]:
			fits[name] = (UNBOUNDED, False)
			continue
		best = None
		for area in areas[name]:
			matched, span, constrained = _evaluate_ancestor_area(case_area, case_lft, case_rgt, area)
			if matched and (best is None or span < best[0]):
				best = (span, constrained)
		if best:
			fits[name] = best
	return fits


def find_matching_assignment(grievance):
	"""Return the winning Grievance RBAC Assignment (Desk), or None.

	Matches when category, grievance type, and service provider match (or are unconstrained),
	and one of the desk's officers covers the case's administrative area: the officer's area is
	an ancestor of, or equal to, the case's area. Resolution is nearest-ancestor.
	Among matching assignments:
	1. Narrowest tree span (rgt - lft) = deepest / nearest ancestor
	2. Specificity count (number of constrained matching dimensions)
	"""
	case_area, case_lft, case_rgt = _extract_case_area_bounds(grievance)

	today = frappe.utils.today()
	assignments = frappe.get_all(
		"Grievance RBAC Assignment",
		filters={
			"active": 1,
			"effective_from": ["<=", today],
		},
		or_filters=[
			["effective_to", "is", "not set"],
			["effective_to", ">=", today],
		],
		fields=[
			"name",
			"department_scope",
			"category_scope",
			"grievance_type_scope",
			"service_provider_scope",
			"routing_strategy",
			"reassignment_requires_approval",
			"creation",
		],
	)

	candidates = []
	scoped = []
	for a in assignments:
		matched = True

		# Direct fields match: category_scope, grievance_type_scope, service_provider_scope
		for scope_field, doc_field in MATCH_FIELDS:
			constraint = a.get(scope_field)
			if not constraint:
				continue
			doc_val = (
				grievance.get(doc_field)
				if isinstance(grievance, dict) or hasattr(grievance, "get")
				else getattr(grievance, doc_field, None)
			)
			if constraint != doc_val:
				matched = False
				break
		if matched:
			scoped.append(a)

	fits = _desk_area_fits([a.name for a in scoped], case_area, case_lft, case_rgt)
	for a in scoped:
		if a.name in fits:
			area_span, constrained = fits[a.name]
			# Candidate tuple: (area_span, -specificity, assignment)
			candidates.append((area_span, -_specificity(a, constrained), a))

	if not candidates:
		return None
	# Several departments may serve one category, so desks can tie on area and specificity.
	# Oldest desk wins, then name, so the same case always routes the same way.
	candidates.sort(key=lambda row: (row[0], row[1], str(row[2].creation), row[2].name))
	return candidates[0][2]


def officer_desks(grievance, department, officer):
	"""Return the active desks held by `officer` that cover `department` and the grievance's area,
	ranked by (area_span asc, -specificity).
	"""
	from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
		query_active_officer_assignments,
	)

	case_area, case_lft, case_rgt = _extract_case_area_bounds(grievance)
	rows = query_active_officer_assignments(
		user=officer,
		fields=[
			"p.name AS name",
			"p.department_scope",
			"p.category_scope",
			"p.grievance_type_scope",
			"p.service_provider_scope",
			"c.administrative_area AS administrative_area_scope",
			"p.routing_strategy",
			"p.reassignment_requires_approval",
		],
		order_by=None,
	)

	seen = set()
	candidates = []
	for r in rows:
		desk = dict(r)
		desk_name = desk.get("name")
		if not desk_name or desk_name in seen:
			continue

		if desk.get("department_scope") and desk.get("department_scope") != department:
			continue

		matched, area_span, specificity = _score_desk(desk, case_area, case_lft, case_rgt)
		if not matched:
			continue

		seen.add(desk_name)
		candidates.append((area_span, -specificity, desk))

	candidates.sort(key=lambda row: (row[0], row[1]))
	return [row[2] for row in candidates]


def department_desks(grievance, department):
	"""Return active desks with department_scope == department covering the grievance's area,
	ranked by (area_span asc, -specificity).
	"""
	case_area, case_lft, case_rgt = _extract_case_area_bounds(grievance)
	today = frappe.utils.today()
	assignments = frappe.get_all(
		"Grievance RBAC Assignment",
		filters={"active": 1, "department_scope": department, "effective_from": ["<=", today]},
		or_filters=[
			["effective_to", "is", "not set"],
			["effective_to", ">=", today],
		],
		fields=[
			"name",
			"department_scope",
			"category_scope",
			"grievance_type_scope",
			"service_provider_scope",
			"routing_strategy",
			"reassignment_requires_approval",
		],
	)

	fits = _desk_area_fits([a.name for a in assignments], case_area, case_lft, case_rgt)
	candidates = []
	for a in assignments:
		if a.name in fits:
			area_span, constrained = fits[a.name]
			candidates.append((area_span, -_specificity(a, constrained), a))

	candidates.sort(key=lambda row: (row[0], row[1]))
	return [row[2] for row in candidates]


def _covering_case(officers, grievance):
	"""The officers whose area covers the grievance's area, narrowest first.

	Only the nearest enclosing area is kept, so a woreda officer is picked over a region
	officer for a case in that woreda. An officer with no area covers everything and is the
	widest fit. Without a grievance there is no area to match and every officer stays.
	"""
	if grievance is None:
		return officers
	case_area, case_lft, case_rgt = _extract_case_area_bounds(grievance)
	fitting = []
	for officer in officers:
		matched, span, _constrained = _evaluate_ancestor_area(
			case_area, case_lft, case_rgt, getattr(officer, "administrative_area", None)
		)
		if matched:
			fitting.append((span, officer))
	if not fitting:
		return []
	narrowest = min(span for span, _officer in fitting)
	return [officer for span, officer in fitting if span == narrowest]


def _available(officers):
	"""Drop officers marked On Leave, so they get no new cases. Read from the row, no extra query."""
	return [o for o in officers if not getattr(o, "on_leave", 0)]


def pick_officer_by_strategy(assignment_doc, grievance=None):
	"""Pick an officer from the assignment's child officers based on routing_strategy.

	Pass the grievance to restrict the pool to officers whose area covers it.
	"""
	if not assignment_doc.get("officers"):
		return None

	# Only first-line officers take cases. A department head is reached by escalation and a
	# reviewer only reads, so neither is assigned work here.
	first_line = [
		o
		for o in assignment_doc.officers
		if getattr(o, "active", 1) and getattr(o, "role_level", None) not in C.NON_ASSIGNEE_LEVELS
	]
	# Availability first: an officer on leave must not hide the next-nearest one.
	active_officers = _covering_case(_available(first_line), grievance)
	if not active_officers:
		return None

	# An officer others on the desk report to is the escalation tier: cases reach them by
	# escalation, not as first-line assignments. If that leaves nobody, fall back to the roster.
	supervisors = {o.reports_to for o in active_officers if getattr(o, "reports_to", None)}
	active_officers = [o for o in active_officers if o.user not in supervisors] or active_officers

	strategy = getattr(assignment_doc, "routing_strategy", "Primary First") or "Primary First"

	if strategy == "Round Robin":

		def rr_key(o):
			val = getattr(o, "last_assigned_at", None)
			return (1, str(val)) if val else (0, "")

		active_officers.sort(key=rr_key)
		winner = active_officers[0]
		winner.last_assigned_at = frappe.utils.now_datetime()
		if winner.name:
			frappe.db.set_value(
				"Grievance RBAC Assignment Officer",
				winner.name,
				"last_assigned_at",
				winner.last_assigned_at,
				update_modified=False,
			)
		return winner.user

	elif strategy == "Least Loaded":
		users = [o.user for o in active_officers if o.user]
		counts = {}
		if users:
			# A case whose SLA clock has Stopped is no longer open load.
			rows = frappe.db.sql(
				"""
				SELECT assigned_to, COUNT(name) AS open_count
				FROM `tabGrievance`
				WHERE assigned_to IN %(users)s
				  AND status NOT IN %(concluded)s
				GROUP BY assigned_to
				""",
				{"users": tuple(users), "concluded": tuple(sla.states_in_category(sla.STOPPED)) or ("",)},
				as_dict=True,
			)
			counts = {r.assigned_to: r.open_count for r in rows}

		candidates = [
			OfficerCandidate(
				user=o.user,
				officer_doc=o,
				open_count=counts.get(o.user, 0),
				max_cap=getattr(o, "max_open_cases", 0) or 0,
			)
			for o in active_officers
		]

		candidates.sort(key=lambda c: (1 if c.is_at_capacity else 0, c.open_count))
		return candidates[0].user

	else:  # Primary First
		active_officers.sort(key=lambda o: -int(getattr(o, "is_primary", 0) or 0))
		return active_officers[0].user


def apply_routing(grievance, commit_status=True):
	"""Route a grievance. Returns the matching Grievance RBAC Assignment, or None.

	Resolves Tier 1 (department) and Tier 2 (officer) from the
	matching Grievance RBAC Assignment desk record.
	Where none matches, the grievance stays Submitted in the manual queue.
	"""
	from oan_grievance_service.services import lifecycle, notifications

	assignment = find_matching_assignment(grievance)
	doc = frappe.get_doc("Grievance RBAC Assignment", assignment.name) if assignment else None
	officer_user = pick_officer_by_strategy(doc, grievance) if doc else None

	if not doc or not doc.department_scope:
		if hasattr(grievance, "db_set"):
			grievance.db_set("routed_automatically", 0, update_modified=False)
		return None

	updates = {
		"assigned_dept": doc.department_scope,
		"routing_rule": doc.name,
		"routed_automatically": 1,
	}
	if officer_user:
		updates["assigned_to"] = officer_user
	if hasattr(grievance, "db_set"):
		grievance.db_set(updates, update_modified=False)

	if commit_status:
		lifecycle.transition(
			grievance, "Assign", reason=f"Auto-routed by assignment {doc.name}", automated=True
		)
		notifications.queue(grievance, C.EVENT_ASSIGNED_AUTO)
	return doc


def enqueue_routing(grievance_name: str) -> None:
	"""Asynchronously enqueue auto-routing for a submitted grievance."""
	try:
		frappe.enqueue(
			"oan_grievance_service.services.routing.route_grievance_job",
			queue="default",
			grievance_name=grievance_name,
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(
			title=f"Failed to enqueue routing for grievance: {grievance_name}",
			message=frappe.get_traceback(),
		)


def route_grievance_job(grievance_name: str) -> None:
	"""Worker task to route a grievance in the background queue."""
	if not frappe.db.exists("Grievance", grievance_name):
		return
	doc = frappe.get_doc("Grievance", grievance_name)
	# Only route if it is submitted and unassigned
	if doc.docstatus != 1 or doc.workflow_state != C.STATE_SUBMITTED or doc.assigned_to:
		return
	apply_routing(doc)


def drain_routing_queue(limit: int = 50) -> int:
	"""Process unrouted submitted cases through the routing engine without deleting any cases."""
	unrouted = frappe.get_all(
		"Grievance",
		filters={
			"docstatus": 1,
			"workflow_state": C.STATE_SUBMITTED,
			"assigned_to": ["is", "not set"],
			"routed_automatically": 0,
		},
		pluck="name",
		limit=limit,
	)
	routed_count = 0
	for name in unrouted:
		try:
			doc = frappe.get_doc("Grievance", name)
			if apply_routing(doc):
				routed_count += 1
		except Exception:
			frappe.log_error(
				title=f"Scheduled queue routing failed for {name}",
				message=frappe.get_traceback(),
			)
	return routed_count
