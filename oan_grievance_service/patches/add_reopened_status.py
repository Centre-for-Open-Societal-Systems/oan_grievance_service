# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Add the Reopened state and move cases that are waiting after a reopen into it.

A reopened case used to go straight back to In Progress. One whose last recorded move
is that reopen has not been touched by an officer since, so it is where Reopened now
puts it. A case an officer has already moved on stays where it is.
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	from oan_grievance_service.setup.install import seed_workflow

	seed_workflow()

	moved = []
	for name in frappe.get_all("Grievance", filters={"workflow_state": C.STATE_IN_PROGRESS}, pluck="name"):
		last = frappe.db.get_value(
			"Grievance Status History",
			{"grievance": name},
			["from_status", "to_status", "action"],
			order_by="timestamp desc, creation desc",
			as_dict=True,
		)
		if last and last.from_status == C.STATE_RESOLVED and last.to_status == C.STATE_IN_PROGRESS:
			moved.append(name)

	for name in moved:
		frappe.db.set_value(
			"Grievance",
			name,
			{"workflow_state": C.STATE_REOPENED, "status": C.STATE_REOPENED},
			update_modified=False,
		)
