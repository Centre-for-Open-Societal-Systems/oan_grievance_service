# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The login behind an officer, admin or reviewer account, shared by the services that roster them.

`services/officer.py` places an officer on category desks and `services/staff_account.py`
places an Admin or Reviewer on the staff desk. What they have in common is here: the User
and its roles, the one-grievance-role rule, the accounts the API never manages, the temporary
password, enabling and disabling, and the status counts. Nothing in this module commits.
"""

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from pypika.functions import Max
from pypika.terms import Case

from oan_grievance_service.services import constants as C

ADMIN_ROLE = C.ROLE_ADMIN
# Custom field on User, created by oan_auth_service. Named here rather than imported so this
# module still loads against an auth service that predates temporary passwords.
MUST_CHANGE_PASSWORD_FIELD = "oan_must_change_password"


def user_roles(user_id: str) -> set[str]:
	"""The roles stored on the User, without the automatic ones (All, Guest, Desk User)."""
	return set(frappe.get_all("Has Role", filters={"parent": user_id, "parenttype": "User"}, pluck="role"))


def grievance_roles(user_id: str) -> set[str]:
	return user_roles(user_id) & C.GRIEVANCE_ROLES


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
	others = sorted(grievance_roles(user_id) - {role})
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
		{
			"doctype": "User",
			"email": email,
			"first_name": email,
			"send_welcome_email": 0,
			"enabled": 1,
		}
	)
	user.insert(ignore_permissions=True)
	return user


def write_user(user, fields: dict, role: str | None = None) -> None:
	"""Set the person-level fields given and, with `role`, make sure the User holds that role."""
	if "full_name" in fields:
		user.first_name, user.last_name = fields["full_name"], ""
	if "phone" in fields:
		user.phone = fields["phone"]
	if role and role not in {row.role for row in user.roles}:
		user.append("roles", {"role": role})
	user.save(ignore_permissions=True)


def issue_temporary_password(user_id: str, password: str) -> None:
	# Imported here because that module declares the auth service's REST routes as a side
	# effect of being imported, and a module-level import would add them to this app's
	# OpenAPI and gateway generators.
	from oan_auth_service.api.v1.auth import issue_temporary_password as issue

	issue(user_id, password)


def set_enabled(user_id: str, enabled: bool) -> None:
	"""Enable or disable the login. Disabling also ends every session and refresh token.

	A disabled User cannot sign in (Frappe's login refuses it), the auth middleware refuses
	its access tokens, and the refresh endpoint refuses its refresh tokens. The sessions and
	tokens are removed as well so nothing is left live for a later re-enable.
	"""
	user = frappe.get_doc("User", user_id)
	if bool(user.enabled) != enabled:
		user.enabled = 1 if enabled else 0
		user.save(ignore_permissions=True)
	if not enabled:
		from frappe.sessions import clear_sessions
		from oan_auth_service.api.v1.auth import revoke_all_for_user

		clear_sessions(user_id, force=True)
		revoke_all_for_user(user_id)


def guard_deactivation(user_id: str) -> None:
	"""Refuse a deactivation that would lock the caller out or leave nobody to administer.

	Two rules: nobody deactivates their own account, and the last enabled Grievance Admin
	cannot be deactivated. The admins are locked while they are counted, so two admins
	deactivating each other at once cannot both pass.
	"""
	if user_id == frappe.session.user:
		frappe.throw(_("You cannot deactivate your own account."), frappe.PermissionError)
	if ADMIN_ROLE not in user_roles(user_id):
		return
	holders = frappe.get_all("Has Role", filters={"role": ADMIN_ROLE, "parenttype": "User"}, pluck="parent")
	user = DocType("User")
	enabled = {
		name
		for (name,) in (
			frappe.qb.from_(user)
			.select(user.name)
			.where(user.name.isin(holders) & (user.enabled == 1))
			.for_update()
			.run()
		)
	}
	if user_id in enabled and not enabled - {user_id}:
		frappe.throw(
			_("{0} is the last active Grievance Admin and cannot be deactivated.").format(user_id),
			frappe.ValidationError,
		)


def status_counts(query, person, *, available, reachable) -> dict[str, int]:
	"""Active, On Leave and Inactive people among the rows of `query`, each person once.

	`query` selects nothing yet and joins `person` (the User table) to the rows. A person is
	Active when any of their rows is `available`, On Leave when none is but one is
	`reachable`, and Inactive otherwise: the most available row wins, as in `_status`. The
	grouping is done in SQL, so this reads one small row per distinct outcome.
	"""
	per_person = (
		query.select(
			Max(Case().when(available, 1).else_(0)).as_("available"),
			Max(Case().when(reachable, 1).else_(0)).as_("reachable"),
		)
		.groupby(person.name)
		.as_("per_person")
	)
	outcomes = (
		frappe.qb.from_(per_person)
		.select(per_person.available, per_person.reachable, Count("*"))
		.groupby(per_person.available, per_person.reachable)
		.run()
	)
	counts = {"active": 0, "on_leave": 0, "inactive": 0}
	for is_available, is_reachable, count in outcomes:
		key = "active" if is_available else "on_leave" if is_reachable else "inactive"
		counts[key] += count
	counts["total"] = sum(counts.values())
	return counts
