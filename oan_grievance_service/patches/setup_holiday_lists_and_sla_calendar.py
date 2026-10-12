# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Setup standard holiday lists, link active SLA configurations, and standardize escalation timeline entries."""

import re

import frappe

from oan_grievance_service.grievance_sla.doctype.grievance_holiday_list.grievance_holiday_list import (
	clear_holiday_cache,
)
from oan_grievance_service.setup.install import seed_holiday_list


def execute():
	frappe.reload_doc("grievance_sla", "doctype", "grievance_holiday")
	frappe.reload_doc("grievance_sla", "doctype", "grievance_holiday_list")
	frappe.reload_doc("grievance_sla", "doctype", "grievance_sla_configuration")
	frappe.reload_doc("grievance_management", "doctype", "grievance")

	# 1. Seed standard Ethiopian holiday list if absent
	seed_holiday_list()

	# 2. Link default holiday list to active SLA configurations that have no list set
	default_list = frappe.db.get_value("Grievance Holiday List", {"is_default": 1}, "name")
	if default_list:
		frappe.db.sql(
			"""
			UPDATE `tabGrievance SLA Configuration`
			SET holiday_list = %s
			WHERE ifnull(holiday_list, '') = ''
			""",
			(default_list,),
		)

	# 3. Clean up historical escalation timeline entries to display role rather than individual user names/emails
	escalations = frappe.get_all(
		"Grievance Timeline",
		filters={"entry_type": "escalation"},
		fields=["name", "body"],
	)
	for row in escalations:
		if not row.body or "Case escalated to " not in row.body:
			continue
		# If formatted as "Case escalated to <user> (<Role>)", simplify to "Case escalated to <Role>"
		new_body = re.sub(r"Case escalated to [^\(:]+\s*\(([^)]+)\)", r"Case escalated to \1", row.body)
		if new_body != row.body:
			frappe.db.set_value("Grievance Timeline", row.name, "body", new_body, update_modified=False)

	# 4. Invalidate cached holiday lookups
	clear_holiday_cache()
