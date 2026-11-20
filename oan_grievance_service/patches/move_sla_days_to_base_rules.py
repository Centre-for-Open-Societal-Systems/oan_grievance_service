# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Move each category's SLA days from its SLA configuration to a base Grievance SLA Rule (STG-514).

The SLA rules are now the one source of SLA days: the category's base rule (no type, area or
window) holds what `Grievance SLA Configuration.sla_days` held. That field is gone from the
doctype, but Frappe keeps its column, so the value is read from there.

One base rule per category with an active configuration: Standard, Normal, the configuration's
days. A category that already has a base rule keeps it. The rule is named `<category code>-BASE`,
the name the seed (`openg2p-data`) gives it, so loading the seed afterwards adds no second copy.
Each rule is saved through its validation; one that validation refuses is logged and skipped.
Cases already filed keep their deadlines.
"""

import frappe
from oan_auth_service.api import operational_log as oplog

from oan_grievance_service.services import constants as C

RULE = "Grievance SLA Rule"
SAVEPOINT = "move_sla_days_to_base_rules"


def execute():
	frappe.reload_doc("grievance_sla", "doctype", "grievance_sla_rule")
	done = set()
	for config in _configurations():
		category = config.service_category
		if category in done or frappe.db.exists(RULE, {"service_category": category, "is_base": 1}):
			continue
		done.add(category)
		doc = frappe.get_doc(
			{
				"doctype": RULE,
				"service_category": category,
				"rule_label": "Base",
				"sla_days": config.sla_days,
				"is_urgent": 0,
				"priority": C.PRIORITY_NORMAL,
			}
		)
		code = frappe.db.get_value("Grievance Service Category", category, "code")
		name = f"{code}-BASE" if code else None
		frappe.db.savepoint(SAVEPOINT)
		try:
			if name and not frappe.db.exists(RULE, name):
				doc.insert(ignore_permissions=True, set_name=name)
			else:
				doc.insert(ignore_permissions=True)
		except frappe.ValidationError:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.clear_messages()
			oplog.log_error(
				title=f"STG-514 base SLA rule not created for {category}", message=frappe.get_traceback()
			)


def _configurations() -> list:
	"""(service_category, sla_days) of the active configurations, newest first, from the column the
	doctype no longer has. Empty on a site that never had it."""
	if not frappe.db.has_column("Grievance SLA Configuration", "sla_days"):
		return []
	return frappe.db.sql(
		"""select service_category, sla_days from `tabGrievance SLA Configuration`
		where active = 1 and ifnull(service_category, '') != '' and ifnull(sla_days, 0) > 0
		order by modified desc""",
		as_dict=True,
	)
