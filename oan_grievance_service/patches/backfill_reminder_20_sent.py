# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Mark the new 20% SLA reminder as sent on cases already past it (STG-516).

The `reminder_20_sent` column arrives as 0 on every case. Without this, the first sweep
after the deploy would send a "20% of the window" reminder on every open case whose 50% or
80% reminder has already gone out, which is late and misleading. A case still between 20%
and 50% keeps the flag cleared and is reminded once, as it should be.
"""

import frappe


def execute():
	grievance = frappe.qb.DocType("Grievance")
	(
		frappe.qb.update(grievance)
		.set(grievance.reminder_20_sent, 1)
		.where((grievance.reminder_50_sent == 1) | (grievance.reminder_80_sent == 1))
		.where(grievance.reminder_20_sent == 0)
	).run()
