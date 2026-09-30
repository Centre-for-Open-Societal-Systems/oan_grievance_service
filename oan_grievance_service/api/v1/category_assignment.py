# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Category assignment CRUD for the Administration Category Assignments tab.

A category assignment is a category-only Grievance RBAC Assignment: one desk per
(department, service category), with no area, type, or provider scope. Several
departments can serve the same category. SLA days and auto-escalate are stored on
the category's Grievance SLA Configuration, which is shared by every department
serving that category. The desk's own `active` flag is the only on/off switch, so
deactivating a desk never touches the SLA row. Area-aware desks are left alone and
still win when they are the nearer match. Role levels and routing strategy are
read from the department.
"""

from collections import defaultdict
from typing import Annotated

import frappe
from frappe import _
from frappe.utils import today
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from oan_grievance_service.api.v1._pagination import PageParams, page_meta

route = prefixed("/api/v1/category-assignments")

DOCTYPE = "Grievance RBAC Assignment"
ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]
OFFICER_ROLES = {"Grievance Officer", "Grievance Admin"}
DESK_FIELDS = ["name", "category_scope", "department_scope", "routing_strategy", "active"]

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _blank_to_none(value):
	if isinstance(value, str) and not value.strip():
		return None
	return value


class CategoryAssignmentRecord(BaseModel):
	name: str
	service_category: str
	department: str
	l1_officer: str | None = None
	l1_officer_name: str | None = None
	l2_officer: str | None = None
	l2_officer_name: str | None = None
	sla_days: int | None = None
	auto_escalate: bool | None = None
	active: bool
	l1_role_level: str | None = None
	l2_role_level: str | None = None
	routing_strategy: str | None = None


class CategoryAssignmentData(BaseModel):
	assignment: CategoryAssignmentRecord


class CategoryAssignmentListData(BaseModel):
	assignments: list[CategoryAssignmentRecord]


class _Body(BaseModel):
	"""Rejects unknown fields. `cmd` is added by the request layer, not the client."""

	model_config = ConfigDict(extra="forbid")

	@model_validator(mode="before")
	@classmethod
	def _drop_cmd(cls, data):
		if isinstance(data, dict):
			return {key: value for key, value in data.items() if key != "cmd"}
		return data


class CreateCategoryAssignment(_Body):
	service_category: NonBlank
	department: NonBlank
	l1_officer: NonBlank
	l2_officer: NonBlank | None = None
	sla_days: int = Field(ge=1)
	auto_escalate: bool = True
	active: bool = True

	_l2_blank = field_validator("l2_officer", mode="before")(_blank_to_none)


class UpdateCategoryAssignment(_Body):
	"""Partial update. Omitted fields stay as they are, so only l2_officer accepts null."""

	assignment: str
	department: NonBlank = None
	l1_officer: NonBlank = None
	l2_officer: NonBlank | None = None
	sla_days: int = Field(default=None, ge=1)
	auto_escalate: bool = None
	active: bool = None

	_l2_blank = field_validator("l2_officer", mode="before")(_blank_to_none)

	def model_dump(self, **kwargs):
		return super().model_dump(**{"exclude_unset": True, **kwargs})


class ListCategoryAssignments(PageParams):
	model_config = ConfigDict(extra="ignore")

	service_category: str | None = None
	department: str | None = None
	active: bool | None = None

	_active_blank = field_validator("active", mode="before")(_blank_to_none)


def resolve_service_category(value: str) -> str:
	if frappe.db.exists("Grievance Service Category", value):
		return value
	name = frappe.db.get_value(
		"Grievance Service Category", {"category_name": value}, "name"
	) or frappe.db.get_value("Grievance Service Category", {"code": value}, "name")
	if not name:
		frappe.throw(_("Service category '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def resolve_department(value: str) -> str:
	if frappe.db.exists("Grievance Department", value):
		return value
	name = frappe.db.get_value("Grievance Department", {"dept_name": value}, "name") or frappe.db.get_value(
		"Grievance Department", {"short_name": value}, "name"
	)
	if not name:
		frappe.throw(_("Department '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def _desk_filters(**extra) -> dict:
	"""Filters that select category-only desks. Extra keys narrow or override them."""
	return {
		"category_scope": ["is", "set"],
		"administrative_area_scope": ["is", "not set"],
		"grievance_type_scope": ["is", "not set"],
		"service_provider_scope": ["is", "not set"],
		**extra,
	}


def _get_or_404(name: str):
	if not name or not frappe.db.exists(DOCTYPE, _desk_filters(name=name)):
		frappe.throw(
			_("Category assignment '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	return frappe.get_doc(DOCTYPE, name)


def _reject_duplicate(state: dict, exclude: str | None = None):
	filters = _desk_filters(category_scope=state["service_category"], department_scope=state["department"])
	if exclude:
		filters["name"] = ["!=", exclude]
	existing = frappe.db.get_value(DOCTYPE, filters, "name")
	if existing:
		frappe.throw(
			_("A category assignment already exists for {0} in {1} ({2}).").format(
				state["service_category"], state["department"], existing
			),
			frappe.DuplicateEntryError,
		)


def _assert_officer(user: str, label: str):
	if not frappe.db.exists("User", user):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, user), frappe.ValidationError)
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} '{1}' is disabled.").format(label, user), frappe.ValidationError)
	if not OFFICER_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(
			_("{0} must be a Grievance Officer or Grievance Admin.").format(label),
			frappe.ValidationError,
		)


def _assert_active(doctype: str, name: str, flag_field: str, label: str):
	if not frappe.db.get_value(doctype, name, flag_field):
		frappe.throw(_("{0} '{1}' is inactive.").format(label, name), frappe.ValidationError)


def _validate_links(state: dict):
	"""Check every link a save depends on. Returns the department's routing preferences."""
	l1, l2 = state["l1_officer"], state.get("l2_officer")
	if l2 and l1 == l2:
		frappe.throw(_("L1 and L2 officers must be different users."), frappe.ValidationError)
	_assert_officer(l1, _("L1 officer"))
	if l2:
		_assert_officer(l2, _("L2 officer"))
	_assert_active(
		"Grievance Service Category", state["service_category"], "is_active", _("Service category")
	)
	_assert_active("Grievance Department", state["department"], "active", _("Department"))
	prefs = frappe.db.get_value(
		"Grievance Department",
		state["department"],
		["l1_role_level", "l2_role_level", "routing_strategy"],
		as_dict=True,
	)
	if not prefs.l1_role_level:
		frappe.throw(_("Department must set an L1 role level."), frappe.ValidationError)
	if l2 and not prefs.l2_role_level:
		frappe.throw(_("Department must set an L2 role level."), frappe.ValidationError)
	return prefs


