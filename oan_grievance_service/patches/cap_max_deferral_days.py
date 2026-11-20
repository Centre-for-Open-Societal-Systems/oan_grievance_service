# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Bring a deferral ceiling set above the 30-day limit back down to it (STG-516).

The policy accepted any ceiling, and a deferral is checked against it, so a site set to
more than 30 days accepted deferrals longer than the business rule allows. The Single now
refuses such a value, so it is corrected here rather than left to fail its next save.
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	doctype = "Grievance Deferral Policy"
	configured = frappe.db.get_single_value(doctype, "max_deferral_days")
	if configured and configured > C.MAX_DEFERRAL_DAYS_LIMIT:
		frappe.db.set_single_value(doctype, "max_deferral_days", C.MAX_DEFERRAL_DAYS_LIMIT)
		frappe.clear_document_cache(doctype, doctype)
