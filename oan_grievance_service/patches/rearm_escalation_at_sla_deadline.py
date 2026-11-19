# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Move every early-armed first escalation to the case's SLA deadline (STG-516).

`arm_escalation` used to arm the first escalation a threshold share of the way into the
window, so a case could escalate long before its SLA ran out. It now arms at the deadline,
but cases armed before the deploy still carry the early time and the hourly sweep would act
on it. Only cases that have not escalated yet are touched: once a case climbs, its rung's
escalation hours own the schedule.
"""

import frappe


def execute():
	grievance = frappe.qb.DocType("Grievance")
	(
		frappe.qb.update(grievance)
		.set(grievance.next_escalation_at, grievance.sla_due_date)
		.where(grievance.escalated == 0)
		.where(grievance.next_escalation_at.isnotnull())
		.where(grievance.sla_due_date.isnotnull())
		.where(grievance.next_escalation_at < grievance.sla_due_date)
	).run()