def _write_desk(desk, state: dict, prefs):
	"""Copy state onto the desk. Existing officer rows are updated in place."""
	desk.department_scope = state["department"]
	desk.active = 1 if state["active"] else 0
	if prefs.routing_strategy:
		desk.routing_strategy = prefs.routing_strategy
	wanted = {state["l1_officer"]: {"role_level": prefs.l1_role_level, "is_primary": 1, "reports_to": None}}
	if state.get("l2_officer"):
		wanted[state["l2_officer"]] = {
			"role_level": prefs.l2_role_level,
			"is_primary": 0,
			"reports_to": state["l1_officer"],
		}
	for row in list(desk.officers):
		if row.user not in wanted:
			desk.remove(row)
	rows = {row.user: row for row in desk.officers}
	for user, values in wanted.items():
		row = rows.get(user) or desk.append("officers", {"user": user})
		row.update({**values, "active": 1})


def _save_sla(service_category: str, sla_days: int, auto_escalate: bool):
	name = frappe.db.get_value("Grievance SLA Configuration", {"service_category": service_category})
	sla = (
		frappe.get_doc("Grievance SLA Configuration", name)
		if name
		else frappe.new_doc("Grievance SLA Configuration")
	)
	sla.service_category = service_category
	sla.sla_days = sla_days
	sla.auto_escalate = 1 if auto_escalate else 0
	sla.save()


def _split_officers(rows: list):
	"""Primary is the active is_primary row, else the first active row. Secondary is the next non-primary."""
	active = [row for row in rows if row.active]
	primary = next((row for row in active if row.is_primary), active[0] if active else None)
	secondary = next((row for row in active if row is not primary and not row.is_primary), None)
	return primary, secondary


def _records(desks: list) -> list[dict]:
	"""Project desk rows to API records with one query each for officers, SLA rows, and names."""
	if not desks:
		return []
	officers = defaultdict(list)
	for row in frappe.get_all(
		"Grievance RBAC Assignment Officer",
		filters={"parent": ["in", [desk.name for desk in desks]], "parenttype": DOCTYPE},
		fields=["parent", "user", "role_level", "is_primary", "active"],
		order_by="parent, idx",
	):
		officers[row.parent].append(row)
	sla_rows = {
		row.service_category: row
		for row in frappe.get_all(
			"Grievance SLA Configuration",
			filters={"service_category": ["in", list({desk.category_scope for desk in desks})]},
			fields=["service_category", "sla_days", "auto_escalate"],
		)
	}
	splits = [_split_officers(officers[desk.name]) for desk in desks]
	full_names = {
		row.name: row.full_name
		for row in frappe.get_all(
			"User",
			filters={"name": ["in", list({row.user for pair in splits for row in pair if row})]},
			fields=["name", "full_name"],
		)
	}
	records = []
	for desk, (primary, secondary) in zip(desks, splits, strict=True):
		sla = sla_rows.get(desk.category_scope)
		records.append(
			CategoryAssignmentRecord(
				name=desk.name,
				service_category=desk.category_scope,
				department=desk.department_scope,
				l1_officer=primary.user if primary else None,
				l1_officer_name=full_names.get(primary.user) if primary else None,
				l2_officer=secondary.user if secondary else None,
				l2_officer_name=full_names.get(secondary.user) if secondary else None,
				sla_days=sla.sla_days if sla else None,
				auto_escalate=bool(sla.auto_escalate) if sla else None,
				active=bool(desk.active),
				l1_role_level=primary.role_level if primary else None,
				l2_role_level=secondary.role_level if secondary else None,
				routing_strategy=desk.routing_strategy or None,
			).model_dump()
		)
	return records


