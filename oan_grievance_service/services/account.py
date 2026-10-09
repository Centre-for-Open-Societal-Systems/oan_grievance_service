# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The login behind an officer, admin or reviewer account, shared by the services that manage them.

`services/officer.py` places an officer on category desks. `services/staff_account.py` manages an
Admin or Reviewer, who is a User holding a role and nothing else. What they have in common is
here: the User and its roles, the one-grievance-role rule, the accounts the API never manages,
the temporary password, and enabling and disabling. Nothing in this module commits.
"""

import frappe
from frappe import _

from oan_grievance_service.services import constants as C

ADMIN_ROLE = C.ROLE_ADMIN
# Custom field on User, created by oan_auth_service. Named here rather than imported so this
# module still loads against an auth service that predates temporary passwords.
MUST_CHANGE_PASSWORD_FIELD = "oan_must_change_password"


def user_roles(user_id: str) -> set[str]:
	"""The roles stored on the User, without the automatic ones (All, Guest, Desk User)."""
	return set(frappe.get_all("Has Role", filters={"parent": user_id, "parenttype": "User"}, pluck="role"))


def is_privileged(user_id: str) -> bool:
	"""Whether the account is Administrator or holds System Manager."""
	return user_id == "Administrator" or bool(user_roles(user_id) & C.PRIVILEGED_ROLES)


def assert_manageable(user_id: str) -> None:
	"""System Manager and Administrator accounts are never managed through the officer API."""
	if is_privileged(user_id):
		frappe.throw(
			_("{0} is a System Manager or Administrator account and cannot be managed here.").format(user_id),
			frappe.PermissionError,
		)


def assert_single_grievance_role(user_id: str, role: str) -> None:
	"""Reject an account that already holds a different grievance role.

	The roles are additive in Frappe: an account that is a submitter and an officer, or an
	officer and a reviewer, has the permissions of both, and a reviewer is read-only only
	because nothing else is granted next to it.
	"""
	others = sorted((user_roles(user_id) & C.GRIEVANCE_ROLES) - {role})
	if others:
		frappe.throw(
			_(
				"{0} already holds the {1} role. An account holds one grievance role, so it "
				"cannot also be given {2}."
			).format(user_id, ", ".join(others), role),
			frappe.ValidationError,
		)


def caller_is_system() -> bool:
	"""Whether the caller is a System Manager or Administrator."""
	return frappe.session.user == "Administrator" or bool(set(frappe.get_roles()) & C.PRIVILEGED_ROLES)


def new_or_existing_user(email: str):
	"""The User with this email, created without a role or a welcome email when it is new."""
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": email, "send_welcome_email": 0, "enabled": 1}
	)
	user.insert(ignore_permissions=True)
	return user


def write_user(user, fields: dict, role: str | None = None) -> None:
	"""Set the person-level fields given and, with `role`, make sure the User holds that role."""
	for key, value in fields.items():
		if key == "full_name":
			user.first_name, user.last_name = value, ""
		else:
			user.set(key, value)
	if role:
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


def guard_deactivation(user_id: str) -> None:
	"""Refuse a deactivation that would lock the caller out or leave nobody to administer.

	Two rules: nobody deactivates their own account, and the last enabled Grievance Admin
	cannot be deactivated.
	"""
	if user_id == frappe.session.user:
		frappe.throw(_("You cannot deactivate your own account."), frappe.PermissionError)
	if ADMIN_ROLE not in user_roles(user_id):
		return
	holders = frappe.get_all("Has Role", filters={"role": ADMIN_ROLE, "parenttype": "User"}, pluck="parent")
	enabled = set(frappe.get_all("User", filters={"name": ["in", holders], "enabled": 1}, pluck="name"))
	if user_id in enabled and not enabled - {user_id}:
		frappe.throw(
			_("{0} is the last active Grievance Admin and cannot be deactivated.").format(user_id),
			frappe.ValidationError,
		)
