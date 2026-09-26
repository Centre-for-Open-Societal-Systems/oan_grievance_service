"""FR-07 SLA and Escalation Management.

Reminders at 50% and 80% of the window, and escalation up the role-level chain on
breach.

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

from dataclasses import dataclass

import frappe
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C

CLOCK_START_ASSIGNMENT = "assignment"
CLOCK_START_CREATION = "creation"


@dataclass(frozen=True, slots=True)
class SLAPolicy:
	"""Resolved SLA policy configuration for a service category."""

	name: str
	sla_days: int
	auto_escalate: bool
	auto_escalation_threshold: int
	first_response_hours: int | None = None
	update_cadence_hours: int | None = None
	remand_execution_hours: int | None = None
	appeal_window_days: int | None = None


def clock_start_mode():
	return frappe.conf.get("grievance_sla_clock_start") or CLOCK_START_ASSIGNMENT


def resolve_policy(service_category) -> SLAPolicy | None:
	"""One policy per service category. Grievance type does not narrow the SLA."""
	rows = frappe.get_all(
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
			"first_response_hours",
			"update_cadence_hours",
			"remand_execution_hours",
			"appeal_window_days",
		],
		limit=1,
	)
	if not rows:
		return None
	r = rows[0]
	return SLAPolicy(
		name=r.name,
		sla_days=r.sla_days or 0,
		auto_escalate=bool(r.auto_escalate),
		auto_escalation_threshold=r.auto_escalation_threshold or 100,
		first_response_hours=r.first_response_hours,
		update_cadence_hours=r.update_cadence_hours,
		remand_execution_hours=r.remand_execution_hours,
		appeal_window_days=r.appeal_window_days,
	)


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
	grievance.db_set(
		{
			"sla_days": policy.sla_days,
			"sla_start_at": started,
			"sla_due_date": due,
		},
		update_modified=False,
	)
	arm_escalation(grievance, policy=policy)


def arm_escalation(grievance, policy):
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
	if not policy or not policy.auto_escalate:
		grievance.db_set("next_escalation_at", None, update_modified=False)
		return

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
	on_hold = grievance.get("on_hold_since")
	if not on_hold:
		return 0
	return max(0, int((now_datetime() - get_datetime(on_hold)).total_seconds()))


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
	updates = {
		"total_hold_time": (grievance.total_hold_time or 0) + held,
		"on_hold_since": None,
	}

	if held and grievance.sla_due_date:
		updates["sla_due_date"] = add_to_date(get_datetime(grievance.sla_due_date), seconds=held)
		# The escalation clock is pushed by the same amount rather than re-armed, so a
		# rung that was part-way through its own hours keeps the remainder instead of
		# being overtaken the moment the case comes off hold.
		if grievance.next_escalation_at:
			updates["next_escalation_at"] = add_to_date(
				get_datetime(grievance.next_escalation_at), seconds=held
			)

	grievance.db_set(updates, update_modified=False)
	return held


def consumed_percent(grievance):
	"""FSD 3.11.4: the SLA tracker's consumed percentage, excluding hold time."""
	start_val = grievance.get("sla_start_at")
	due_val = grievance.get("sla_due_date")
	if not (start_val and due_val):
		return 0
	start = get_datetime(start_val)
	due = get_datetime(due_val)
	banked = grievance.get("total_hold_time") or 0

	window = (due - start).total_seconds() - banked
	if window <= 0:
		return 100

	elapsed = (now_datetime() - start).total_seconds() - banked - open_hold_seconds(grievance)
	return max(0, min(round(elapsed / window * 100), 999))


def escalation_chain():
	"""The active rungs, most junior first. Read from the master table with caching."""
	from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
		GrievanceRoleLevel,
	)

	return GrievanceRoleLevel.get_chain()


