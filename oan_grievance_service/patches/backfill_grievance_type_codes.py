# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Give every grievance type created before the `code` field a code.

The code is the type's stable machine key within its category. Existing types get it from
their name, the same way the doctype fills one in for a type saved without a code, and it is
made unique within the category. Types that already have a code are left alone.
"""

import frappe

from oan_grievance_service.grievance_masters.doctype.grievance_type.grievance_type import generate_code


def execute():
	for row in frappe.get_all(
		"Grievance Type",
		filters=[["code", "in", ["", None]]],
		fields=["name", "type_name", "service_category"],
		order_by="creation asc, name asc",
	):
		code = generate_code(row.type_name, row.service_category, exclude=row.name)
		frappe.db.set_value("Grievance Type", row.name, "code", code, update_modified=False)
