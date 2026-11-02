# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Move officer availability from the User custom field onto the officer rows.

Status used to live on User as `grievance_officer_status`, a second source of truth beside the
row's `active` flag. It now lives on the rows: On Leave sets `on_leave`, Inactive clears
`active`. This runs after the model sync, when `on_leave` exists, then drops the custom field
so core User is left as it was.
"""

import frappe

FIELD = "grievance_officer_status"
CUSTOM_FIELD = f"User-{FIELD}"


def execute():
	if frappe.db.has_column("User", FIELD):
		Row = frappe.qb.DocType("Grievance RBAC Assignment Officer")
		User = frappe.qb.DocType("User")
		for status, column in (("On Leave", Row.on_leave), ("Inactive", Row.active)):
			away = frappe.qb.from_(User).select(User.name).where(User[FIELD] == status)
			(
				frappe.qb.update(Row)
				.set(column, 1 if status == "On Leave" else 0)
				.where((Row.parenttype == "Grievance RBAC Assignment") & Row.user.isin(away))
				.run()
			)
	if frappe.db.exists("Custom Field", CUSTOM_FIELD):
		frappe.delete_doc("Custom Field", CUSTOM_FIELD, force=True)
