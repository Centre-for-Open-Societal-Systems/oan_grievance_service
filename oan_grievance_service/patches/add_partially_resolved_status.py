# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Add the Partially Resolved state and move cases that sit in Resolved only because of a partial answer.

Partially Resolve used to move a case into Resolved, so a case the department had only
partly answered was reported as resolved. A case still in Resolved whose last recorded
move is a Partially Resolve has not been touched since, so it belongs in the new state.
A case that was reopened, closed or resolved again has a later move and stays where it is.
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	from oan_grievance_service.patches import add_default_state_timers
	from oan_grievance_service.setup.install import seed_workflow

	seed_workflow()
	add_default_state_timers.execute()

	for name in frappe.get_all("Grievance", filters={"workflow_state": C.STATE_RESOLVED}, pluck="name"):
		last = frappe.db.get_value(
			"Grievance Status History",
			{"grievance": name},
			["to_status", "action"],
			order_by="timestamp desc, creation desc",
			as_dict=True,
		)
		if last and last.to_status == C.STATE_RESOLVED and last.action == "Partially Resolve":
			frappe.db.set_value(
				"Grievance",
				name,
				{"workflow_state": C.STATE_PARTIALLY_RESOLVED, "status": C.STATE_PARTIALLY_RESOLVED},
				update_modified=False,
			)
