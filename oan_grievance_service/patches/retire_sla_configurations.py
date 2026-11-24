# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Retire Grievance SLA Configuration, moving what it held to where it now lives.

- The state timers go to the Grievance Deferral Policy, which every case shares. Every
  configuration started from the same defaults, so the active one modified last gives the set.
  A policy that already has timers keeps them; one left with none gets the defaults.
- `auto_escalate` and `notify_on_breach` go to the desks serving the category. A desk keeps
  the default (on) unless its category's active configuration had the flag off.

Its own threshold and holiday list are installation-wide now (the policy's threshold and the
default holiday list), and its first-response, update-cadence and remand hours were never read,
so they are dropped. The SLA rules already carry their service category.
"""

import frappe

from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
	seed_state_timers,
)

CONFIG = "Grievance SLA Configuration"
POLICY = "Grievance Deferral Policy"
FLAGS = ("auto_escalate", "notify_on_breach")


def execute():
	if frappe.db.table_exists(CONFIG):
		table = frappe.qb.DocType(CONFIG)
		configs = (
			frappe.qb.from_(table)
			.select(table.name, table.service_category, *(table[field] for field in FLAGS))
			.where(
				(table.active == 1) & (table.service_category.isnotnull()) & (table.service_category != "")
			)
			.orderby(table.modified, order=frappe.qb.desc)
			.orderby(table.name, order=frappe.qb.desc)
		).run(as_dict=True)
		policy = frappe.get_doc(POLICY)
		if configs and not policy.state_timers:
			for row in frappe.get_all(
				"Grievance State Timer",
				filters={"parenttype": CONFIG, "parent": configs[0].name},
				fields=["workflow_state", "hours", "on_expiry"],
				order_by="idx asc",
			):
				policy.append("state_timers", row)
			policy.save(ignore_permissions=True)
		done = set()
		for config in configs:
			if config.service_category in done:
				continue
			done.add(config.service_category)
			for field in FLAGS:
				if not config[field]:
					frappe.db.set_value(
						"Grievance RBAC Assignment",
						{"category_scope": config.service_category},
						field,
						0,
						update_modified=False,
					)
		frappe.db.delete("Grievance State Timer", {"parenttype": CONFIG})
	seed_state_timers()
	frappe.delete_doc("DocType", CONFIG, force=True, ignore_missing=True)
