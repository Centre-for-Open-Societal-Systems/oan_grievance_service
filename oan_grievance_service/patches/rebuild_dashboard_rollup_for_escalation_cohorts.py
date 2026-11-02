# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Rebuild the dashboard rollup after `escalated_count` changed meaning and `informed_count` arrived.

`escalated_count` used to count the cases first escalated on a day; it now counts the
cases filed on a day that have been escalated since, so the escalation rate divides like
by like. `informed_count` counts the cases filed on a day that have had a public officer
update, and is empty on every existing row. Rows built the old way sit under the wrong
days until every day is rebuilt, and the rebuild reads every grievance, so it is queued
rather than run inside the migrate.
"""

import frappe


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance_stat_daily")
	if not frappe.db.get_value("Grievance", {}, "name"):
		return
	frappe.enqueue(
		"oan_grievance_service.services.dashboard_rollup.refresh",
		full=True,
		queue="long",
		job_id="grievance_dashboard_rollup",
		deduplicate=True,
		enqueue_after_commit=True,
	)
