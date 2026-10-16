# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Grievance is no longer a submittable doctype, and drafts are gone.

A case is filed by its insert, straight into Submitted, so every row is now docstatus 0.
Drafts that were saved but never submitted have no ticket and no place in the new
lifecycle; they are deleted with their uploads. after_migrate then rebuilds the
Grievance Workflow without its Draft state.
"""

import frappe


def execute():
	drafts = frappe.get_all("Grievance", filters={"docstatus": 0, "workflow_state": "Draft"}, pluck="name")
	for name in drafts:
		# Each attachment row's on_trash removes its own File.
		for attachment in frappe.get_all("Grievance Attachment", filters={"grievance": name}, pluck="name"):
			frappe.delete_doc("Grievance Attachment", attachment, force=True, ignore_permissions=True)
		for file in frappe.get_all(
			"File", filters={"attached_to_doctype": "Grievance", "attached_to_name": name}, pluck="name"
		):
			frappe.delete_doc("File", file, force=True, ignore_permissions=True)
		frappe.delete_doc("Grievance", name, force=True, ignore_permissions=True)

	frappe.db.sql("update `tabGrievance` set docstatus = 0 where docstatus != 0")
