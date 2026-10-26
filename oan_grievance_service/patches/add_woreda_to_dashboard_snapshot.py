# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Add the `woreda` column to the dashboard snapshot and have today's rows rebuilt.

grvByAdminLevel counts cases per Woreda from today's snapshot. A past day's rows cannot be
regrouped by Woreda from current data, so they keep an empty `woreda` and the chart never
reads them. Today's rows are rebuilt by a queued refresh rather than inside the migrate, so
the chart does not wait up to 15 minutes for the scheduler.
"""

import frappe


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance_stat_snapshot")
	if not frappe.db.get_value("Grievance", {}, "name"):
		return
	frappe.enqueue(
		"oan_grievance_service.services.dashboard_rollup.refresh",
		queue="long",
		job_id="grievance_dashboard_rollup",
		deduplicate=True,
		enqueue_after_commit=True,
	)
