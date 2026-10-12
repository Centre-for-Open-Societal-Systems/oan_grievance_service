# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Let categories that sit at the old default threshold follow the installation-wide one.

`auto_escalation_threshold` used to default to 100 on every SLA Configuration, so a stored
100 cannot be told apart from a deliberate one. The installation-wide threshold now lives
on the Grievance Deferral Policy and starts at 100, so clearing the stored 100 changes no
behaviour today and lets the setting take effect for these categories from now on.
Thresholds below 100 were chosen by someone and stay.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabGrievance SLA Configuration`
		SET auto_escalation_threshold = 0
		WHERE auto_escalation_threshold = 100
		"""
	)
