# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Make the unrouted officer the category admin of the unrouted desk.

The desk that takes a case no other desk can used to seat its officer at the first
level. It is the last stop for those cases, so its officer is the department head, with the
desk head role that comes with it.
"""

import frappe

from oan_grievance_service.services import constants as C
from oan_grievance_service.services.officer import _sync_desk_head_role


def execute():
	if not frappe.db.exists("User", C.UNROUTED_OFFICER):
		return

	frappe.db.set_value(
		"Grievance RBAC Assignment Officer",
		{
			"parenttype": "Grievance RBAC Assignment",
			"user": C.UNROUTED_OFFICER,
			"role_level": "nodal_officer",
		},
		"role_level",
		"department_head",
	)
	_sync_desk_head_role(C.UNROUTED_OFFICER)
