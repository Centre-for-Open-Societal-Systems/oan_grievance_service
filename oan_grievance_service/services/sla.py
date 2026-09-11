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
			"top_level_authority",
			"auto_escalation_threshold",
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

	grievance.db_set("sla_days", policy.sla_days, update_modified=False)
	grievance.db_set("sla_start_at", started, update_modified=False)
	grievance.db_set("sla_due_date", add_days(started, policy.sla_days), update_modified=False)


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
	grievance.db_set(
		"sla_deferred_days",
		(grievance.sla_deferred_days or 0) + additional_days,
		update_modified=False,
	)
	# The window moved, so the old reminders are no longer the right ones to suppress.
	grievance.db_set("reminder_50_sent", 0, update_modified=False)
	grievance.db_set("reminder_80_sent", 0, update_modified=False)


ESCALATION_ROLE_LEVELS = {
	"L1": "nodal_officer",
	"L2": "senior_nodal_officer",
	"L3": "department_head",
}


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


def escalate(grievance, level, trigger, reason=None, escalated_by=None, reassign=True):
	"""FSD 3.7 & Docs: escalate along reporting chain, set flag, and notify.

	Escalated is a flag, never a status, so the lifecycle stage is left untouched.
	Target resolution order:
	1. Direct supervisor from officer's reports_to in RBAC assignment (for L1)
	2. Policy top_level_authority (if level == 'L2' and configured)
	3. Dynamic RBAC role_level lookup (find_officer_by_role_level)
	4. Grievance Department fallback
	"""
	from oan_grievance_service.permissions import find_officer_by_role_level
	from oan_grievance_service.services import notifications

	if already_escalated_at(grievance.name, level):
		return None

	target = None
	if level == "L1" and grievance.assigned_to:
		target = get_officer_supervisor(
			grievance.assigned_to,
			department=grievance.assigned_dept,
			administrative_area=grievance.administrative_area,
		)

	policy = resolve_policy(grievance.service_category)
	if not target and level == "L2" and policy and policy.top_level_authority:
		target = policy.top_level_authority

	if not target:
		role_level = ESCALATION_ROLE_LEVELS.get(level, "nodal_officer")
		target = find_officer_by_role_level(
			role_level,
			department=grievance.assigned_dept,
			administrative_area=grievance.administrative_area,
		)

	if not target and grievance.assigned_dept:
		dept_field = "senior_officer" if level == "L2" else "nodal_officer"
		target = frappe.db.get_value(
			"Grievance Department", grievance.assigned_dept, dept_field
		) or frappe.db.get_value("Grievance Department", grievance.assigned_dept, "head_of_dept")

	grievance.db_set("escalated", 1, update_modified=False)
	grievance.db_set("escalation_level", 2 if level == "L2" else 1, update_modified=False)

	if reassign and target and target != grievance.assigned_to:
		grievance.db_set("assigned_to", target, update_modified=False)

	notifications.queue(
		grievance,
		C.EVENT_SLA_BREACH_L2 if level == "L2" else C.EVENT_SLA_BREACH_L1,
		recipient_override=target,
	)
	return True


def already_escalated_at(grievance_name, level):
	event = C.EVENT_SLA_BREACH_L2 if level == "L2" else C.EVENT_SLA_BREACH_L1
	return bool(frappe.db.exists("Grievance Notification Log", {"grievance": grievance_name, "event": event}))


def clear_escalation(grievance):
	"""FSD 4.3: once a structured response is submitted the flag is cleared."""
	if not grievance.escalated:
		return
	grievance.db_set("escalated", 0, update_modified=False)
	grievance.db_set("escalation_level", 0, update_modified=False)


def manual_escalate(grievance, reason, by_submitter=True):
	"""FSD 3.7: the submitter may escalate once the SLA window has elapsed."""
	if not grievance.sla_due_date:
		frappe.throw(_("This grievance has no SLA window yet, so it cannot be escalated."))
	if get_datetime(grievance.sla_due_date) > now_datetime():
		frappe.throw(_("The SLA window has not elapsed yet, so escalation is not available."))

	return escalate(
		grievance,
		level="L1",
		trigger="Submitter" if by_submitter else "Officer",
		reason=reason,
	)
