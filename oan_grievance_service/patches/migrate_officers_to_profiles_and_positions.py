# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Migrate existing officer rows into Grievance Officer Position records.

Generates canonical positions from unique (occupant, role_level, area, designation) groupings
and links desk child rows to their respective positions. Cleans up any deprecated Grievance
Officer Profile records.
"""

import frappe


def execute():
	# Drop deprecated Grievance Officer Profile if present
	if frappe.db.exists("DocType", "Grievance Officer Profile"):
		frappe.delete_doc("DocType", "Grievance Officer Profile", force=True, ignore_permissions=True)

	frappe.reload_doc("grievance_access_control", "doctype", "grievance_officer_position")
	frappe.reload_doc("grievance_access_control", "doctype", "grievance_rbac_assignment_officer")

	# Create Positions for unique groupings and link them
	all_rows = frappe.get_all(
		"Grievance RBAC Assignment Officer",
		fields=[
			"name",
			"parent",
			"user",
			"role_level",
			"administrative_area",
			"designation",
			"active",
			"on_leave",
			"max_open_cases",
			"reports_to",
			"position",
		],
	)

	pos_map = {}
	for r in all_rows:
		if r.position:
			continue
		key = (r.user, r.role_level, r.administrative_area or "", r.designation or "")
		if key not in pos_map:
			desig = r.designation or (r.role_level.replace("_", " ").title() if r.role_level else "Officer")
			area_suffix = f" - {r.administrative_area}" if r.administrative_area else ""
			title = f"{desig}{area_suffix}"
			occupant = r.user if (r.user and frappe.db.exists("User", r.user)) else None
			if occupant and not frappe.db.get_value("User", occupant, "enabled"):
				occupant = None
			try:
				pos = frappe.get_doc(
					{
						"doctype": "Grievance Officer Position",
						"position_title": title[:140],
						"role_level": r.role_level or "nodal_officer",
						"administrative_area": r.administrative_area,
						"designation": r.designation,
						"occupant": occupant,
						"active": r.active if r.active is not None else 1,
						"on_leave": r.on_leave if r.on_leave is not None else 0,
						"max_open_cases": r.max_open_cases or 0,
					}
				).insert(ignore_permissions=True)
				pos_map[key] = pos.name
			except Exception:
				continue

		frappe.db.set_value(
			"Grievance RBAC Assignment Officer",
			r.name,
			"position",
			pos_map[key],
			update_modified=False,
		)

	# Link reports_to_position
	for r in all_rows:
		key = (r.user, r.role_level, r.administrative_area or "", r.designation or "")
		if r.reports_to and key in pos_map:
			pos_name = pos_map[key]
			sup_pos = frappe.db.get_value(
				"Grievance Officer Position", {"occupant": r.reports_to, "active": 1}, "name"
			)
			if sup_pos and sup_pos != pos_name:
				try:
					frappe.db.set_value(
						"Grievance Officer Position",
						pos_name,
						"reports_to_position",
						sup_pos,
						update_modified=False,
					)
				except Exception:
					pass
