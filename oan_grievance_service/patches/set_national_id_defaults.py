# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Fill in the National ID Pattern and Format Hint on sites that already saved Grievance Settings.

A Single that has stored values loads as stored, so its JSON defaults never apply and the new
fields read as None. The pattern is read without a fallback, so they must not be empty.
"""

import frappe

DEFAULTS = {
	"national_id_pattern": r"^\d{12}(\d{4})?$",
	"national_id_format_hint": "a 12- or 16-digit number",
}


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance_settings")
	for field, default in DEFAULTS.items():
		if not frappe.db.get_single_value("Grievance Settings", field):
			frappe.db.set_single_value("Grievance Settings", field, default)
