# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Admin and Reviewer accounts on the staff desk.

An Admin ("Grievance Admin") or Reviewer ("Grievance Review Officer") is a User like an
officer is, and is on the RBAC record like one: one row on the staff desk (`GR-RBAC-STAFF`),
carrying a role level of `admin` or `review_officer` and the designation. They are not
officers and never take part in grievance work:

- the staff desk is never active, and routing, escalation, scope checks and officer
  statistics read active desks only;
- the two role levels are inactive, so neither is a rung of the escalation chain;
- the officer listing reads L1 and L2 rows only.

What an account may do comes from its Frappe role alone. The row is the roster: who the
account is, its designation, and whether it is active. Status is therefore only Active or
Inactive. Inactive clears the row's `active`, disables the User, and ends its sessions and
refresh tokens, which is what blocks sign-in.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count

from oan_grievance_service.services import account
from oan_grievance_service.services import constants as C

DESK = "Grievance RBAC Assignment"
ROW = "Grievance RBAC Assignment Officer"
ROLES = ("Admin", "Reviewer")
# API role -> (Frappe role the User holds, role level of the staff-desk row)
FRAPPE_ROLES = {"Admin": C.ROLE_ADMIN, "Reviewer": C.ROLE_REVIEW_OFFICER}
LEVEL_CODES = {"Admin": C.ROLE_LEVEL_ADMIN, "Reviewer": C.ROLE_LEVEL_REVIEW_OFFICER}
CODE_ROLES = {code: role for role, code in LEVEL_CODES.items()}
STATUSES = ("Active", "Inactive")
# What PATCH may change. The role is fixed once created, and so is the email.
UPDATABLE = {"full_name", "phone", "designation", "status"}
USER_FIELDS = [
	"name",
	"full_name",
	"email",
	"phone",
	"enabled",
	account.MUST_CHANGE_PASSWORD_FIELD,
]


def ensure_desk() -> str:
	"""The staff desk's name, seeding it first when this site has not been migrated yet."""
	if not frappe.db.exists(DESK, C.STAFF_DESK):
		from oan_grievance_service.setup.install import seed_staff_desk

		seed_staff_desk()
	return C.STAFF_DESK


def role_of(user_id: str) -> str | None:
	"""Admin or Reviewer when the user has a row on the staff desk, else None."""
	found = rows([user_id])
	return CODE_ROLES[found[0].role_level] if found else None


def rows(user_ids: list[str]) -> list:
	"""The staff-desk rows of `user_ids`."""
	if not user_ids:
		return []
	return frappe.get_all(
		ROW,
		filters={
			"parent": C.STAFF_DESK,
			"parenttype": DESK,
			"user": ["in", user_ids],
			"role_level": ["in", list(CODE_ROLES)],
		},
		fields=["name", "user", "role_level", "designation", "active"],
		order_by="idx",
	)


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
	"""Make `email` an Admin or Reviewer on the staff desk.

	Returns the user id and whether `temporary_password` was applied.

	The User is created only when the email is new, and holds the requested role alone. An
	existing login is kept as it is, password included, when it holds no grievance role or
	already holds this one. One that holds a different grievance role, or is a System Manager
	or Administrator, is rejected. The caller commits.
	"""
	_assert_status(status)
	if rows([email]):
		frappe.throw(_("{0} is already an Admin or Reviewer.").format(email), frappe.ValidationError)
	has_login = bool(frappe.db.exists("User", email))
	if has_login:
		account.assert_manageable(email)
		account.assert_single_grievance_role(email, FRAPPE_ROLES[role])

	desk = frappe.get_doc(DESK, ensure_desk(), for_update=True)
	user = account.new_or_existing_user(email)
	account.write_user(user, {"full_name": full_name, "phone": phone}, FRAPPE_ROLES[role])
	desk.append(
		"officers",
		{
			"user": user.name,
			"role_level": LEVEL_CODES[role],
			"designation": designation,
			"is_primary": 0,
			"active": 1 if status == "Active" else 0,
			"on_leave": 0,
		},
	)
	_save_desk(desk)
	if not has_login:
		account.issue_temporary_password(user.name, temporary_password)
	if status == "Inactive":
		account.set_enabled(user.name, False)
	return user.name, not has_login


