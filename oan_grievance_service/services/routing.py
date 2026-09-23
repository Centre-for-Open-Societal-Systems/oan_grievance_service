"""FR-03 Routing and Assignment with Nearest-Ancestor Administrative Area matching.

Auto-routing rules consider service category, grievance type, service provider, and
administrative area (tree hierarchy) configured on Grievance RBAC Assignment desks.
Where a rule matches, the grievance is assigned and the status advances to Assigned.
Where none matches, it stays Submitted and sits in the nodal officer's manual queue.
"""

from dataclasses import dataclass
from typing import Any

import frappe

from oan_grievance_service.permissions import get_area_bounds
from oan_grievance_service.services import constants as C

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
		],
	)

	candidates = []
	for a in assignments:
		specificity = 0
		matched = True

		# Direct fields match: category_scope, grievance_type_scope, service_provider_scope
		for scope_field, doc_field in MATCH_FIELDS:
			constraint = a.get(scope_field)
			if not constraint:
				continue
			specificity += 1
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

		area_match, area_span, has_area_constraint = _evaluate_ancestor_area(
			case_area, case_lft, case_rgt, a.administrative_area_scope
		)
		if not area_match:
			continue
		if has_area_constraint:
			specificity += 1

		# Candidate tuple: (area_span, -specificity, assignment)
		candidates.append((area_span, -specificity, a))

	if not candidates:
		return None
	candidates.sort(key=lambda row: (row[0], row[1]))
	return candidates[0][2]


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
			rows = frappe.db.sql(
				"""
				SELECT assigned_to, COUNT(name) AS open_count
				FROM `tabGrievance`
				WHERE assigned_to IN %(users)s
				  AND status NOT IN ('Closed', 'Rejected', 'Resolved')
				GROUP BY assigned_to
				""",
				{"users": tuple(users)},
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

	FSD 3.3 / Database Schema 8: resolves Tier 1 (department) and Tier 2 (officer) from the
	matching Grievance RBAC Assignment desk record.
	If no assignment matches, or no department/officer is available, the category falls back to 'Other'.
	"""
	from oan_grievance_service.services import lifecycle, notifications

	assignment = find_matching_assignment(grievance)
	doc = frappe.get_doc("Grievance RBAC Assignment", assignment.name) if assignment else None
	officer_user = pick_officer_by_strategy(doc) if doc else None

	current_cat = (
		grievance.get("service_category")
		if isinstance(grievance, dict) or hasattr(grievance, "get")
		else getattr(grievance, "service_category", None)
	)

	# Fallback to "Other" if no matching assignment, no department, or officers rostered but none available
	needs_fallback = (
		(not doc) or (not doc.department_scope) or (bool(doc.get("officers")) and not officer_user)
	)
	if needs_fallback and current_cat != "Other":
		if hasattr(grievance, "db_set"):
			grievance.db_set({"service_category": "Other", "grievance_type": "Other"}, update_modified=False)
		if hasattr(grievance, "service_category"):
			grievance.service_category = "Other"
			grievance.grievance_type = "Other"
		elif isinstance(grievance, dict):
			grievance["service_category"] = "Other"
			grievance["grievance_type"] = "Other"

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


def manual_assign(grievance, department, officer=None, assigned_by=None):
	"""FSD 3.3 / 4.1 step 8b: the nodal officer assigns from the manual queue."""
	from oan_grievance_service.services import lifecycle, notifications

	updates = {
		"assigned_dept": department,
		"routed_automatically": 0,
	}
	if officer:
		updates["assigned_to"] = officer
	grievance.db_set(updates, update_modified=False)

	lifecycle.transition(
		grievance, "Assign", note=f"Manually assigned by {assigned_by or frappe.session.user}"
	)
	notifications.queue(grievance, C.EVENT_ASSIGNED_MANUAL)


def manual_queue():
	"""FSD 3.3: grievances awaiting a nodal officer's routing decision."""
	return frappe.get_all(
		"Grievance",
		filters={"status": "Submitted", "assigned_dept": ["is", "not set"]},
		fields=["name", "ticket_number", "service_category", "administrative_area", "creation"],
		order_by="creation asc",
	)
