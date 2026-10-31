# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The login behind an officer or reviewer: the User, its role and its password.

`services/officer.py` places both on category desks. What it needs from the User is here: the
role an account must hold, the accounts the API never manages, the temporary password, and
enabling and disabling the login. Nothing in this module commits.
"""

import frappe
from frappe import _

from oan_grievance_service.services import constants as C

# Custom field on User, created by oan_auth_service. Named here rather than imported so this
# module still loads against an auth service that predates temporary passwords.
MUST_CHANGE_PASSWORD_FIELD = "oan_must_change_password"


def user_roles(user_id: str) -> set[str]:
	"""The roles stored on the User, without the automatic ones (All, Guest, Desk User)."""
	return set(frappe.get_all("Has Role", filters={"parent": user_id, "parenttype": "User"}, pluck="role"))


def assert_manageable(user_id: str) -> None:
	"""System Manager and Administrator accounts are never managed through the officer API."""
	if user_id == "Administrator" or user_roles(user_id) & C.PRIVILEGED_ROLES:
		frappe.throw(
			_("{0} is a System Manager or Administrator account and cannot be managed here.").format(user_id),
			frappe.PermissionError,
		)


def new_or_existing_user(email: str):
	"""The User with this email, created without a role or a welcome email when it is new."""
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": email, "send_welcome_email": 0, "enabled": 1}
	)
	user.insert(ignore_permissions=True)
	return user


def write_user(user, fields: dict, role: str) -> None:
	"""Set the person-level fields that were given and make sure the User holds `role`."""
	if "full_name" in fields:
		user.first_name, user.last_name = fields["full_name"], ""
	if "phone" in fields:
		user.phone = fields["phone"]
	user.append_roles(role)
	user.save(ignore_permissions=True)


def issue_temporary_password(user_id: str, password: str) -> None:
	# Imported here because that module declares the auth service's REST routes as a side
	# effect of being imported, and a module-level import would add them to this app's
	# OpenAPI and gateway generators.
	from oan_auth_service.api.v1.auth import issue_temporary_password as issue

	issue(user_id, password)


def set_enabled(user_id: str, enabled: bool) -> None:
	"""Enable or disable the login. Disabling also ends every session and refresh token.

	A disabled User cannot sign in, the auth middleware refuses its access tokens, and the
	refresh endpoint refuses its refresh tokens. The sessions and tokens are removed as well
	so nothing is left live for a later re-enable.
	"""
	user = frappe.get_doc("User", user_id)
	if bool(user.enabled) != enabled:
		user.enabled = 1 if enabled else 0
		user.save(ignore_permissions=True)
	if not enabled:
		# Imported here for the same reason as issue_temporary_password.
		from oan_auth_service.api.v1.auth import revoke_all_for_user

		frappe.sessions.clear_sessions(user_id, force=True)
		revoke_all_for_user(user_id)
