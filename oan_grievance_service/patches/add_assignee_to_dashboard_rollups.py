# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Add the assignee to the dashboard rollups and rebuild them before the migrate ends.

An officer's dashboard counts the cases assigned to them and their team, which the rollups
could not say until each row carried `assigned_to`. Every existing row has none, so an officer
reads zeros until it is rebuilt. The rebuild therefore runs here, in the migrate, not on a
queue: a worker that is down or a job that fails would leave officers on zeros with nothing to
retry it.

A full rebuild gives every daily row, and today's snapshot and area rows, their assignee. The
snapshot rows of earlier days stay without one: a past day's status counts cannot be rebuilt
from current data, and they are not split by assignee anyway (see dashboard_rollup).
"""

import frappe

from oan_grievance_service.services import dashboard_rollup


def execute():
	for doctype in ("grievance_stat_daily", "grievance_stat_snapshot", "grievance_stat_area"):
		frappe.reload_doc("grievance_management", "doctype", doctype)
	if not frappe.db.get_value("Grievance", {}, "name"):
		return
	if dashboard_rollup.refresh_now(full=True) is None:
		# A runner held the lock for the whole wait. Queue behind it rather than fail the
		# migrate; it takes the same lock, so the two cannot overlap.
		dashboard_rollup.enqueue(full=True)
