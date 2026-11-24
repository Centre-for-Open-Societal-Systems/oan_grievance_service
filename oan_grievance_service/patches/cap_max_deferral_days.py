# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Bring a deferral ceiling set above 30 days back down to 30 (STG-516).

No deferral may push a deadline out by more than 30 days. `max_deferral_days` on the policy is
the one place that limit lives, so a site that raised it above 30 is set back to 30 here.

The 30 is written out, not imported: a patch records what was done at the time, and must not
change if the shipped default ever does.
"""

import frappe

CEILING_DAYS = 30


def execute():
	doctype = "Grievance Deferral Policy"
	configured = frappe.db.get_single_value(doctype, "max_deferral_days")
	if configured and configured > CEILING_DAYS:
		frappe.db.set_single_value(doctype, "max_deferral_days", CEILING_DAYS)
		frappe.clear_document_cache(doctype, doctype)
