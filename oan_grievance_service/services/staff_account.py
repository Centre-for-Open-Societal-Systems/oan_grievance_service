# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Admin and Reviewer accounts, kept as plain Users.

An Admin ("Grievance Admin") or Reviewer ("Grievance Review Officer") is a User that holds that
role. Frappe's own records are the only source of truth: the role is the `Has Role` row, the
status is `User.enabled`, and the title is the `oan_designation` Custom Field. They are not
officers and have no desk, so nothing in routing, escalation, scope checks or officer
statistics can see them, and there is no roster of them to keep in step.

Status is therefore only Active or Inactive. Inactive disables the User and ends its sessions
and refresh tokens, which is what blocks sign-in. System Manager and Administrator accounts are
never listed or managed here, even if they also hold one of these roles.
"""

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count

from oan_grievance_service.services import account
from oan_grievance_service.services import constants as C

OFFICER_ROW = "Grievance RBAC Assignment Officer"
ROLES = ("Admin", "Reviewer")
FRAPPE_ROLES = {"Admin": C.ROLE_ADMIN, "Reviewer": C.ROLE_REVIEW_OFFICER}
STATUSES = ("Active", "Inactive")
# What PATCH may change. The role is fixed once created, and so is the email.
UPDATABLE = {"full_name", "phone", "designation", "status"}
DESIGNATION_FIELD = "oan_designation"
USER_FIELDS = [
	"name",
	"full_name",
	"email",
	"phone",
	"enabled",
	DESIGNATION_FIELD,
	account.MUST_CHANGE_PASSWORD_FIELD,
]


def role_of(user_id: str) -> str | None:
	"""Admin or Reviewer for a User that holds that role, else None."""
	return _roles_of([user_id]).get(user_id)


def create(
	*,
	role: str,
	full_name: str,
	email: str,
	phone: str,
	temporary_password: str,
	designation: str | None = None,
	status: str = "Active",
) -> tuple[str, bool]:
	"""Give `email` the Admin or Reviewer role, creating the User when the email is new.

	Returns the user id and whether `temporary_password` was applied.

	The User holds the requested role alone. An existing login with no grievance role is kept as
	it is, password included. One that holds any grievance role, or is a System Manager or
	Administrator, is rejected. The caller commits.
	"""
	_assert_status(status)
	has_login = bool(frappe.db.exists("User", email))
	if has_login:
		account.assert_manageable(email)
		account.assert_single_grievance_role(email, FRAPPE_ROLES[role])
		if role_of(email):
			frappe.throw(_("{0} is already an Admin or Reviewer.").format(email), frappe.ValidationError)

	user = account.new_or_existing_user(email)
	account.write_user(
		user,
		{"full_name": full_name, "phone": phone, DESIGNATION_FIELD: designation},
		FRAPPE_ROLES[role],
	)
	if not has_login:
		account.issue_temporary_password(user.name, temporary_password)
	if status == "Inactive":
		account.set_enabled(user.name, False)
	return user.name, not has_login


def update(user_id: str, changes: dict) -> str:
	"""Apply a partial update. Only the name, phone, designation and status can change."""
	role = role_of(user_id)
	if not role:
		frappe.throw(_("Account '{0}' was not found.").format(user_id), frappe.DoesNotExistError)

	changes = dict(changes)
	rejected = sorted(set(changes) - UPDATABLE)
	if rejected:
		frappe.throw(
			_("{0} cannot be changed on a {1} account. Only name, phone, designation and status can.").format(
				", ".join(rejected), role
			),
			frappe.ValidationError,
		)
	if "phone" in changes and not changes["phone"]:
		frappe.throw(_("A phone number is required for a {0} account.").format(role), frappe.ValidationError)
	status = changes.pop("status", None)
	if status:
		_assert_status(status)
		if status == "Inactive":
			account.guard_deactivation(user_id)

	person = {(DESIGNATION_FIELD if key == "designation" else key): value for key, value in changes.items()}
	if person:
		account.write_user(frappe.get_doc("User", user_id), person)
	if status:
		account.set_enabled(user_id, status == "Active")
	return user_id


def records(user_ids: list[str]) -> list[dict]:
	"""API records for the Admin and Reviewer accounts among `user_ids`, in the order given."""
	roles = _roles_of(user_ids)
	if not roles:
		return []
	people = {
		person.name: person
		for person in frappe.get_all("User", filters={"name": ["in", list(roles)]}, fields=USER_FIELDS)
	}
	return [
		{
			"name": person.name,
			"full_name": person.full_name,
			"role": roles[person.name],
			"designation": person.get(DESIGNATION_FIELD),
			"level": None,
			"department": None,
			"email": person.email,
			"phone": person.phone,
			"must_change_password": bool(person.get(account.MUST_CHANGE_PASSWORD_FIELD)),
			"region": None,
			"region_name": None,
			"status": "Active" if person.enabled else "Inactive",
			"service_categories": [],
			"reports_to": None,
			"reports_to_name": None,
			"assignments": [],
		}
		for user_id in user_ids
		if (person := people.get(user_id))
	]


def list_ids(*, role: str, status=None, q=None, start=0, page_size=20) -> tuple[list[str], int]:
	"""One page of the ids of `role` accounts and the total, filtered and counted in SQL."""
	user = DocType("User")
	query = _filtered(role, q)
	if status:
		query = query.where(user.enabled == (1 if status == "Active" else 0))
	total = query.select(Count(user.name).distinct()).run()[0][0]
	ids = (
		query.select(user.name, user.full_name)
		.distinct()
		.orderby(user.full_name)
		.orderby(user.name)
		.limit(page_size)
		.offset(start)
		.run()
	)
	return [name for name, _full_name in ids], total


def count_by_status(*, role: str, q=None) -> dict[str, int]:
	"""Active and Inactive accounts of `role`. There is no On Leave for a staff account."""
	user = DocType("User")
	per_flag = dict(
		_filtered(role, q).select(user.enabled, Count(user.name).distinct()).groupby(user.enabled).run()
	)
	active, inactive = per_flag.get(1, 0), per_flag.get(0, 0)
	return {"active": active, "on_leave": 0, "inactive": inactive, "total": active + inactive}


def _filtered(role: str, q: str | None):
	"""Users that hold `role`, narrowed by the search text.

	Left out: System Manager and Administrator accounts, and anyone with officer rows, who is
	an officer here whatever other roles they hold.
	"""
	user, has_role, other_role, officer = (
		DocType("User"),
		DocType("Has Role"),
		DocType("Has Role"),
		DocType(OFFICER_ROW),
	)
	privileged = (
		frappe.qb.from_(other_role)
		.select(other_role.parent)
		.where((other_role.parenttype == "User") & other_role.role.isin(list(C.PRIVILEGED_ROLES)))
	)
	officers = (
		frappe.qb.from_(officer).select(officer.user).where(officer.parenttype == "Grievance RBAC Assignment")
	)
	query = (
		frappe.qb.from_(user)
		.join(has_role)
		.on(has_role.parent == user.name)
		.where((has_role.parenttype == "User") & (has_role.role == FRAPPE_ROLES[role]))
		.where(user.name != "Administrator")
		.where(user.name.notin(privileged))
		.where(user.name.notin(officers))
	)
	if q:
		like = f"%{q.strip()}%"
		query = query.where(user.full_name.like(like) | user.email.like(like))
	return query


def _roles_of(user_ids: list[str]) -> dict[str, str]:
	"""Admin or Reviewer for each of `user_ids` that holds that role, in one query.

	An Administrator, a System Manager or a user with officer rows is left out, matching the
	lists. A user who holds both roles is an Admin.
	"""
	if not user_ids:
		return {}
	held = {}
	for row in frappe.get_all(
		"Has Role",
		filters={
			"parenttype": "User",
			"parent": ["in", user_ids],
			"role": ["in", [*FRAPPE_ROLES.values(), *C.PRIVILEGED_ROLES]],
		},
		fields=["parent", "role"],
	):
		held.setdefault(row.parent, set()).add(row.role)
	with_rows = set(frappe.get_all(OFFICER_ROW, filters={"user": ["in", user_ids]}, pluck="user"))
	roles = {}
	for user_id, user_held in held.items():
		if user_id == "Administrator" or user_id in with_rows or user_held & C.PRIVILEGED_ROLES:
			continue
		roles[user_id] = next(role for role, name in FRAPPE_ROLES.items() if name in user_held)
	return roles


def _assert_status(status: str) -> None:
	if status not in STATUSES:
		frappe.throw(
			_("An Admin or Reviewer account is either Active or Inactive, not {0}.").format(status),
			frappe.ValidationError,
		)
