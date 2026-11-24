# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Drop the unused parent_provider column from Grievance Service Provider.

No source supplies a head office, so no provider has a parent.
"""

from frappe.model import delete_fields

DOCTYPE = "Grievance Service Provider"


def execute():
	delete_fields({DOCTYPE: ["parent_provider"]}, delete=1)
