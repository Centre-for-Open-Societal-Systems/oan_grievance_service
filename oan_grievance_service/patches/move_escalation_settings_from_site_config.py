# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Carry the escalation site-config keys over to Grievance Settings.

`grievance_auto_escalation_enabled` and `grievance_change_request_hours` now live on the
Grievance Settings single, so an administrator can change them without a redeploy. A site
that set either key keeps its value; the keys can then be removed from `site_config.json`.
"""

import frappe

DEFAULT_CHANGE_REQUEST_HOURS = 24


def execute():
	enabled = frappe.conf.get("grievance_auto_escalation_enabled") is not False
	hours = frappe.conf.get("grievance_change_request_hours") or DEFAULT_CHANGE_REQUEST_HOURS
	frappe.reload_doc("grievance_management", "doctype", "grievance_settings")
	# Saved whole: a site that never saved the settings reads every field from its default, and
	# writing only these two would leave the others stored empty instead.
	settings = frappe.get_single("Grievance Settings")
	settings.auto_escalation_enabled = int(enabled)
	settings.change_request_hours = int(hours)
	settings.save(ignore_permissions=True)
