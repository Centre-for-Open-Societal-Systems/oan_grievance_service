"""Routing and assignment with nearest-ancestor Administrative Area matching.

Auto-routing rules consider service category, grievance type, service provider, and
administrative area (tree hierarchy) configured on Grievance RBAC Assignment desks.
Where a rule matches, the grievance is assigned and the status advances to Assigned.
Where none matches, it stays Submitted and sits in the nodal officer's manual queue.
"""

from dataclasses import dataclass
from typing import Any

import frappe

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	get_area_bounds,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import sla

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

	specificity = 0
	if desk.get("category_scope"):
		specificity += 1
	if desk.get("grievance_type_scope"):
		specificity += 1
	if desk.get("service_provider_scope"):
		specificity += 1
	if has_area_constraint:
		specificity += 1

	return True, area_span, specificity


def find_matching_assignment(grievance):
	"""Return the winning Grievance RBAC Assignment (Desk), or None.

	Uses Nearest-Ancestor resolution for administrative_area_scope:
	Matches when category, grievance type, and service provider match (or are unconstrained),
	and administrative area is an ancestor or exact match in the tree hierarchy.
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
			"administrative_area_scope",
			"routing_strategy",
			"reassignment_requires_approval",
		],
	)

	candidates = []
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
		if not matched:
			continue

		area_match, area_span, specificity = _score_desk(a, case_area, case_lft, case_rgt)
		if not area_match:
			continue

		# Candidate tuple: (area_span, -specificity, assignment)
		candidates.append((area_span, -specificity, a))

	if not candidates:
		return None
	candidates.sort(key=lambda row: (row[0], row[1]))
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
			"p.administrative_area_scope",
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
			"administrative_area_scope",
			"routing_strategy",
			"reassignment_requires_approval",
		],
	)

	candidates = []
	for a in assignments:
		if a.get("department_scope") and a.get("department_scope") != department:
			continue
		matched, area_span, specificity = _score_desk(a, case_area, case_lft, case_rgt)
		if not matched:
			continue
		candidates.append((area_span, -specificity, a))

	candidates.sort(key=lambda row: (row[0], row[1]))
	return [row[2] for row in candidates]


def pick_officer_by_strategy(assignment_doc):
	"""Pick an officer from the assignment's child officers based on routing_strategy."""
	if not assignment_doc.get("officers"):
		return None

	active_officers = [o for o in assignment_doc.officers if getattr(o, "active", 1)]
	if not active_officers:
		return None

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
	officer_user = pick_officer_by_strategy(doc) if doc else None

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
			grievance, "Assign", note=f"Auto-routed by assignment {doc.name}", automated=True
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
