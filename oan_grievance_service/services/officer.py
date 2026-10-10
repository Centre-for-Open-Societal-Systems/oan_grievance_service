# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer management over the existing User and Grievance RBAC Assignment Officer model.

There is no officer record of its own. An officer is a User with Grievance RBAC Assignment
Officer rows, one on each category desk they staff, so routing, permissions and escalation
read exactly what an admin edits here:

- the person (name, email, phone) is the User;
- the level (L1 Nodal, L2 Senior Nodal, L3 Department Head) and the designation are on the row;
- the service categories are the category desks the officer sits on;
- the region, the supervisor (`reports_to`) and availability are on the officer's rows.

A department head (L3) is an officer on the department's desks like the others, at the rung
`department_head`: cases reach them by escalation and they approve the department's
reassignments, so routing does not hand them first-line cases.

A Reviewer is placed on the department's desks the same way, at the inactive level
`review_officer`. They are not assigned cases and cannot change any, and what they read is
limited to the scopes of their desks. The `role` of an account is read from its row levels.

An officer is never deleted. Status is read from the rows, so it has no field of its own and
nothing on User: Active is an active row, On Leave is an active row with `on_leave` set (the
desk and its permissions stay, but auto-routing skips the officer), and Inactive is a row with
`active` cleared. Changing status writes every desk row of the officer. A Reviewer is only
Active or Inactive, and Inactive also disables their login and ends their sessions, because a
Reviewer's rows are what limit what they can read.
"""

from collections import defaultdict
from types import SimpleNamespace

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count
from pypika.functions import Max
from pypika.terms import Case

from oan_grievance_service.services import account, category_assignment
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.resolvers import (
	resolve_administrative_area,
	resolve_department,
	resolve_service_category,
)

DESK = category_assignment.DOCTYPE
ROW = "Grievance RBAC Assignment Officer"
LEVEL_CODES = {"L1": "nodal_officer", "L2": "senior_nodal_officer", "L3": "department_head"}
CODE_LEVELS = {code: level for level, code in LEVEL_CODES.items()}
DEFAULT_STATUS = "Active"
DEFAULT_ROLE = "Officer"
REVIEWER_ROLE = "Reviewer"
# The Frappe role a User must hold, and the role levels that make a row that role's.
FRAPPE_ROLES = {DEFAULT_ROLE: C.ROLE_OFFICER, REVIEWER_ROLE: C.ROLE_REVIEW_OFFICER}
ROLE_CODES = {
	DEFAULT_ROLE: tuple(CODE_LEVELS),
	REVIEWER_ROLE: (C.ROLE_LEVEL_REVIEW_OFFICER,),
}
MUST_CHANGE_PASSWORD_FIELD = account.MUST_CHANGE_PASSWORD_FIELD
USER_FIELDS = [
	"name",
	"full_name",
	"email",
	"phone",
	MUST_CHANGE_PASSWORD_FIELD,
]


def create(
	*,
	full_name: str,
	email: str,
	temporary_password: str,
	role: str = DEFAULT_ROLE,
	designation: str | None = None,
	level: str | None = None,
	department: str | None = None,
	service_categories: list[str] | None = None,
	phone: str | None = None,
	region: str | None = None,
	status: str = DEFAULT_STATUS,
	reports_to: str | None = None,
) -> tuple[str, bool]:
	"""Make `email` an officer, or a reviewer, on the category desks of `department`.

	Returns the user id and whether `temporary_password` was applied.

	An existing login with no desk rows is promoted, so an admin can staff someone who
	already has an account. A login that is already an officer or reviewer is rejected, and so
	is a System Manager or Administrator, who are never managed here.

	`temporary_password` is required, so a new account always has a way in: they can use it
	just long enough to replace it (`oan_auth_service`'s /api/v1/auth/password/initial). It is applied
	to a new login only. An existing login keeps the password its owner already knows, because
	replacing it with one the admin typed would let the admin take the account over. The caller
	tells the admin when that happened.
	"""
	_assert_status(role, status)
	desks = _category_desks(resolve_department(department), service_categories)
	existing = role_of(email)
	if existing:
		message = (
			_("{0} is already an officer.") if existing == DEFAULT_ROLE else _("{0} is already a reviewer.")
		)
		frappe.throw(message.format(email), frappe.ValidationError)
	has_login = bool(frappe.db.exists("User", email))
	if has_login:
		account.assert_manageable(email)
	placement = _placement(role, level, status)
	placement["designation"] = designation
	placement["administrative_area"] = _resolve_region(region)
	placement["reports_to"] = _resolve_supervisor(reports_to, level)

	user = account.new_or_existing_user(email)
	account.write_user(user, {"full_name": full_name, "phone": phone}, FRAPPE_ROLES[role])
	for desk in desks:
		_save_desk(desk.name, user.name, placement)
	if not has_login:
		account.issue_temporary_password(user.name, temporary_password)
	if role == REVIEWER_ROLE and status == "Inactive":
		account.set_enabled(user.name, False)
	return user.name, not has_login


def reset_temporary_password(user_id: str, password: str) -> str:
	"""Issue a fresh temporary password to an existing officer or reviewer (the forgotten-password path).

	Only an officer or reviewer: this is how an admin recovers an account they manage, and it
	must not be a way to set a password on any other login. The account's current sessions end
	and it must replace the password before signing in again.

	Nobody resets their own password here, and an account that itself holds an admin role is
	never reset here, even if it is also an officer: this sets a password the caller knows, so
	it must not reach an account more powerful than the caller may manage.
	"""
	account.assert_manageable(user_id)
	if user_id == frappe.session.user:
		frappe.throw(
			_("You cannot issue a temporary password to your own account here."),
			frappe.PermissionError,
		)
	if account.user_roles(user_id) & set(C.ADMIN_ROLES):
		frappe.throw(_("A temporary password cannot be issued for this account."), frappe.PermissionError)
	if not role_of(user_id):
		_not_found(user_id)
	account.issue_temporary_password(user_id, password)
	return user_id


def role_of(user_id: str) -> str | None:
	"""Officer or Reviewer, from the levels of the user's desk rows. None for any other login."""
	for role in ROLE_CODES:
		if _rows([user_id], role):
			return role
	return None


def assert_may_read(role: str) -> None:
	"""Officers are read by every admin-read role. Reviewers only by admins.

	Whether a Reviewer may read Reviewer accounts is a product decision still to be confirmed;
	the answer for now is no, and it is one constant (`REVIEWER_READ_ROLES`) to change.
	"""
	allowed = C.ADMIN_READ_ROLES if role == DEFAULT_ROLE else C.REVIEWER_READ_ROLES
	if not set(frappe.get_roles()) & set(allowed):
		frappe.throw(_("You cannot view {0} accounts.").format(role), frappe.PermissionError)


def update(user_id: str, changes: dict) -> str:
	"""Apply a partial update. `department` and `service_categories` re-seat the account on desks.

	A Reviewer has no level or supervisor, so those are refused for them.
	"""
	account.assert_manageable(user_id)
	role = role_of(user_id)
	if not role:
		_not_found(user_id)
	current = _current(user_id, role)
	changes = dict(changes)
	if role == REVIEWER_ROLE:
		refused = sorted(set(changes) & {"level", "reports_to"})
		if refused:
			frappe.throw(
				_("{0} cannot be changed on a Reviewer.").format(", ".join(refused)),
				frappe.ValidationError,
			)
	level = changes.pop("level", current.level)
	status = changes.pop("status", None)
	if status:
		_assert_status(role, status)
	if status == "Inactive" and user_id == frappe.session.user:
		frappe.throw(_("You cannot deactivate your own account."), frappe.PermissionError)
	person = {key: changes.pop(key) for key in ("full_name", "phone") if key in changes}
	if person:
		account.write_user(frappe.get_doc("User", user_id), person, FRAPPE_ROLES[role])

	row_changes = _availability(status) if status else {}
	designation = changes.get("designation", current.designation)
	if "designation" in changes:
		row_changes["designation"] = designation
	if level != current.level:
		row_changes.update(_change_level(user_id, current.level, level))
	if "region" in changes:
		row_changes["administrative_area"] = _resolve_region(changes["region"])
	if "reports_to" in changes:
		row_changes["reports_to"] = _resolve_supervisor(changes["reports_to"], level)

	department = resolve_department(changes["department"]) if "department" in changes else current.department
	desks = _category_desks(department, changes.get("service_categories", current.categories))
	target = {desk.name for desk in desks}
	if role == REVIEWER_ROLE and status == "Active":
		# Before the rows: a desk refuses an active row whose login is disabled.
		account.set_enabled(user_id, True)
	for name in current.desks - target:
		_save_desk(name, user_id, None)
	for name in sorted(target):
		_save_desk(
			name,
			user_id,
			row_changes,
			on_new={**_placement(role, level, status or current.status), "designation": designation},
		)
	if role == REVIEWER_ROLE and status == "Inactive":
		# After the rows, so they are retired before the login stops existing for the desk.
		account.set_enabled(user_id, False)
	return user_id


def list_officers(
	*,
	role=DEFAULT_ROLE,
	level=None,
	department=None,
	status=None,
	service_category=None,
	region=None,
	q=None,
	start=0,
	page_size=20,
):
	"""One page of account ids of `role` and the total, filtered and counted in SQL."""
	row, user = DocType(ROW), DocType("User")
	query = _officer_query(
		role=role,
		level=level,
		department=department,
		service_category=service_category,
		region=region,
		q=q,
	)
	if status:
		query = query.where(_status_filter(row, status))

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


def status_counts(
	*,
	role=DEFAULT_ROLE,
	level=None,
	department=None,
	service_category=None,
	region=None,
	q=None,
) -> dict[str, int]:
	"""Active, On Leave and Inactive people for the same filters as the list, without `status`.

	Each person is counted once, in the status `_status` gives them: the most available of
	their rows. The list's status filter matches a person with a row in that status, so an
	officer whose rows differ is in two of its pages but in one count here.
	"""
	row, user = DocType(ROW), DocType("User")
	query = _officer_query(
		role=role,
		level=level,
		department=department,
		service_category=service_category,
		region=region,
		q=q,
	)
	per_person = (
		query.select(
			Max(Case().when((row.active == 1) & (row.on_leave == 0), 1).else_(0)).as_("available"),
			Max(Case().when(row.active == 1, 1).else_(0)).as_("reachable"),
		)
		.groupby(user.name)
		.as_("per_person")
	)
	outcomes = (
		frappe.qb.from_(per_person)
		.select(per_person.available, per_person.reachable, Count("*"))
		.groupby(per_person.available, per_person.reachable)
		.run()
	)
	counts = {"active": 0, "on_leave": 0, "inactive": 0}
	for available, reachable, count in outcomes:
		counts["active" if available else "on_leave" if reachable else "inactive"] += count
	return {**counts, "total": sum(counts.values())}


def _officer_query(*, role, level, department, service_category, region, q):
	"""Rows of `role` on category desks narrowed by every list filter except status."""
	row, desk, user = DocType(ROW), DocType(DESK), DocType("User")
	codes = [LEVEL_CODES[level]] if level else list(ROLE_CODES[role])
	query = (
		frappe.qb.from_(row)
		.join(desk)
		.on(desk.name == row.parent)
		.join(user)
		.on(user.name == row.user)
		.where(row.parenttype == DESK)
		.where(row.role_level.isin(codes))
	)
	if department:
		query = query.where(desk.department_scope == resolve_department(department))
	if service_category:
		query = query.where(desk.category_scope == resolve_service_category(service_category))
	if region:
		query = query.where(row.administrative_area == _resolve_region(region))
	if q:
		like = f"%{q.strip()}%"
		query = query.where(user.full_name.like(like) | user.email.like(like))
	return query


def records(user_ids: list[str]) -> list[dict]:
	"""API records for officers and reviewers, in the order given, with a fixed number of queries."""
	if not user_ids:
		return []
	rows = _rows(user_ids)
	by_user = defaultdict(list)
	for row in rows:
		by_user[row.user].append(row)

	supervisors = {row.reports_to for row in rows if row.reports_to}
	people = {
		person.name: person
		for person in frappe.get_all(
			"User", filters={"name": ["in", list(set(user_ids) | supervisors)]}, fields=USER_FIELDS
		)
	}
	area_names = {
		area.name: area.area_name
		for area in frappe.get_all(
			"Grievance Administrative Area",
			filters={
				"name": [
					"in",
					list({row.administrative_area for row in rows if row.administrative_area}),
				]
			},
			fields=["name", "area_name"],
		)
	}

	records = []
	for user_id in user_ids:
		person, every = people.get(user_id), by_user[user_id]
		if not person or not every:
			continue
		# An account that is somehow both is an officer: reviewer rows only add read access.
		role = DEFAULT_ROLE if any(row.role_level in CODE_LEVELS for row in every) else REVIEWER_ROLE
		own = [row for row in every if row.role_level in ROLE_CODES[role]]
		top = max(own, key=lambda row: row.level_order)
		region = next((row.administrative_area for row in own if row.administrative_area), None)
		supervisor = next((row.reports_to for row in own if row.reports_to), None)
		records.append(
			{
				"name": person.name,
				"full_name": person.full_name,
				"role": role,
				"designation": next((row.designation for row in own if row.designation), None),
				"level": CODE_LEVELS.get(top.role_level),
				"department": top.department_scope,
				"email": person.email,
				"phone": person.phone,
				"must_change_password": bool(person.get(MUST_CHANGE_PASSWORD_FIELD)),
				"region": region,
				"region_name": area_names.get(region),
				"status": _status(own),
				"service_categories": sorted({row.category_scope for row in own if row.category_scope}),
				"reports_to": supervisor,
				"reports_to_name": people[supervisor].full_name if supervisor in people else None,
				"assignments": [_assignment(row) for row in own],
			}
		)
	return records


def _assignment(row) -> dict:
	"""One desk the account sits on: the RBAC assignment the detail reports."""
	return {
		"assignment": row.parent,
		"service_category": row.category_scope,
		"department": row.department_scope,
		"level": CODE_LEVELS.get(row.role_level),
		"region": row.administrative_area,
		"active": bool(row.active),
		"on_leave": bool(row.on_leave),
	}


def record(user_id: str) -> dict:
	"""One officer or reviewer as an API record, or DoesNotExistError."""
	found = records([user_id])
	if not found:
		_not_found(user_id)
	return found[0]


def _current(user_id: str, role: str) -> SimpleNamespace:
	"""What an account is today, read from its desk rows of `role`."""
	rows = _rows([user_id], role)
	if not rows:
		_not_found(user_id)
	return SimpleNamespace(
		level=CODE_LEVELS.get(max(rows, key=lambda row: row.level_order).role_level),
		department=rows[0].department_scope,
		categories=sorted({row.category_scope for row in rows}),
		desks={row.parent for row in rows},
		status=_status(rows),
		designation=next((row.designation for row in rows if row.designation), None),
	)


def _not_found(user_id: str):
	frappe.throw(_("Officer '{0}' was not found.").format(user_id), frappe.DoesNotExistError)


def _rows(user_ids: list[str], role: str | None = None) -> list:
	"""The desk rows of `user_ids`, of `role` or of either, with the desk's scope and level order."""
	codes = ROLE_CODES[role] if role else [*ROLE_CODES[DEFAULT_ROLE], *ROLE_CODES[REVIEWER_ROLE]]
	row, desk, level = DocType(ROW), DocType(DESK), DocType("Grievance Role Level")
	return (
		frappe.qb.from_(row)
		.join(desk)
		.on(desk.name == row.parent)
		.join(level)
		.on(level.name == row.role_level)
		.where(row.parenttype == DESK)
		.where(row.user.isin(user_ids))
		.where(row.role_level.isin(list(codes)))
		.select(
			row.user,
			row.parent,
			row.role_level,
			row.administrative_area,
			row.designation,
			row.reports_to,
			row.active,
			row.on_leave,
			desk.department_scope,
			desk.category_scope,
			level.level_order,
		)
		.orderby(row.parent)
		.run(as_dict=True)
	)


def _category_desks(department: str, categories: list[str]) -> list:
	"""The category-only desk of `department` for each category. Every one must exist."""
	if not categories:
		frappe.throw(_("An officer needs at least one service category."), frappe.ValidationError)
	names = list(dict.fromkeys(resolve_service_category(value) for value in categories))
	desks = frappe.get_all(
		DESK,
		filters=category_assignment.desk_filters(department_scope=department, category_scope=["in", names]),
		fields=["name", "category_scope", "department_scope"],
	)
	missing = set(names) - {desk.category_scope for desk in desks}
	if missing:
		frappe.throw(
			_(
				"{0} has no category assignment for: {1}. Create it first. "
				"An assignment scoped to a grievance type or service provider does not count."
			).format(department, ", ".join(sorted(missing))),
			frappe.ValidationError,
		)
	return desks


def _assert_status(role: str, status: str) -> None:
	"""A Reviewer is never on leave: they take no cases, so there is nothing to skip."""
	if role == REVIEWER_ROLE and status == "On Leave":
		frappe.throw(_("A Reviewer is Active or Inactive, not On Leave."), frappe.ValidationError)


def _availability(status: str) -> dict:
	"""Officer-row flags for a status. On Leave stays active: the desk row and its permissions remain."""
	return {
		"active": 0 if status == "Inactive" else 1,
		"on_leave": 1 if status == "On Leave" else 0,
	}


def _status(rows: list) -> str:
	"""An officer's status from their desk rows: the most available one wins.

	Every write here keeps an officer's rows equal. They can differ only if an admin edits one
	desk directly, and then the officer is still reachable while any desk takes cases.
	"""
	if any(row.active and not row.on_leave for row in rows):
		return "Active"
	return "On Leave" if any(row.active for row in rows) else "Inactive"


def _status_filter(row, status: str):
	"""SQL for officers with a row in `status`, the same reading as `_status`."""
	if status == "Active":
		return (row.active == 1) & (row.on_leave == 0)
	if status == "On Leave":
		return (row.active == 1) & (row.on_leave == 1)
	return row.active == 0


def _placement(role: str, level: str | None, status: str) -> dict:
	"""Row fields that follow from the role, level and status."""
	if role == REVIEWER_ROLE:
		return {"role_level": C.ROLE_LEVEL_REVIEW_OFFICER, "is_primary": 0, **_availability(status)}
	return {
		"role_level": LEVEL_CODES[level],
		"is_primary": 1 if level == "L1" else 0,
		**_availability(status),
	}


def _change_level(user_id: str, old: str, new: str) -> dict:
	"""Row fields for moving an officer between L1, L2 and L3.

	Only an L1 reports to an L2, so any move clears the officer's own supervisor, and an L2
	cannot change level while L1 officers still report to them: escalation would climb to an L1.
	"""
	if old == "L2" and frappe.db.exists(ROW, {"reports_to": user_id, "parenttype": DESK}):
		frappe.throw(
			_("{0} cannot change level while other officers report to them.").format(user_id),
			frappe.ValidationError,
		)
	return {
		"role_level": LEVEL_CODES[new],
		"is_primary": 1 if new == "L1" else 0,
		"reports_to": None,
	}


def _resolve_region(value: str | None) -> str | None:
	if not value:
		return None
	name = resolve_administrative_area(value)
	if not name:
		frappe.throw(_("Region '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def _resolve_supervisor(user_id: str | None, level: str | None) -> str | None:
	"""Only an L1 reports to someone, and that someone is an L2 officer: escalation climbs to them."""
	if not user_id:
		return None
	if level != "L1":
		frappe.throw(_("Only an L1 officer reports to an L2 officer."), frappe.ValidationError)
	if not frappe.db.exists(ROW, {"user": user_id, "parenttype": DESK, "role_level": LEVEL_CODES["L2"]}):
		frappe.throw(_("{0} is not an L2 officer.").format(user_id), frappe.ValidationError)
	return user_id


def _save_desk(desk_name: str, user_id: str, values: dict | None, on_new: dict | None = None):
	"""Put `user_id` on a desk with `values`, or take them off when `values` is None.

	`on_new` fills the fields of a row that did not exist yet. The desk is locked so two
	admins editing the same desk are serialised, and its own validation runs on save.
	"""
	desk = frappe.get_doc(DESK, desk_name, for_update=True)
	mine = next((row for row in desk.officers if row.user == user_id), None)
	if values is None:
		if mine:
			if len(desk.officers) == 1:
				frappe.throw(
					_("{0} would be left without officers. Assign another officer first.").format(desk_name),
					frappe.ValidationError,
				)
			desk.remove(mine)
	elif mine:
		mine.update(values)
	else:
		desk.append("officers", {"user": user_id, **(on_new or {}), **values})
	desk.assigned_by = frappe.session.user
	desk.save()