def current_level_of(user):
	"""The rung a user sits on, read from their RBAC assignment.

	The rung is deliberately not stored on the grievance. A case sits at whatever level
	its current assignee occupies, so escalating *is* the reassignment and the two can
	never disagree. This follows DIGIT PGR, which keys escalation off the assignee's
	designation rather than a counter on the complaint.
	"""
	if not user:
		return None

	from oan_grievance_service.permissions import query_active_officer_assignments

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

	from oan_grievance_service.permissions import is_in_area_subtree, query_active_officer_assignments

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


def find_higher_authority(grievance):
	"""Who the case goes to next, and the rung that person sits on.

	1. The assignee's direct supervisor (reports_to), when they sit higher up the chain
	   or hold no rung at all.
	2. Otherwise whoever holds the next rung for the case's department and area.

	Returns (user, rung) or (None, None) when the case has nowhere left to climb.
	"""
	chain = escalation_chain()
	if not chain:
		return None, None

	rank = {rung.name: index for index, rung in enumerate(chain)}
	current_assignee = grievance.assigned_to
	department = grievance.assigned_dept
	area = grievance.administrative_area

	current_level = current_level_of(current_assignee)
	current_rank = rank.get(current_level, -1)
	if current_level and current_level not in rank:
		# Held on a rung that has since been deactivated: nowhere defined to climb to.
		return None, None
	if current_rank + 1 >= len(chain):
		return None, None
	next_level = chain[current_rank + 1]

	supervisor = get_officer_supervisor(current_assignee, department=department, administrative_area=area)
	if supervisor and supervisor != current_assignee:
		sup_level = current_level_of(supervisor)
		if not sup_level:
			return supervisor, next_level
		if rank.get(sup_level, -1) > current_rank:
			return supervisor, chain[rank[sup_level]]

	from oan_grievance_service.permissions import find_officer_by_role_level

	officer = find_officer_by_role_level(next_level.name, department=department, administrative_area=area)
	if not officer:
		frappe.log_error(
			title=f"Grievance escalation rung unstaffed: {next_level.name}",
			message=(
				f"No active Grievance RBAC Assignment holds role level '{next_level.name}' for "
				f"department '{department}' and administrative area '{area}'. "
				f"Grievance {grievance.name} cannot escalate past this rung until an assignment covers it."
			),
		)
		return None, None
	if officer == current_assignee:
		return None, None

	return officer, next_level


def escalate(grievance, reason=None, reassign=True):
	"""Move the case one rung up the chain and re-arm the clock.

	`escalated` stays a flag, never a status, so the lifecycle stage is untouched.
	Returns the user the case was handed to, or None when it could not move.
	"""
	from oan_grievance_service.services import notifications

	target, level = find_higher_authority(grievance)
	if not target:
		grievance.db_set("next_escalation_at", None, update_modified=False)
		return None

	# The rung the case just landed on owns the next deadline. No hours means this is a
	# terminal rung and the ladder stops here.
	hours = level.escalation_hours or 0
	updates = {
		"escalated": 1,
		"next_escalation_at": add_to_date(now_datetime(), hours=hours) if hours else None,
	}
	if reassign:
		updates["assigned_to"] = target
	grievance.db_set(updates, update_modified=False)

	body = f"Case escalated to {target} ({level.level_name or level.name})"
	if reason:
		body += f": {reason}"
	GrievanceTimeline.record(
		grievance=grievance.name,
		entry_type="escalation",
		is_internal=False,
		body=body,
		author_user=frappe.session.user if frappe.session.user != "Guest" else None,
	)

	notifications.queue(grievance, C.EVENT_SLA_BREACH, recipient_override=target)
	return target


def clear_escalation(grievance):
	"""FSD 4.3: once a structured response is submitted the flag is cleared.

	Only the flag. `next_escalation_at` is left running on purpose: a response resets
	the badge, not the obligation, and an officer who answers and then sits on the case
	again must still be overtaken.
	"""
	if not grievance.escalated:
		return
	grievance.db_set("escalated", 0, update_modified=False)
