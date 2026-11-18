# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Apply the Routing Matrix's Sensitive, Urgent and season columns to the types a site already holds.

A new site gets the same values from the seed (`openg2p-data`, oan-seed/grievance). This is for
a site whose types were seeded before STG-514: the new columns arrive unset, so no type would be
sensitive or urgent until someone set them. It only fills what is missing: a season that exists is
left alone, and a type that already follows a season (an administrator's choice) is not changed.
Cases already filed keep the class they have (Standard, Normal, not sensitive): the class is
fixed at filing, and re-classifying history would change who may read a case after the fact.
"""

import frappe

from oan_grievance_service.setup import urgency_matrix as matrix


def execute():
	frappe.reload_doc("grievance_masters", "doctype", "grievance_urgency_season_window")
	frappe.reload_doc("grievance_masters", "doctype", "grievance_urgency_season")
	frappe.reload_doc("grievance_masters", "doctype", "grievance_type")
	frappe.reload_doc("grievance_masters", "doctype", "grievance_administrative_area")

	for season in matrix.SEASONS:
		if frappe.db.exists("Grievance Urgency Season", season["season_name"]):
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Urgency Season",
				"is_active": 1,
				**{key: value for key, value in season.items() if key != "windows"},
				"windows": list(season["windows"]),
			}
		)
		doc.insert(ignore_permissions=True)

	for code in matrix.SENSITIVE_TYPE_CODES:
		frappe.db.set_value("Grievance Type", {"type_code": code}, "is_sensitive", 1, update_modified=False)

	for code, (is_urgent, season) in matrix.TYPE_SEASONS.items():
		name = frappe.db.get_value("Grievance Type", {"type_code": code, "urgency_season": ["is", "not set"]})
		if name:
			frappe.db.set_value(
				"Grievance Type",
				name,
				{"is_urgent": is_urgent, "urgency_season": season},
				update_modified=False,
			)
