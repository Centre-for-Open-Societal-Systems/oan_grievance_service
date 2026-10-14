# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Make sure every standard Submission Type exists, is active, and carries its code.

`GET /api/v1/submitters/options` lists only active Submission Types, and registration
defaults a profile to "Web Portal". An environment where a standard channel was never
seeded, was saved inactive, or has no code therefore showed an empty Step 1 dropdown or
could not register anyone without naming a channel.

This is a one-time repair that reactivates the standard channels. Anything an
administrator adds or changes afterwards is left alone: the seed on migrate only creates
what is missing.
"""

import frappe

from oan_grievance_service.setup.install import SUBMISSION_TYPES


def execute():
	for name, code in SUBMISSION_TYPES:
		if not frappe.db.exists("Grievance Submission Type", name):
			frappe.get_doc(
				{
					"doctype": "Grievance Submission Type",
					"submission_type_name": name,
					"code": code,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
			continue

		doc = frappe.get_doc("Grievance Submission Type", name)
		# The code is unique, so a record without one only takes ours if nobody else holds it.
		if not doc.code and not frappe.db.exists("Grievance Submission Type", {"code": code}):
			doc.code = code
		doc.is_active = 1
		doc.save(ignore_permissions=True)
