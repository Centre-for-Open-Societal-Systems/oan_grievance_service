# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Apply the Routing Matrix's Sensitive, Urgent and season columns to the types a site already holds.

A new site gets the same values from the seed (`openg2p-data`, oan-seed/grievance). This is for
a site whose types were seeded before STG-514: the new columns arrive unset, so no type would be
sensitive or urgent until someone set them. It only fills what is missing: a season that exists is
left alone, and a type that already follows a season (an administrator's choice) is not changed.

Every write is a document save, so the type's own validation runs (an Urgent type needs an active,
Urgent season) and the change is in the type's version history. A type that validation refuses
is left as it is and logged, rather than forced. Cases already filed keep the class they have:
the class is fixed at filing, and re-classifying history would change who may read a case.
"""

import frappe
from oan_auth_service.api import operational_log as oplog

from oan_grievance_service.setup import urgency_matrix as matrix


def execute():
	for doctype in (
		"grievance_urgency_season_window",
		"grievance_urgency_season",
		"grievance_type",
		"grievance_administrative_area",
	):
		frappe.reload_doc("grievance_masters", "doctype", doctype)

	for season in matrix.SEASONS:
		if frappe.db.exists("Grievance Urgency Season", season["season_name"]):
			continue
		frappe.get_doc(
			{
				"doctype": "Grievance Urgency Season",
				"is_active": 1,
				**{key: value for key, value in season.items() if key != "windows"},
				"windows": list(season["windows"]),
			}
		).insert(ignore_permissions=True)

	for code in sorted(set(matrix.SENSITIVE_TYPE_CODES) | set(matrix.TYPE_SEASONS)):
		name = frappe.db.get_value("Grievance Type", {"type_code": code})
		if not name:
			continue
		doc = frappe.get_doc("Grievance Type", name)
		changed = False
		if code in matrix.SENSITIVE_TYPE_CODES and not doc.is_sensitive:
			doc.is_sensitive, changed = 1, True
		if code in matrix.TYPE_SEASONS and not doc.urgency_season:
			doc.is_urgent, doc.urgency_season = matrix.TYPE_SEASONS[code]
			changed = True
		if not changed:
			continue
		savepoint = "apply_sensitive_urgent_matrix"
		frappe.db.savepoint(savepoint)
		try:
			doc.save(ignore_permissions=True)
		except frappe.ValidationError:
			frappe.db.rollback(save_point=savepoint)
			frappe.clear_messages()
			oplog.log_error(
				title=f"STG-514 matrix not applied to grievance type {code}",
				message=frappe.get_traceback(),
			)
