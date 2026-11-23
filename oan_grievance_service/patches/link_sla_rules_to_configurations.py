# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Link each Grievance SLA Rule to the SLA configuration of its category.

A rule overrides its category's SLA configuration, so `sla_configuration` is now what it
belongs to and `service_category` follows it. A rule whose category has no configuration is
left unlinked: it is not served until one is made.
"""

import frappe


def execute():
	frappe.reload_doc("grievance_sla", "doctype", "grievance_sla_rule")
	for category in frappe.get_all(
		"Grievance SLA Rule",
		filters={"sla_configuration": ["is", "not set"]},
		pluck="service_category",
		distinct=True,
	):
		config = frappe.db.get_value(
			"Grievance SLA Configuration",
			{"service_category": category},
			"name",
			order_by="active desc, creation asc",
		)
		if config:
			frappe.db.set_value(
				"Grievance SLA Rule",
				{"service_category": category, "sla_configuration": ["is", "not set"]},
				"sla_configuration",
				config,
				update_modified=False,
			)
