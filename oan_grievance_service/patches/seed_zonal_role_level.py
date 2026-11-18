# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Seed the L3 Zonal Head role level into Grievance Role Level.

Inserts the intermediate zonal_head rung at level_order 25 between senior_nodal_officer (20)
and department_head (30), enabling 4-level escalation: Kebele (L1) -> Woreda (L2) ->
Zone (L3) -> Region (L4).
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	if not frappe.db.exists("Grievance Role Level", "zonal_head"):
		frappe.get_doc(
			{
				"doctype": "Grievance Role Level",
				"level_code": "zonal_head",
				"level_name": "Zonal Head",
				"level_order": 25,
				"escalation_hours": 24,
				"description": "L3. Zonal-level coordination and escalation point.",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
	frappe.cache.delete_value("grievance_role_level_chain")
