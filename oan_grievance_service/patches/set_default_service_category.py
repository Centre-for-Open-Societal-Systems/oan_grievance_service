# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Name the default service category on sites that predate the `is_default` flag.

Unclassified cases used to fall to the catch-all category by its name, "Other". The flag
makes that a setting an administrator can move, so the catch-all becomes the default
unless some category already holds the flag.
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	if frappe.db.exists("Grievance Service Category", {"is_default": 1}):
		return
	if frappe.db.exists("Grievance Service Category", C.FALLBACK_SERVICE_CATEGORY):
		frappe.db.set_value(
			"Grievance Service Category",
			C.FALLBACK_SERVICE_CATEGORY,
			"is_default",
			1,
			update_modified=False,
		)