def _record(name: str) -> dict:
	return _records(frappe.get_all(DOCTYPE, filters={"name": name}, fields=DESK_FIELDS))[0]


def _update(desk, changes: dict):
	"""Apply a partial update. Deactivation skips link checks so a broken desk can always be retired."""
	if changes == {"active": False}:
		desk.active = 0
		desk.save()
		return
	current = _record(desk.name)
	state = {
		"service_category": desk.category_scope,
		"department": current["department"],
		"l1_officer": current["l1_officer"],
		"l2_officer": current["l2_officer"],
		"sla_days": current["sla_days"],
		"auto_escalate": current["auto_escalate"],
		"active": current["active"],
		**changes,
	}
	if "department" in changes:
		state["department"] = resolve_department(changes["department"])
		_reject_duplicate(state, exclude=desk.name)
	prefs = _validate_links(state)
	_write_desk(desk, state, prefs)
	desk.save()
	if {"sla_days", "auto_escalate"} & changes.keys():
		_save_sla(desk.category_scope, state["sla_days"], state["auto_escalate"])


@route("", methods=("GET",), summary="List category assignments")
@frappe.whitelist()
@validate_request(ListCategoryAssignments)
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="List category assignments",
	description="Admin list of category-to-department routing rules, filterable by category, department, and active flag.",
	tags=["Administration"],
	response_model=CategoryAssignmentListData,
)
def list_assignments(
	service_category: str | None = None,
	department: str | None = None,
	active: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List category-only RBAC desks for the admin tab.

	Numeric and boolean parameters also accept str: frappe checks annotations before
	validate_request runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	filters = _desk_filters()
	if service_category:
		filters["category_scope"] = resolve_service_category(service_category)
	if department:
		filters["department_scope"] = resolve_department(department)
	if active is not None:
		filters["active"] = 1 if active else 0
	desks = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=DESK_FIELDS,
		order_by="modified desc, name desc",
		offset=params.start,
		limit_page_length=params.page_size,
	)
	return success_response(
		data={"assignments": _records(desks)},
		message=_("Category assignments retrieved"),
		pagination=page_meta(params, frappe.db.count(DOCTYPE, filters)),
	)


@route("/<assignment>", methods=("GET",), summary="Get a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Get a category assignment",
	description="One category routing rule. The id is the category-only Grievance RBAC Assignment.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def get_assignment(assignment: str):
	"""Return one category-only RBAC assignment."""
	return success_response(
		data={"assignment": _record(_get_or_404(assignment).name)},
		message=_("Category assignment retrieved"),
	)


@route("", methods=("POST",), summary="Create a category assignment")
@frappe.whitelist()
@validate_request(CreateCategoryAssignment)
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Create a category assignment",
	description="Create the category-only Grievance RBAC Assignment for one department and service category. One rule per department and category, including inactive rules.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def create_assignment(
	service_category: str,
	department: str,
	l1_officer: str,
	sla_days: int | str,
	l2_officer: str | None = None,
	auto_escalate: bool | str = True,
	active: bool | str = True,
	**kwargs,
):
	"""Create a category-only RBAC desk and set the category's SLA."""
	state = {
		"service_category": resolve_service_category(service_category),
		"department": resolve_department(department),
		"l1_officer": l1_officer,
		"l2_officer": l2_officer,
		"sla_days": sla_days,
		"auto_escalate": auto_escalate,
		"active": active,
	}
	_reject_duplicate(state)
	prefs = _validate_links(state)
	desk = frappe.new_doc(DOCTYPE)
	desk.effective_from = today()
	desk.category_scope = state["service_category"]
	desk.assigned_by = frappe.session.user
	_write_desk(desk, state, prefs)
	desk.insert()
	_save_sla(state["service_category"], sla_days, auto_escalate)
	return success_response(
		data={"assignment": _record(desk.name)},
		message=_("Category assignment created"),
	)


@route("/<assignment>", methods=("PATCH",), summary="Update a category assignment")
@frappe.whitelist()
@validate_request(UpdateCategoryAssignment)
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Update a category assignment",
	description="Change department, officers, SLA window, or the auto-escalate and active flags. The service category is fixed once created.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def update_assignment(assignment: str, **kwargs):
	"""Update a category-only RBAC desk. `kwargs` holds only the fields the client sent."""
	desk = _get_or_404(assignment)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	_update(desk, kwargs)
	return success_response(
		data={"assignment": _record(desk.name)},
		message=_("Category assignment updated"),
	)


@route("/<assignment>", methods=("DELETE",), summary="Deactivate a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Deactivate a category assignment",
	description="Retire a category routing rule. The RBAC desk stays for audit and is marked inactive. The category's SLA row is not changed. Same as PATCH with active false.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def deactivate_assignment(assignment: str):
	"""Deactivate a category-only RBAC desk. Repeating the call is a no-op."""
	desk = _get_or_404(assignment)
	_update(desk, {"active": False})
	return success_response(
		data={"assignment": _record(desk.name)},
		message=_("Category assignment deactivated"),
	)
