# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Ensure all Closed grievances have docstatus = 2 (Cancelled).

The initial workflow migration left Closed cases at docstatus = 1.
In the finalised workflow specification, Closed and Rejected are terminal states
with docstatus = 2.
"""

import frappe


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance")
	frappe.db.sql(
		"""UPDATE `tabGrievance`
		SET docstatus = 2
		WHERE status = 'Closed' AND docstatus != 2"""
	)
