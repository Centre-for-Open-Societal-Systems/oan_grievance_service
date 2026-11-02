# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Relabel the department_head rung as Category Admin on sites seeded before the rename.

Seeding only creates missing rungs, so an existing record keeps its old label. The code
`department_head` stays as it is: routing, escalation and the department slots key on it.
"""

import frappe


def execute():
	if frappe.db.get_value("Grievance Role Level", "department_head", "level_name") == "Department Head":
		frappe.db.set_value("Grievance Role Level", "department_head", "level_name", "Category Admin")
