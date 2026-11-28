# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Store Grievance Settings' new `reopen_escalation_enabled` as on (STG-521).

A site that saved its settings before the field existed has no value stored for it. The desk
form would show it unticked, and the next save would switch reopen escalation off without anyone
choosing to. A site that never saved its settings reads every field from its default and is left
alone, as is a value someone has already set.
"""

import frappe


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance_settings")
	stored = frappe.db.get_singles_dict("Grievance Settings")
	if stored and stored.get("reopen_escalation_enabled") is None:
		frappe.db.set_single_value("Grievance Settings", "reopen_escalation_enabled", 1)
