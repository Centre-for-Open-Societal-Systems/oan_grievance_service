# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Carry legacy 'Pending Submitter' confirmation timers over to 'Resolved'.

'Pending Submitter' was removed from the Grievance Workflow in favor of direct
resolution and the Grievance Feedback model. The confirmation window now runs
while in 'Resolved' with an 'Auto Close' transition.

Preserve configured confirmation windows by converting 'Pending Submitter'
timer rows to 'Resolved' when the SLA Configuration does not already have a
'Resolved' timer. Any remaining obsolete 'Pending Submitter' rows are deleted.
"""

import frappe


def execute():
	existing_resolved = set(
		frappe.get_all(
			"Grievance State Timer",
			filters={"parenttype": "Grievance SLA Configuration", "workflow_state": "Resolved"},
			pluck="parent",
		)
	)

	pending_timers = frappe.get_all(
		"Grievance State Timer",
		filters={"parenttype": "Grievance SLA Configuration", "workflow_state": "Pending Submitter"},
		fields=["name", "parent", "hours", "on_expiry"],
		order_by="idx asc",
	)

	converted_parents = set(existing_resolved)
	for timer in pending_timers:
		if timer.parent not in converted_parents:
			frappe.db.set_value(
				"Grievance State Timer",
				timer.name,
				{
					"workflow_state": "Resolved",
					"on_expiry": timer.on_expiry or "Auto Close",
				},
				update_modified=False,
			)
			converted_parents.add(timer.parent)

	frappe.db.delete("Grievance State Timer", {"workflow_state": "Pending Submitter"})
