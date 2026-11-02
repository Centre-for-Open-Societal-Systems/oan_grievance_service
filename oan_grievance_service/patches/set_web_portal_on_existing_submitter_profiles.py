# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Give every Submitter Profile that predates `submission_channel` the Web Portal channel.

Registration saved no channel before the field existed, and every account came through the
portal. Profiles that already carry a channel are left as they are.
"""

import frappe


def execute():
	profile = frappe.qb.DocType("Grievance Submitter Profile")
	(
		frappe.qb.update(profile)
		.set(profile.submission_channel, "Web Portal")
		.where(profile.submission_channel.isnull() | (profile.submission_channel == ""))
		.run()
	)
