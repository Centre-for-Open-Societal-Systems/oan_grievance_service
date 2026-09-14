"""FR-07 SLA and Escalation Management.

Reminders at 50% and 80% of the window, escalation on breach, second-level escalation
at 2x, and submitter-triggered manual escalation once the window has elapsed.

CLOCK START — a resolved specification conflict, recorded here rather than buried.
FSD 4.2 step 1 says "an SLA timer starts from the moment of assignment". FSD UC-04
step 1 computes the breach from "creation_date + sla_days". They differ for every
grievance that waits in the nodal officer's manual routing queue, which is exactly the
population most at risk of breaching.

This implementation follows FSD 4.2 (assignment), because the SLA is defined in 1.3 as
"the expected timeframe within which an assigned agency must act" — a department cannot
be held to a clock that ran before the case reached it. The behaviour is switchable via
the `grievance_sla_clock_start` site config key ("assignment" or "creation") so the
decision can be reversed without a code change.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from oan_grievance_service.services import constants as C

CLOCK_START_ASSIGNMENT = "assignment"
CLOCK_START_CREATION = "creation"


def clock_start_mode():
	return frappe.conf.get("grievance_sla_clock_start") or CLOCK_START_ASSIGNMENT


def resolve_policy(service_category):
	"""One policy per service category. Grievance type does not narrow the SLA."""
	policy = frappe.get_all(
		"Grievance SLA Configuration",
		filters={
			"service_category": service_category,
			"active": 1,
		},
		fields=[
			"name",
			"sla_days",
			"auto_escalate",
			"auto_escalation_threshold",
			"top_level_authority",
			"first_response_hours",
			"update_cadence_hours",
			"remand_execution_hours",
			"appeal_window_days",
		],
		limit=1,
	)
	return policy[0] if policy else None


def start_clock(grievance):
	"""Stamp the SLA window onto the grievance. Idempotent."""
	if grievance.sla_due_date:
		return

	policy = resolve_policy(grievance.service_category)
	if not policy or not policy.sla_days:
		return

	if clock_start_mode() == CLOCK_START_CREATION:
		started = get_datetime(grievance.creation)
	else:
		started = now_datetime()

	due = add_days(started, policy.sla_days)
	grievance.db_set("sla_days", policy.sla_days, update_modified=False)
	grievance.db_set("sla_start_at", started, update_modified=False)
	grievance.db_set("sla_due_date", due, update_modified=False)
	arm_escalation(grievance, policy=policy)


def arm_escalation(grievance, policy=None):
	"""Point the escalation clock at the first bump.

	Until a case has escalated once the next bump is a fraction of its window, so this
	is re-run whenever the deadline moves (resume from hold, approved deferral). Once
	the case starts climbing, the rung's own hours own the schedule and the deadline is
	no longer the thing being waited on.

	Turning `auto_escalate` off simply leaves the clock unarmed. That is the whole
	switch: the batch selects on `next_escalation_at`, so a null is invisible to it,
	and no per-case policy lookup is needed at escalation time. The SLA window, the
	reminders and the compliance reporting all carry on untouched.
	"""
	if grievance.escalated or not grievance.sla_due_date:
		return

	policy = policy or resolve_policy(grievance.service_category)
	if not policy or not policy.auto_escalate:
		return disarm_escalation(grievance)

	start = get_datetime(grievance.sla_start_at) if grievance.sla_start_at else None
	due = get_datetime(grievance.sla_due_date)
	threshold = policy.auto_escalation_threshold or 100

	if start and 0 < threshold < 100:
		# Hand the case up before the deadline, while there is still time to save it.
		consumed = (due - start).total_seconds() * threshold / 100
		at = add_to_date(start, seconds=int(consumed))
	else:
		at = due

	grievance.db_set("next_escalation_at", at, update_modified=False)


def paused_statuses():
	configured = frappe.conf.get("grievance_sla_paused_statuses")
	return frozenset(configured) if configured else C.SLA_PAUSED_STATUSES


def open_hold_seconds(grievance):
	"""Seconds in the hold that is still running. Zero when the clock is not paused."""
	if not grievance.on_hold_since:
		return 0
	return max(0, int((now_datetime() - get_datetime(grievance.on_hold_since)).total_seconds()))


def pause_clock(grievance):
	"""Stamp the start of a hold. Idempotent — pausing an already-held case is a no-op."""
	if not grievance.sla_due_date or grievance.on_hold_since:
		return
	grievance.db_set("on_hold_since", now_datetime(), update_modified=False)


def resume_clock(grievance):
	"""Bank the hold and push the deadline out by the same amount.

	Spec section 2: two writes total per hold — one on pause, one here. The reminder flags
	are deliberately left alone, because consumed percentage is unchanged across a resume:
	the window and the elapsed time both move by the hold duration.
	"""
	if not grievance.on_hold_since:
		return

	held = open_hold_seconds(grievance)
	grievance.db_set("total_hold_time", (grievance.total_hold_time or 0) + held, update_modified=False)
	grievance.db_set("on_hold_since", None, update_modified=False)

	if held and grievance.sla_due_date:
		grievance.db_set(
			"sla_due_date",
			add_to_date(get_datetime(grievance.sla_due_date), seconds=held),
			update_modified=False,
		)
		# The escalation clock is pushed by the same amount rather than re-armed, so a
		# rung that was part-way through its own hours keeps the remainder instead of
		# being overtaken the moment the case comes off hold.
		if grievance.next_escalation_at:
			grievance.db_set(
				"next_escalation_at",
				add_to_date(get_datetime(grievance.next_escalation_at), seconds=held),
				update_modified=False,
			)
	return held


def consumed_percent(grievance):
	"""FSD 3.11.4: the SLA tracker's consumed percentage, excluding hold time.

	`sla_due_date` has already been pushed out by every banked hold, so subtracting the
	bank from both sides recovers the original window and the time actually spent with the
	department. An open hold is subtracted from the elapsed side only, because the due date
	does not move until the case resumes.
	"""
	if not (grievance.sla_start_at and grievance.sla_due_date):
		return 0
	start = get_datetime(grievance.sla_start_at)
	due = get_datetime(grievance.sla_due_date)
	banked = grievance.total_hold_time or 0

	window = (due - start).total_seconds() - banked
	if window <= 0:
		return 100

	elapsed = (now_datetime() - start).total_seconds() - banked - open_hold_seconds(grievance)
	return max(0, min(round(elapsed / window * 100), 999))


def extend_for_deferral(grievance, additional_days):
	"""FSD 3.11.7: an approved deferral pushes the due date out."""
	if not grievance.sla_due_date:
		return
	grievance.db_set(
		"sla_due_date",
		add_days(get_datetime(grievance.sla_due_date), additional_days),
		update_modified=False,
	)
	# The window moved, so the old reminders are no longer the right ones to suppress.
	grievance.db_set("reminder_50_sent", 0, update_modified=False)
	grievance.db_set("reminder_80_sent", 0, update_modified=False)

	# A case already climbing keeps its rung's remaining hours, shifted by the granted
	# days; one that has not escalated yet is re-armed against the new deadline.
	if grievance.escalated and grievance.next_escalation_at:
		grievance.db_set(
			"next_escalation_at",
			add_days(get_datetime(grievance.next_escalation_at), additional_days),
			update_modified=False,
		)
	else:
		arm_escalation(grievance)


# Each department record keeps one named slot per rung. Last resort, when no RBAC
# assignment covers the case's department and area.
DEPARTMENT_SLOT_BY_LEVEL = {
	"nodal_officer": "nodal_officer",
	"senior_nodal_officer": "senior_officer",
	"department_head": "head_of_dept",
}


def escalation_chain():
	"""The active rungs, most junior first. The ordering is data, not an enum."""
	return frappe.get_all(
		"Grievance Role Level",
		filters={"is_active": 1},
		fields=["name", "level_order", "escalation_hours"],
		order_by="level_order asc",
	)


def current_level_of(user):
	"""The rung a user sits on, read from their RBAC assignment.

	The rung is deliberately not stored on the grievance. A case sits at whatever level
	its current assignee occupies, so escalating *is* the reassignment and the two can
	never disagree. This follows DIGIT PGR, which keys escalation off the assignee's
	designation rather than a counter on the complaint.
	"""
	if not user:
		return None

	today = frappe.utils.today()
	query = """
		SELECT c.role_level
		FROM `tabGrievance RBAC Assignment Officer` c
		JOIN `tabGrievance RBAC Assignment` p ON p.name = c.parent
		WHERE c.user = %(user)s
		  AND c.active = 1
		  AND p.active = 1
		  AND p.effective_from <= %(today)s
		  AND (p.effective_to IS NULL OR p.effective_to = '' OR p.effective_to >= %(today)s)
		  AND c.role_level IS NOT NULL
		  AND c.role_level != ''
		ORDER BY c.is_primary DESC, p.modified DESC
		LIMIT 1
	"""
	try:
		rows = frappe.db.sql(query, {"user": user, "today": today}, as_dict=True)
	except Exception:
		return None
	return rows[0].role_level if rows else None


def next_level_above(level_code):
	"""The next active rung up, or None at the top of the chain.

	An unknown or absent level enters at the most junior rung, so a case held by
	someone outside the chain still has somewhere to go.
	"""
	chain = escalation_chain()
	if not chain:
		return None
	for index, rung in enumerate(chain):
		if rung.name == level_code:
			return chain[index + 1] if index + 1 < len(chain) else None
	return chain[0]


def resolve_officer_for_level(grievance, level):
	"""Who holds `level` for this case's department and area.

	A named `reports_to` wins, because a supervisor the officer actually reports to
	beats a role lookup that only knows the rung. Then RBAC assignments scoped to the
	department and area, then the department's own slot for that rung.
	"""
	supervisor = get_officer_supervisor(
		grievance.assigned_to,
		department=grievance.assigned_dept,
		administrative_area=grievance.administrative_area,
	)
	if supervisor and current_level_of(supervisor) == level.name:
		return supervisor

	from oan_grievance_service.permissions import find_officer_by_role_level

	officer = find_officer_by_role_level(
		level.name,
		department=grievance.assigned_dept,
		administrative_area=grievance.administrative_area,
	)
	if officer:
		return officer

	if not grievance.assigned_dept:
		return None

	slot = DEPARTMENT_SLOT_BY_LEVEL.get(level.name)
	return (
		frappe.db.get_value("Grievance Department", grievance.assigned_dept, slot) if slot else None
	) or frappe.db.get_value("Grievance Department", grievance.assigned_dept, "head_of_dept")


def get_officer_supervisor(user, department=None, administrative_area=None):
	"""Find direct supervisor (reports_to) configured on the officer's RBAC assignment."""
	if not user:
		return None

	today = frappe.utils.today()
	query = """
		SELECT c.reports_to, p.department_scope, p.administrative_area_scope
		FROM `tabGrievance RBAC Assignment Officer` c
		JOIN `tabGrievance RBAC Assignment` p ON p.name = c.parent
		WHERE c.user = %(user)s
		  AND c.active = 1
		  AND p.active = 1
		  AND p.effective_from <= %(today)s
		  AND (p.effective_to IS NULL OR p.effective_to = '' OR p.effective_to >= %(today)s)
		  AND c.reports_to IS NOT NULL
		  AND c.reports_to != ''
		ORDER BY c.is_primary DESC, p.modified DESC
	"""
	try:
		rows = frappe.db.sql(query, {"user": user, "today": today}, as_dict=True)
	except Exception:
		return None

	if not rows:
		return None

	target_lft = None
	if administrative_area:
		target_lft = frappe.db.get_value("Grievance Administrative Area", administrative_area, "lft")

	for r in rows:
		if department and r.department_scope and r.department_scope != department:
			continue
		if target_lft is not None and r.administrative_area_scope:
			area_bounds = frappe.db.get_value(
				"Grievance Administrative Area", r.administrative_area_scope, ["lft", "rgt"], as_dict=True
			)
			if area_bounds and area_bounds.lft is not None and area_bounds.rgt is not None:
				if not (area_bounds.lft <= int(target_lft) <= area_bounds.rgt):
					continue
		return r.reports_to

	return rows[0].reports_to


