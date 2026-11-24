# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Relabel role levels to reflect the 4-level Ethiopian administrative hierarchy.

Updates existing rungs:
- nodal_officer -> Kebele Nodal Officer (L1)
- senior_nodal_officer -> Woreda Nodal Officer (L2)
- department_head -> Regional Bureau Head (L4)
"""

import frappe


def execute():
	renames = {
		"nodal_officer": (
			"Kebele Nodal Officer",
			"L1. Kebele-level coordination point for grievances.",
		),
		"senior_nodal_officer": (
			"Woreda Nodal Officer",
			"L2. Woreda-level coordination; first rung above the Kebele nodal officer.",
		),
		"department_head": (
			"Regional Bureau Head",
			"L4. Final internal escalation rung for the region/department.",
		),
	}
	for code, (name, desc) in renames.items():
		if frappe.db.exists("Grievance Role Level", code):
			frappe.db.set_value(
				"Grievance Role Level",
				code,
				{"level_name": name, "description": desc},
				update_modified=False,
			)
	frappe.cache.delete_value("grievance_role_level_chain")
