# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Bring grievances filed before the Workflow into it.

Two things a pre-workflow row lacks. `workflow_state`, which is now the field the
engine drives and `status` mirrors; and a `docstatus` that matches its state, since
every state past Draft is a submitted document and Rejected a cancelled one. Without
this the engine would see a Closed case as a Draft and offer to Submit it.

Written straight to the table: the controller's move guards and history rows are
for moves, and this is not one -- each case stays exactly where it was.
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance")

	docstatus_for = {C.DRAFT: 0, C.REJECTED: 2}
	for status in frappe.get_all("Grievance", distinct=True, pluck="status"):
		if not status:
			continue
		frappe.db.sql(
			"""update `tabGrievance`
			set workflow_state = %(status)s, docstatus = %(docstatus)s
			where status = %(status)s and ifnull(workflow_state, '') = ''""",
			{"status": status, "docstatus": docstatus_for.get(status, 1)},
		)
