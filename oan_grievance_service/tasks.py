"""Scheduled jobs. Registered in hooks.py under scheduler_events.

FSD 4.3 describes a background process that continuously monitors open grievances
against their SLA deadlines. FSD 7 requires that batch to finish inside 30 minutes,
so each job filters on an indexed column and touches only the cases that need work.
"""

import frappe
from frappe.utils import now_datetime

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle, notifications, sla


def open_grievances_with_sla(extra_filters=None):
	filters = {
		"status": ["in", list(C.OPEN_STATUSES)],
		"sla_due_date": ["is", "set"],
	}
	if extra_filters:
		filters.update(extra_filters)
	return frappe.get_all(
		"Grievance",
		filters=filters,
		fields=[
			"name",
			"ticket_number",
			"status",
			"sla_start_at",
			"sla_due_date",
			"on_hold_since",
			"total_hold_time",
			"reminder_50_sent",
			"reminder_80_sent",
			"escalated",
			"assigned_dept",
			"assigned_to",
			"service_category",
			"grievance_type",
			"contact_email",
			"contact_mobile",
			"submitter_name",
			"administrative_area",
			"administrative_unit",
			"sla_days",
		],
	)


def send_sla_reminders():
	"""FSD 3.7: reminders to the assigned officer at 50% and 80% of the window."""
	sent = 0
	for row in open_grievances_with_sla():
		# A paused case is waiting on the submitter, so the officer has nothing to be
		# reminded about and the deadline has not moved yet.
		if row.on_hold_since:
			continue
		grievance = frappe.get_doc("Grievance", row.name)
		percent = sla.consumed_percent(grievance)

		if percent >= 80 and not grievance.reminder_80_sent:
			notifications.queue(grievance, C.EVENT_SLA_REMINDER_80)
			notifications.queue(grievance, C.EVENT_SLA_AT_RISK)
			grievance.db_set("reminder_80_sent", 1, update_modified=False)
			sent += 1
		elif percent >= 50 and not grievance.reminder_50_sent:
			notifications.queue(grievance, C.EVENT_SLA_REMINDER_50)
			grievance.db_set("reminder_50_sent", 1, update_modified=False)
			sent += 1

	return sent


def escalate_breached():
	"""FSD 3.7 / 4.3: hand every overdue case one rung up the chain.

	One indexed read on `next_escalation_at` rather than a scan of every open case:
	a grievance carries its own next deadline, so the query is the schedule. Each case
	is escalated inside its own try block, because one unroutable grievance must not
	take the rest of the batch down with it (FSD 7's 30-minute budget assumes the run
	completes).
	"""
	# Site-wide stop, one read per run. Per-category is the `auto_escalate` tick on the
	# SLA configuration, which works by leaving `next_escalation_at` unarmed.
	if frappe.conf.get("grievance_auto_escalation_enabled") is False:
		return 0

	due_now = frappe.get_all(
		"Grievance",
		filters={
			"status": ["in", list(C.OPEN_STATUSES)],
			"next_escalation_at": ["<=", now_datetime()],
			"on_hold_since": ["is", "not set"],
		},
		pluck="name",
	)

	escalated = 0
	for name in due_now:
		try:
			grievance = frappe.get_doc("Grievance", name)
			if sla.escalate(grievance, trigger="System"):
				escalated += 1
		except Exception:
			frappe.log_error(
				title="Grievance escalation failed",
				message=f"{name}\n\n{frappe.get_traceback()}",
			)

	return escalated


def auto_close_expired():
	"""FSD 3.6: close cases whose confirmation window has expired with no response."""
	expired = frappe.get_all(
		"Grievance",
		filters={
			"status": C.PENDING_SUBMITTER,
			"confirmation_deadline": ["<", now_datetime()],
		},
		pluck="name",
	)

	for name in expired:
		grievance = frappe.get_doc("Grievance", name)
		lifecycle.auto_close(grievance)

	return len(expired)


def dispatch_notifications():
	"""FR-08: drain the notification queue."""
	return notifications.dispatch_queued()


def hourly():
	"""Entry point wired to the hourly scheduler event."""
	send_sla_reminders()
	escalate_breached()
	dispatch_notifications()


def daily():
	"""Entry point wired to the daily scheduler event."""
	auto_close_expired()
