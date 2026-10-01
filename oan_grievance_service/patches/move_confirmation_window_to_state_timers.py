# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Carry the confirmation window over to the generic state timer.

`confirmation_deadline` becomes `state_deadline`. The old field was stamped on entering
Pending Submitter and never cleared, so a value left on a case in any other state is
cleared here: it would otherwise fire that state's timer.

`appeal_window_days` on an SLA Configuration becomes a Pending Submitter row in its
State Timers, closing the case when the window runs out, as it did before.
"""

import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	if frappe.db.has_column("Grievance", "confirmation_deadline"):
		rename_field("Grievance", "confirmation_deadline", "state_deadline")
		frappe.db.sql(
			"update `tabGrievance` set state_deadline = null where workflow_state != 'Pending Submitter'"
		)

	if not frappe.db.has_column("Grievance SLA Configuration", "appeal_window_days"):
		return

	windows = frappe.db.sql(
		"""select name, appeal_window_days from `tabGrievance SLA Configuration`
		where ifnull(appeal_window_days, 0) > 0""",
		as_dict=True,
	)
	for row in windows:
		config = frappe.get_doc("Grievance SLA Configuration", row.name)
		if any(t.workflow_state == "Pending Submitter" for t in config.state_timers):
			continue
		config.append(
			"state_timers",
			{
				"workflow_state": "Pending Submitter",
				"hours": row.appeal_window_days * 24,
				"on_expiry": "Auto Close",
			},
		)
		config.save(ignore_permissions=True)
