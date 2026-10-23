# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Fill `has_desk` on the snapshot days kept before the column existed.

The coverage axis used to read the department alone: a case counted when it had one that
was not the Unrouted desk. `has_desk` also counts a case on a desk with no department. A
past day cannot be recomputed, so its rows keep the old reading, which is the most they
can say. Today's rows are replaced by the next refresh.
"""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	frappe.reload_doc("grievance_management", "doctype", "grievance_stat_snapshot")
	frappe.db.sql(
		"""UPDATE `tabGrievance Stat Snapshot`
		SET has_desk = 1
		WHERE COALESCE(assigned_dept, '') NOT IN ('', %s) AND COALESCE(has_desk, 0) = 0""",
		C.UNROUTED_DEPARTMENT,
	)
