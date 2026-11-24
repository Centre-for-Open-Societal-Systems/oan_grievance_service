# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Carry the old per-threshold reminder flags into `last_reminder_percent` (STG-516).

A case used to record each reminder in its own check (`reminder_50_sent`, `reminder_80_sent`).
It now records the highest threshold sent, so a new threshold needs no new column. The new
column arrives as 0 on every case; without this, the first sweep after the deploy would resend
every reminder already sent in the current window, and send the new 20% one late on cases past
50%. A case with neither flag set stays at 0 and gets each reminder once as it comes due.

The old columns are kept by Frappe and read here once; nothing else reads them.
"""

import frappe


def execute():
	grievance = frappe.qb.DocType("Grievance")
	# Lowest first, so a case that sent both ends at 80.
	for flag, percent in (("reminder_50_sent", 50), ("reminder_80_sent", 80)):
		if not frappe.db.has_column("Grievance", flag):
			continue
		(
			frappe.qb.update(grievance)
			.set(grievance.last_reminder_percent, percent)
			.where(grievance[flag] == 1)
			.where(grievance.last_reminder_percent < percent)
		).run()
