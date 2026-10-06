# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer management over the existing User and Grievance RBAC Assignment Officer model.

There is no officer record of its own. An officer is a User with Grievance RBAC Assignment
Officer rows, one on each category desk they staff, so routing, permissions and escalation
read exactly what an admin edits here:

- the person (name, email, phone, designation) is the User;
- the level (L1 Nodal, L2 Senior Nodal) is the row's role level;
- the service categories are the category desks the officer sits on;
- the region, the supervisor (`reports_to`) and availability are on the officer's rows.

An officer is never deleted. Status is read from the rows, so it has no field of its own and
nothing on User: Active is an active row, On Leave is an active row with `on_leave` set (the
desk and its permissions stay, but auto-routing skips the officer), and Inactive is a row with
`active` cleared. Changing status writes every desk row of the officer.
"""

from collections import defaultdict
from types import SimpleNamespace

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count

from oan_grievance_service.services import category_assignment
from oan_grievance_service.services.resolvers import (
	resolve_administrative_area,
	resolve_department,
	resolve_service_category,
)

DESK = category_assignment.DOCTYPE
ROW = "Grievance RBAC Assignment Officer"
OFFICER_ROLE = "Grievance Officer"
LEVEL_CODES = {"L1": "nodal_officer", "L2": "senior_nodal_officer"}
CODE_LEVELS = {code: level for level, code in LEVEL_CODES.items()}
DEFAULT_STATUS = "Active"
USER_FIELDS = [
	"name",
	"full_name",
	"email",
	"phone",
	"grievance_designation",
]


def create(
	*,
	full_name: str,
	designation: str,
	level: str,
	department: str,
	email: str,
	service_categories: list[str],
	phone: str | None = None,
	region: str | None = None,
	status: str = DEFAULT_STATUS,
	reports_to: str | None = None,
) -> str:
	"""Make `email` an officer on the category desks of `department`. Returns the user id.

	An existing login with no desk rows is promoted, so an admin can staff someone who
	already has an account. A login that is already an officer is rejected.
	"""
	desks = _category_desks(resolve_department(department), service_categories)
	if _rows([email]):
		frappe.throw(_("{0} is already an officer.").format(email), frappe.ValidationError)
	placement = _placement(level, status)
	placement["administrative_area"] = _resolve_region(region)
	placement["reports_to"] = _resolve_supervisor(reports_to, level)

	user = _new_or_existing_user(email)
	_write_user(user, {"full_name": full_name, "designation": designation, "phone": phone})
	for desk in desks:
		_save_desk(desk.name, user.name, placement)
	return user.name


def update(user_id: str, changes: dict) -> str:
	"""Apply a partial update. `department` and `service_categories` re-seat the officer on desks."""
	current = _current(user_id)
	changes = dict(changes)
	status = changes.pop("status", None)
	person = {key: changes.pop(key) for key in ("full_name", "designation", "phone") if key in changes}
	if person:
		_write_user(frappe.get_doc("User", user_id), person)

	row_changes = _availability(status) if status else {}
	if "region" in changes:
		row_changes["administrative_area"] = _resolve_region(changes["region"])
	if "reports_to" in changes:
		row_changes["reports_to"] = _resolve_supervisor(changes["reports_to"], current.level)

	department = resolve_department(changes["department"]) if "department" in changes else current.department
	desks = _category_desks(department, changes.get("service_categories", current.categories))
	target = {desk.name for desk in desks}
	for name in current.desks - target:
		_save_desk(name, user_id, None)
	for name in sorted(target):
		_save_desk(name, user_id, row_changes, on_new=_placement(current.level, status or current.status))
	return user_id


def list_officers(*, level=None, department=None, status=None, q=None, start=0, page_size=20):
	"""One page of officer ids and the total, filtered and counted in SQL."""
	row, desk, user = DocType(ROW), DocType(DESK), DocType("User")
	query = (
		frappe.qb.from_(row)
		.join(desk)
		.on(desk.name == row.parent)
		.join(user)
		.on(user.name == row.user)
		.where(row.parenttype == DESK)
		.where(row.role_level.isin([LEVEL_CODES[level]] if level else list(CODE_LEVELS)))
	)
	if department:
		query = query.where(desk.department_scope == resolve_department(department))
	if status:
		query = query.where(_status_filter(row, status))
	if q:
		like = f"%{q.strip()}%"
		query = query.where(user.full_name.like(like) | user.email.like(like))

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


def records(user_ids: list[str]) -> list[dict]:
	"""API records for officers, in the order given, with a fixed number of queries."""
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
				"name": ["in", list({row.administrative_area for row in rows if row.administrative_area})]
			},
			fields=["name", "area_name"],
		)
	}

	records = []
	for user_id in user_ids:
		person, own = people.get(user_id), by_user[user_id]
		if not person or not own:
			continue
		top = max(own, key=lambda row: row.level_order)
		region = next((row.administrative_area for row in own if row.administrative_area), None)
		supervisor = next((row.reports_to for row in own if row.reports_to), None)
		records.append(
			{
				"name": person.name,
				"full_name": person.full_name,
				"designation": person.grievance_designation,
				"level": CODE_LEVELS[top.role_level],
				"department": top.department_scope,
				"email": person.email,
				"phone": person.phone,
				"region": region,
				"region_name": area_names.get(region),
				"status": _status(own),
				"service_categories": sorted({row.category_scope for row in own if row.category_scope}),
				"reports_to": supervisor,
				"reports_to_name": people[supervisor].full_name if supervisor in people else None,
			}
		)
	return records


def record(user_id: str) -> dict:
	"""One officer as an API record, or DoesNotExistError."""
	found = records([user_id])
	if not found:
		_not_found(user_id)
	return found[0]


def _current(user_id: str) -> SimpleNamespace:
	"""What an officer is today, read from their desk rows."""
	rows = _rows([user_id])
	if not rows:
		_not_found(user_id)
	return SimpleNamespace(
		level=CODE_LEVELS[max(rows, key=lambda row: row.level_order).role_level],
		department=rows[0].department_scope,
		categories=sorted({row.category_scope for row in rows}),
		desks={row.parent for row in rows},
		status=_status(rows),
	)


def _not_found(user_id: str):
	frappe.throw(_("Officer '{0}' was not found.").format(user_id), frappe.DoesNotExistError)


def _rows(user_ids: list[str]) -> list:
	"""Every officer row of `user_ids` on a category desk, with its desk's scope and level order."""
	row, desk, level = DocType(ROW), DocType(DESK), DocType("Grievance Role Level")
	return (
		frappe.qb.from_(row)
		.join(desk)
		.on(desk.name == row.parent)
		.join(level)
		.on(level.name == row.role_level)
		.where(row.parenttype == DESK)
		.where(row.user.isin(user_ids))
		.where(row.role_level.isin(list(CODE_LEVELS)))
		.select(
			row.user,
			row.parent,
			row.role_level,
			row.administrative_area,
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
			_("{0} has no category assignment for: {1}. Create it first.").format(
				department, ", ".join(sorted(missing))
			),
			frappe.ValidationError,
		)
	return desks


def _availability(status: str) -> dict:
	"""Officer-row flags for a status. On Leave stays active: the desk row and its permissions remain."""
	return {"active": 0 if status == "Inactive" else 1, "on_leave": 1 if status == "On Leave" else 0}


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


def _placement(level: str, status: str) -> dict:
	"""Officer-row fields that follow from the level and status."""
	return {
		"role_level": LEVEL_CODES[level],
		"is_primary": 1 if level == "L1" else 0,
		**_availability(status),
	}


def _resolve_region(value: str | None) -> str | None:
	if not value:
		return None
	name = resolve_administrative_area(value)
	if not name:
		frappe.throw(_("Region '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def _resolve_supervisor(user_id: str | None, level: str) -> str | None:
	"""Only an L1 reports to someone, and that someone is an L2 officer: escalation climbs to them."""
	if not user_id:
		return None
	if level != "L1":
		frappe.throw(_("Only an L1 officer reports to an L2 officer."), frappe.ValidationError)
	if not frappe.db.exists(ROW, {"user": user_id, "parenttype": DESK, "role_level": LEVEL_CODES["L2"]}):
		frappe.throw(_("{0} is not an L2 officer.").format(user_id), frappe.ValidationError)
	return user_id


def _new_or_existing_user(email: str):
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": email, "send_welcome_email": 0, "enabled": 1}
	)
	user.insert(ignore_permissions=True)
	return user


def _write_user(user, fields: dict):
	"""Set the person-level fields that were given and make sure the User holds the officer role."""
	if "full_name" in fields:
		user.first_name, user.last_name = fields["full_name"], ""
	for key, field in (("designation", "grievance_designation"), ("phone", "phone")):
		if key in fields:
			user.set(field, fields[key])
	if OFFICER_ROLE not in {role.role for role in user.roles}:
		user.append("roles", {"role": OFFICER_ROLE})
	user.save(ignore_permissions=True)


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
