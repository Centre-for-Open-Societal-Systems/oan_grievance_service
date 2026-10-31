# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Create Grievance Stat Area and have it filled.

grvByAdminLevel counts cases per Region and Woreda from that table. It holds only the
rows of the moment, so there is nothing to carry over: a refresh, queued here instead of
waiting up to 15 minutes for the scheduler, writes it whole.
"""

import frappe

from oan_grievance_service.services import dashboard_rollup


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance_stat_area")
	if frappe.db.exists("Grievance"):
		dashboard_rollup.enqueue()
