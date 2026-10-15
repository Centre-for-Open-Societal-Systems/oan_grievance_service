# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Give the Grievance Desk Head role to every officer already on the top rung."""

import frappe

from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	top_rung,
)
from oan_grievance_service.services import constants as C


def execute():
	# Patches run before after_migrate seeds the roles.
	if not frappe.db.exists("Role", C.ROLE_DESK_HEAD):
		frappe.get_doc({"doctype": "Role", "role_name": C.ROLE_DESK_HEAD, "desk_access": 0}).insert(
			ignore_permissions=True
		)
	top = top_rung()
	if not top:
		return
	users = frappe.get_all(
		"Grievance RBAC Assignment Officer",
		filters={"role_level": top, "active": 1, "parenttype": "Grievance RBAC Assignment"},
		pluck="user",
		distinct=True,
	)
	for user_id in users:
		user = frappe.get_doc("User", user_id)
		if not any(row.role == C.ROLE_DESK_HEAD for row in user.roles):
			user.append_roles(C.ROLE_DESK_HEAD)
			user.save(ignore_permissions=True)