def update(user_id: str, changes: dict) -> str:
	"""Apply a partial update. Only the name, phone, designation and status can change."""
	found = rows([user_id])
	if not found:
		frappe.throw(_("Account '{0}' was not found.").format(user_id), frappe.DoesNotExistError)
	role = CODE_ROLES[found[0].role_level]
	account.assert_manageable(user_id)

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

	if changes.keys() & {"full_name", "phone"}:
		person = {key: changes[key] for key in ("full_name", "phone") if key in changes}
		account.write_user(frappe.get_doc("User", user_id), person)

	desk = frappe.get_doc(DESK, ensure_desk(), for_update=True)
	mine = next(row for row in desk.officers if row.user == user_id)
	if "designation" in changes:
		mine.designation = changes["designation"]
	if status:
		mine.active = 1 if status == "Active" else 0
	_save_desk(desk)
	if status:
		account.set_enabled(user_id, status == "Active")
	return user_id


def records(user_ids: list[str]) -> list[dict]:
	"""API records for the Admin and Reviewer accounts among `user_ids`, in the order given."""
	found = {row.user: row for row in rows(user_ids)}
	if not found:
		return []
	people = {
		person.name: person
		for person in frappe.get_all("User", filters={"name": ["in", list(found)]}, fields=USER_FIELDS)
	}
	records = []
	for user_id in user_ids:
		row, person = found.get(user_id), people.get(user_id)
		if not row or not person:
			continue
		records.append(
			{
				"name": person.name,
				"full_name": person.full_name,
				"role": CODE_ROLES[row.role_level],
				"designation": row.designation,
				"level": None,
				"department": None,
				"email": person.email,
				"phone": person.phone,
				"must_change_password": bool(person.get(account.MUST_CHANGE_PASSWORD_FIELD)),
				"region": None,
				"region_name": None,
				"status": "Active" if row.active and person.enabled else "Inactive",
				"service_categories": [],
				"reports_to": None,
				"reports_to_name": None,
				"assignments": [],
			}
		)
	return records


def list_ids(*, role: str, status=None, q=None, start=0, page_size=20) -> tuple[list[str], int]:
	"""One page of the ids of `role` accounts and the total, filtered and counted in SQL."""
	row, user = DocType(ROW), DocType("User")
	query = _filtered(role, q)
	if status:
		query = query.where(_available(row, user) if status == "Active" else ~_available(row, user))
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
	row, user = DocType(ROW), DocType("User")
	available = _available(row, user)
	return account.status_counts(_filtered(role, q), user, available=available, reachable=available)


def _filtered(role: str, q: str | None):
	"""Staff-desk rows of `role`, joined to their User, narrowed by the search text."""
	row, user = DocType(ROW), DocType("User")
	query = (
		frappe.qb.from_(row)
		.join(user)
		.on(user.name == row.user)
		.where(row.parenttype == DESK)
		.where(row.parent == C.STAFF_DESK)
		.where(row.role_level == LEVEL_CODES[role])
	)
	if q:
		like = f"%{q.strip()}%"
		query = query.where(user.full_name.like(like) | user.email.like(like))
	return query


def _available(row, user):
	"""A row that is active on an enabled login. Disabling the User on Desk makes it Inactive."""
	return (row.active == 1) & (user.enabled == 1)


def _assert_status(status: str) -> None:
	if status not in STATUSES:
		frappe.throw(
			_("An Admin or Reviewer account is either Active or Inactive, not {0}.").format(status),
			frappe.ValidationError,
		)


def _save_desk(desk) -> None:
	desk.assigned_by = frappe.session.user
	desk.save()
