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
		"status": ["not in", ["Closed", "Rejected", "Draft"]],
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
		percent = sla.consumed_percent(row)

		if percent >= 80 and not row.reminder_80_sent:
			grievance = frappe.get_doc("Grievance", row.name)
			notifications.queue(grievance, C.EVENT_SLA_REMINDER_80)
			notifications.queue(grievance, C.EVENT_SLA_AT_RISK)
			grievance.db_set("reminder_80_sent", 1, update_modified=False)
			sent += 1
		elif percent >= 50 and not row.reminder_50_sent:
			grievance = frappe.get_doc("Grievance", row.name)
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
			"status": ["not in", ["Closed", "Rejected", "Draft"]],
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
			"status": "Pending Submitter",
			"confirmation_deadline": ["<", now_datetime()],
		},
		pluck="name",
	)

	for name in expired:
		grievance = frappe.get_doc("Grievance", name)
		grievance.db_set("closure_reason", "Closed - no objection received", update_modified=False)
		lifecycle.transition(
			grievance,
			"Auto Close",
			note="Closed - no objection received",
			automated=True,
			notify=False,
			closure_type="auto_closed",
		)
		notifications.queue(grievance, C.EVENT_AUTO_CLOSED)

	return len(expired)


def dispatch_notifications():
	"""FR-08: drain the notification queue."""
	return notifications.dispatch_queued()


def scan_pending_attachments():
	"""Drain the attachment scan queue.

	Nothing is served to an officer while a row is still Pending, so a backlog
	here is a usability problem rather than a safety one.
	"""
	from oan_grievance_service.services import scanning

	return scanning.scan_pending()


def purge_expired_drafts():
	"""Daily: clear abandoned drafts that expired without being submitted."""
	from oan_grievance_service.api.v1 import draft

	return draft.purge_expired_drafts()


def refresh_dashboard_projection():
	"""FR-09 / STG-330: rebuild the dashboard reporting projection.

	Dashboard Statistics API reads only this projection so request paths never
	scan the live Grievance table for national-scale aggregates. Wired hourly;
	admins can also trigger via ``POST /api/v1/dashboard-statistics/refresh``.
	"""
	from oan_grievance_service.services import dashboard_stats

	return dashboard_stats.refresh_projection()


def hourly():
	"""Entry point wired to the hourly scheduler event."""
	send_sla_reminders()
	scan_pending_attachments()
	escalate_breached()
	dispatch_notifications()
	refresh_dashboard_projection()


def daily():
	"""Entry point wired to the daily scheduler event."""
	auto_close_expired()
	purge_expired_drafts()
