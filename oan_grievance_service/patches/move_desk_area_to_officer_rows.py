# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Copy each desk's administrative area onto its officer rows.

An officer is assigned to a region, not to a department desk, so the area moved from
Grievance RBAC Assignment to Grievance RBAC Assignment Officer. This runs after the model
sync, when the new column exists and the old one has not been dropped yet. Rows that
already name an area keep it.
"""

import frappe


def execute():
	if not frappe.db.has_column("Grievance RBAC Assignment", "administrative_area_scope"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabGrievance RBAC Assignment Officer` row
		JOIN `tabGrievance RBAC Assignment` desk ON desk.name = row.parent
		SET row.administrative_area = desk.administrative_area_scope
		WHERE row.parenttype = 'Grievance RBAC Assignment'
		  AND IFNULL(desk.administrative_area_scope, '') != ''
		  AND IFNULL(row.administrative_area, '') = ''
		"""
	)
