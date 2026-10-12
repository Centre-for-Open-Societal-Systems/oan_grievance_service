# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Remove obsolete 'Pending Submitter' state timers.

'Pending Submitter' was removed from the Grievance Workflow in favor of direct
resolution and the Grievance Feedback model. Any state timers left on an SLA
Configuration referencing 'Pending Submitter' will fail document validation
on save.
"""

import frappe


def execute():
	frappe.db.delete("Grievance State Timer", {"workflow_state": "Pending Submitter"})
