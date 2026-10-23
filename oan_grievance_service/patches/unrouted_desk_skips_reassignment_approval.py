# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Let cases leave the unrouted desk without waiting for department head approval."""

import frappe

from oan_grievance_service.services import constants as C


def execute():
	frappe.db.set_value(
		"Grievance RBAC Assignment",
		{"department_scope": C.UNROUTED_DEPARTMENT},
		"reassignment_requires_approval",
		0,
	)
