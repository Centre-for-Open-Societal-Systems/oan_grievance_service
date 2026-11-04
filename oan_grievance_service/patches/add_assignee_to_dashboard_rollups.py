# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Add the assignee to the dashboard rollups and have them rebuilt.

An officer's dashboard counts the cases assigned to them and their team, which the rollups
could not say until each row carried `assigned_to`. A full rebuild gives every daily row and
today's snapshot and area rows their assignee; the daily rows are the history an officer
reads. Snapshot rows of earlier days stay without one: a past day's status counts cannot be
rebuilt from current data. The rebuild reads every grievance, so it is queued rather than run
inside the migrate.
"""

import frappe

from oan_grievance_service.services import dashboard_rollup


def execute():
	for doctype in ("grievance_stat_daily", "grievance_stat_snapshot", "grievance_stat_area"):
		frappe.reload_doc("grievance_management", "doctype", doctype)
	if frappe.db.get_value("Grievance", {}, "name"):
		dashboard_rollup.enqueue(full=True)
