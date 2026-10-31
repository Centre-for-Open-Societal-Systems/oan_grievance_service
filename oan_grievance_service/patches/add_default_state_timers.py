# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Give existing SLA Configurations the default Resolved and More Info Needed timers."""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	for name in frappe.get_all("Grievance SLA Configuration", pluck="name"):
		config = frappe.get_doc("Grievance SLA Configuration", name)
		have = {row.workflow_state for row in config.state_timers}
		missing = [t for t in C.DEFAULT_STATE_TIMERS if t[0] not in have]
		for state, hours, on_expiry in missing:
			config.append("state_timers", {"workflow_state": state, "hours": hours, "on_expiry": on_expiry})
		if missing:
			config.save(ignore_permissions=True)
