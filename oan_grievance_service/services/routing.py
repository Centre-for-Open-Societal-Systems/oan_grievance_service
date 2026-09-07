"""FR-03 Routing and Assignment.

Auto-routing rules consider service category, grievance type, geographic area and
associated service provider. Where a rule matches, the grievance is assigned and the
status advances to Assigned. Where none matches, it stays Submitted and sits in the
nodal officer's manual queue.
"""

import frappe

from oan_grievance_service.services import constants as C

# FSD 3.3: the four dimensions a rule may constrain. A null on the rule means
# "any", so a rule that sets fewer dimensions is more general and loses to one
# that sets more. Order here is only for scoring; precedence is explicit.
MATCH_FIELDS = (
	("service_category", "service_category"),
	("grievance_type", "grievance_type"),
	("region", "region"),
	("zone", "zone"),
	("woreda", "woreda"),
	("kebele", "kebele"),
	("service_provider", "associated_service_provider"),
)


def find_matching_rule(grievance):
	"""Return the winning Grievance Routing Rule, or None for the manual queue.

	A rule matches when every dimension it constrains equals the grievance's value.
	Among matches, the explicit rule_precedence wins; ties break toward the rule that
	constrains more dimensions, because that is the more specific rule.
	"""
	rules = frappe.get_all(
		"Grievance Routing Rule",
		filters={"active": 1},
		fields=[
			"name",
			"rule_precedence",
			"assigned_dept",
			"priority",
			*[rule_field for rule_field, _ in MATCH_FIELDS],
		],
		order_by="rule_precedence asc",
	)

	candidates = []
	for rule in rules:
		specificity = 0
		matched = True
		for rule_field, doc_field in MATCH_FIELDS:
			constraint = rule.get(rule_field)
			if not constraint:
				continue
			specificity += 1
			if constraint != grievance.get(doc_field):
				matched = False
				break
		if matched:
			candidates.append((rule.rule_precedence or 0, -specificity, rule))

	if not candidates:
		return None
	candidates.sort(key=lambda row: (row[0], row[1]))
	return candidates[0][2]


def apply_routing(grievance, commit_status=True):
	"""Route a grievance. Returns the rule that matched, or None.

	FSD 3.3: on a match the department is set and status advances to Assigned. With no
	match the status stays Submitted so the case surfaces in the manual routing queue.
	"""
	from oan_grievance_service.services import lifecycle, notifications

	rule = find_matching_rule(grievance)

	if not rule:
		grievance.db_set("routed_automatically", 0, update_modified=False)
		return None

	grievance.db_set("assigned_dept", rule.assigned_dept, update_modified=False)
	grievance.db_set("routing_rule", rule.name, update_modified=False)
	grievance.db_set("routed_automatically", 1, update_modified=False)
	if rule.priority:
		grievance.db_set("priority", rule.priority, update_modified=False)

	if commit_status:
		lifecycle.change_status(
			grievance,
			C.ASSIGNED,
			note=f"Auto-routed by rule {rule.name}",
			automated=True,
		)
		notifications.queue(grievance, C.EVENT_ASSIGNED_AUTO)

	return rule


def manual_assign(grievance, department, officer=None, assigned_by=None):
	"""FSD 3.3 / 4.1 step 8b: the nodal officer assigns from the manual queue."""
	from oan_grievance_service.services import lifecycle, notifications

	grievance.db_set("assigned_dept", department, update_modified=False)
	if officer:
		grievance.db_set("assigned_to", officer, update_modified=False)
	grievance.db_set("routed_automatically", 0, update_modified=False)

	lifecycle.change_status(
		grievance,
		C.ASSIGNED,
		note=f"Manually assigned by {assigned_by or frappe.session.user}",
	)
	notifications.queue(grievance, C.EVENT_ASSIGNED_MANUAL)


def manual_queue():
	"""FSD 3.3: grievances awaiting a nodal officer's routing decision."""
	return frappe.get_all(
		"Grievance",
		filters={"status": C.SUBMITTED, "assigned_dept": ["is", "not set"]},
		fields=["name", "ticket_number", "service_category", "region", "woreda", "creation"],
		order_by="creation asc",
	)