def disarm_escalation(grievance):
	"""Stop the ladder. The case has nowhere left to climb."""
	if grievance.next_escalation_at:
		grievance.db_set("next_escalation_at", None, update_modified=False)
	return None


def escalate(grievance, trigger, reason=None, reassign=True):
	"""FSD 3.7: move the case one rung up the chain and re-arm the clock.

	Escalating is the reassignment: there is no level stored on the grievance, so the
	rung is read from whoever holds it and written back by handing it to someone else.
	The chain terminates on its own - no rung above, or the walk returning the person
	already holding the case, which is DIGIT PGR's `isNewAssigneeSameAsPreviousAssignee`
	check and means we have run out of hierarchy.

	`escalated` stays a flag, never a status, so the lifecycle stage is untouched.
	Returns the user the case was handed to, or None when it could not move.
	"""
	from oan_grievance_service.services import notifications

	level = next_level_above(current_level_of(grievance.assigned_to))
	if not level:
		return disarm_escalation(grievance)

	target = resolve_officer_for_level(grievance, level)
	if not target or target == grievance.assigned_to:
		return disarm_escalation(grievance)

	# First bump reads as the breach itself; every later one is the chain climbing.
	event = C.EVENT_SLA_BREACH_L1 if not grievance.escalated else C.EVENT_SLA_BREACH_L2

	if reassign:
		grievance.db_set("assigned_to", target, update_modified=False)
	grievance.db_set("escalated", 1, update_modified=False)

	# The rung the case just landed on owns the next deadline. No hours means this is a
	# terminal rung and the ladder stops here.
	hours = level.escalation_hours or 0
	grievance.db_set(
		"next_escalation_at",
		add_to_date(now_datetime(), hours=hours) if hours else None,
		update_modified=False,
	)

	notifications.queue(grievance, event, recipient_override=target)
	return target


def clear_escalation(grievance):
	"""FSD 4.3: once a structured response is submitted the flag is cleared.

	Only the flag. `next_escalation_at` is left running on purpose: a response resets
	the badge, not the obligation, and an officer who answers and then sits on the case
	again must still be overtaken. Clearing it here is what used to make a second breach
	permanently silent.
	"""
	if not grievance.escalated:
		return
	grievance.db_set("escalated", 0, update_modified=False)


def manual_escalate(grievance, reason, by_submitter=True):
	"""FSD 3.7: the submitter may escalate once the SLA window has elapsed."""
	if not grievance.sla_due_date:
		frappe.throw(_("This grievance has no SLA window yet, so it cannot be escalated."))
	if get_datetime(grievance.sla_due_date) > now_datetime():
		frappe.throw(_("The SLA window has not elapsed yet, so escalation is not available."))

	target = escalate(
		grievance,
		trigger="Submitter" if by_submitter else "Officer",
		reason=reason,
	)
	if not target:
		frappe.throw(
			_("This grievance is already with the highest authority, so it cannot be escalated further.")
		)
	return target
