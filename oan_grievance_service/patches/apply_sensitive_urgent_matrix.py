# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Apply the Routing Matrix's Sensitive flags and seasonal rules to a site that already holds the taxonomy.

A new site gets the same values from the seed (`openg2p-data`, oan-seed/grievance). This is for a
site whose types and categories were seeded before STG-514: no type would be sensitive and no
category would have a seasonal rule until someone set them. It only fills what is missing: a
sensitive flag already set stays, and a category that already has rules (an administrator's
choice) is not changed.

Every write is a document save, so the documents' own validation runs and the change is in their
version history. A record that validation refuses is left as it is and logged, rather than forced;
a category's rules are added all together or not at all. Cases already filed keep the class and
SLA they have: both are fixed at filing, and re-classifying history would change who may read a
case and when it is due.
"""

import frappe
from oan_auth_service.api import operational_log as oplog

from oan_grievance_service.setup import urgency_matrix as matrix

SAVEPOINT = "apply_sensitive_urgent_matrix"
RULE = "Grievance SLA Rule"


def execute():
	frappe.reload_doc("grievance_masters", "doctype", "grievance_type")
	frappe.reload_doc("grievance_sla", "doctype", "grievance_sla_rule")

	for code in matrix.SENSITIVE_TYPE_CODES:
		name = frappe.db.get_value("Grievance Type", {"type_code": code})
		if not name or frappe.db.get_value("Grievance Type", name, "is_sensitive"):
			continue
		doc = frappe.get_doc("Grievance Type", name)
		doc.is_sensitive = 1
		_save([doc], f"grievance type {code}")

	for code, rules in matrix.SLA_RULES.items():
		category = frappe.db.get_value("Grievance Service Category", {"code": code})
		if not category or frappe.db.exists(RULE, {"service_category": category}):
			continue
		docs = []
		for index, rule in enumerate(rules, start=1):
			doc = frappe.get_doc({"doctype": RULE, "service_category": category, **rule})
			# The seed's names, so loading the seed afterwards finds them and adds no second copy.
			doc.flags.seed_name = f"{code}-R{index}"
			docs.append(doc)
		_save(docs, f"the rules of {code}")


def _save(docs, what: str) -> None:
	frappe.db.savepoint(SAVEPOINT)
	try:
		for doc in docs:
			if doc.flags.seed_name:
				doc.insert(ignore_permissions=True, set_name=doc.flags.seed_name)
			else:
				doc.save(ignore_permissions=True)
	except frappe.ValidationError:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.clear_messages()
		oplog.log_error(title=f"STG-514 matrix not applied to {what}", message=frappe.get_traceback())
