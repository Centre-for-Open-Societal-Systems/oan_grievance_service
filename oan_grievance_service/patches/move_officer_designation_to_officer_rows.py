# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Move officer designation from the User custom field onto the officer rows.

User is shared by submitters, admins and officers, so an officer's title does not belong on
it. The title now lives on Grievance RBAC Assignment Officer. This runs after the model sync,
when `designation` exists on the rows, copies any title already held on a User to that user's
rows that have none, then drops the custom field so core User is left as it was.
"""

import frappe

FIELD = "grievance_designation"
CUSTOM_FIELD = f"User-{FIELD}"


def execute():
	if frappe.db.has_column("User", FIELD):
		Row = frappe.qb.DocType("Grievance RBAC Assignment Officer")
		User = frappe.qb.DocType("User")
		for user, designation in (
			frappe.qb.from_(User).select(User.name, User[FIELD]).where(User[FIELD].isnotnull()).run()
		):
			if not designation:
				continue
			(
				frappe.qb.update(Row)
				.set(Row.designation, designation)
				.where(
					(Row.parenttype == "Grievance RBAC Assignment")
					& (Row.user == user)
					& (Row.designation.isnull() | (Row.designation == ""))
				)
				.run()
			)
	if frappe.db.exists("Custom Field", CUSTOM_FIELD):
		frappe.delete_doc("Custom Field", CUSTOM_FIELD, force=True)
