# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Priority keeps one value, Urgent: clear Normal and High from SLA rules and cases (STG-514).

Priority now follows urgency: Urgent for an urgent rule or case, blank for any other. So:

- a Grievance SLA Rule gets Urgent when `is_urgent` is set, blank otherwise (a non-urgent rule
  that carried High, the harvest marketing peak's, keeps its window and days but no priority);
- a Grievance gets Urgent when its `handling_class` is Urgent, blank otherwise.

Both are fields the system derives and nobody edits (a case's priority is read only and its
save refuses a change to it), so they are set in two bulk updates rather than a save per record,
which on a case would also trip its guards. Cases keep their handling class and deadline.
"""

import frappe
from oan_auth_service.api import operational_log as oplog

from oan_grievance_service.services import constants as C


def execute():
	frappe.reload_doc("grievance_sla", "doctype", "grievance_sla_rule")
	frappe.reload_doc("grievance_management", "doctype", "grievance")

	rule = frappe.qb.DocType("Grievance SLA Rule")
	rules = _count(rule, rule.priority.isnotnull() & (rule.priority != C.PRIORITY_URGENT))
	frappe.qb.update(rule).set(rule.priority, C.PRIORITY_URGENT).where(rule.is_urgent == 1).run()
	frappe.qb.update(rule).set(rule.priority, None).where(rule.is_urgent == 0).run()

	case = frappe.qb.DocType("Grievance")
	cases = _count(case, case.priority.isnotnull() & (case.priority != C.PRIORITY_URGENT))
	frappe.qb.update(case).set(case.priority, C.PRIORITY_URGENT).where(
		case.handling_class == C.HANDLING_URGENT
	).run()
	frappe.qb.update(case).set(case.priority, None).where(
		(case.handling_class != C.HANDLING_URGENT) | case.handling_class.isnull()
	).run()

	oplog.emit("patch.priority_urgent_only", rules_cleared=rules, cases_cleared=cases)


def _count(table, condition) -> int:
	"""How many rows still hold Normal, High or another value that is not Urgent."""
	from frappe.query_builder.functions import Count

	return frappe.qb.from_(table).select(Count("*")).where(condition).run()[0][0]
