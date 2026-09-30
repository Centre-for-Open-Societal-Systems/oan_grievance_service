# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Category assignment CRUD for the Administration Category Assignments tab.

The record is a category-only Grievance RBAC Assignment: one desk per service
category, with no area, type, or provider scope. SLA days, auto-escalate,
priority, and notify-on-submit are stored on the category's Grievance SLA
Configuration. Area-aware desks are left alone and still win when they are
the nearer match. Role levels and routing strategy are read from the department.
"""

import math
from typing import Literal

import frappe
from frappe import _
from frappe.utils import today
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import api_doc, handle_api_errors, require_role, success_response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

route = prefixed("/api/v1/category-assignments")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]
PRIORITIES = ("Low", "Normal", "High")
OFFICER_ROLES = {"Grievance Officer", "Grievance Admin"}
ROUTING_STRATEGIES = ("Primary First", "Round Robin", "Least Loaded")
_PRIORITY_ALIASES = {
	"low": "Low",
	"normal": "Normal",
	"high": "High",
	"high priority": "High",
}


def coerce_bool(value):
	if isinstance(value, bool):
		return value
	if isinstance(value, int) and value in (0, 1):
		return bool(value)
	if isinstance(value, str):
		token = value.strip().lower()
		if token in {"1", "true", "yes", "on"}:
			return True
		if token in {"0", "false", "no", "off"}:
			return False
	raise ValueError("expected a boolean")


def normalize_priority(value: str) -> str:
	key = " ".join(str(value).strip().split()).lower()
	mapped = _PRIORITY_ALIASES.get(key)
	if not mapped:
		raise ValueError("priority must be Low, Normal, or High")
	return mapped


def _bool_field(value):
	if value is None or value == "":
		return value
	return coerce_bool(value)


def _optional_user(value):
	if value is None:
		return None
	text = str(value).strip()
	if text == "" or text.lower() == "none":
		return None
	return text


def _required_text(value):
	"""Reject null and blank. Omitted fields never reach this validator."""
	if value is None or not str(value).strip() or str(value).strip().lower() == "none":
		raise ValueError("must not be null or empty")
	return str(value).strip()


class CategoryAssignmentRecord(BaseModel):
	model_config = ConfigDict(extra="ignore")

	name: str
	service_category: str
	department: str
	l1_officer: str
	l1_officer_name: str | None = None
	l2_officer: str | None = None
	l2_officer_name: str | None = None
	priority: str
	sla_days: int
	auto_escalate: bool
	notify_on_submit: bool
	active: bool
	l1_role_level: str | None = None
	l2_role_level: str | None = None
	routing_strategy: str | None = None
	rbac_assignment: str | None = None


class Pagination(BaseModel):
	page: int
	page_size: int
	total_count: int
	total_pages: int
	has_next: bool
	has_prev: bool


class CategoryAssignmentData(BaseModel):
	assignment: CategoryAssignmentRecord


class CategoryAssignmentListData(BaseModel):
	assignments: list[CategoryAssignmentRecord]
	pagination: Pagination


class CreateCategoryAssignment(BaseModel):
	model_config = ConfigDict(extra="forbid")

	service_category: str = Field(min_length=1)
	department: str = Field(min_length=1)
	l1_officer: str = Field(min_length=1)
	l2_officer: str | None = None
	priority: str = "Normal"
	sla_days: int = Field(ge=1)
	auto_escalate: bool = True
	notify_on_submit: bool = True
	active: bool = True

	@field_validator("priority", mode="before")
	@classmethod
	def _priority(cls, value):
		if value is None or value == "":
			return "Normal"
		return normalize_priority(value)

	@field_validator("auto_escalate", "notify_on_submit", "active", mode="before")
	@classmethod
	def _flags(cls, value):
		return _bool_field(value)

	@field_validator("l2_officer", mode="before")
	@classmethod
	def _l2(cls, value):
		return _optional_user(value)


class UpdateCategoryAssignment(BaseModel):
	model_config = ConfigDict(extra="forbid")

	service_category: str | None = None
	department: str | None = None
	l1_officer: str | None = None
	l2_officer: str | None = None
	priority: str | None = None
	sla_days: int | None = Field(default=None, ge=1)
	auto_escalate: bool | None = None
	notify_on_submit: bool | None = None
	active: bool | None = None

	@field_validator("priority", mode="before")
	@classmethod
	def _priority(cls, value):
		if value is None or value == "":
			return None
		return normalize_priority(value)

	@field_validator("auto_escalate", "notify_on_submit", "active", mode="before")
	@classmethod
	def _flags(cls, value):
		return _bool_field(value)

	@field_validator("l1_officer", "service_category", "department", mode="before")
	@classmethod
	def _required_when_sent(cls, value):
		return _required_text(value)

	@field_validator("l2_officer", mode="before")
	@classmethod
	def _l2(cls, value):
		return _optional_user(value)


def resolve_service_category(value: str) -> str:
	if frappe.db.exists("Grievance Service Category", value):
		return value
	name = frappe.db.get_value(
		"Grievance Service Category", {"category_name": value}, "name"
	) or frappe.db.get_value("Grievance Service Category", {"code": value}, "name")
	if not name:
		frappe.throw(
			_("Service category '{0}' does not exist.").format(value),
			frappe.ValidationError,
		)
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


def resolve_officer(value: str | None) -> str | None:
	if not value:
		return None
	if frappe.db.exists("User", value):
		return value
	frappe.throw(_("Officer '{0}' does not exist.").format(value), frappe.ValidationError)


def _get_or_404(name: str):
	if not name or not frappe.db.exists("Grievance RBAC Assignment", name):
		frappe.throw(
			_("Category assignment '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	doc = frappe.get_doc("Grievance RBAC Assignment", name)
	if not _is_category_desk(doc):
		frappe.throw(
			_("Category assignment '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	return doc


def _user_names(users: set[str]) -> dict[str, str]:
	users = {user for user in users if user}
	if not users:
		return {}
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", list(users)]},
		fields=["name", "full_name"],
	)
	return {row.name: row.full_name for row in rows}


def _record(source, names: dict[str, str] | None = None) -> dict:
	l1 = source.get("l1_officer")
	l2 = source.get("l2_officer") or None
	if names is None:
		names = _user_names({l1, l2} if l2 else {l1})
	return {
		"name": source.get("name"),
		"service_category": source.get("service_category"),
		"department": source.get("department"),
		"l1_officer": l1,
		"l1_officer_name": names.get(l1),
		"l2_officer": l2,
		"l2_officer_name": names.get(l2) if l2 else None,
		"priority": source.get("priority") or "Normal",
		"sla_days": int(source.get("sla_days") or 0),
		"auto_escalate": bool(source.get("auto_escalate")),
		"notify_on_submit": bool(source.get("notify_on_submit")),
		"active": bool(source.get("active")),
		"l1_role_level": source.get("l1_role_level") or None,
		"l2_role_level": source.get("l2_role_level") or None,
		"routing_strategy": source.get("routing_strategy") or None,
		"rbac_assignment": source.get("rbac_assignment") or None,
	}


def _apply(current: dict, changes: dict) -> dict:
	merged = {
		"service_category": current.get("service_category"),
		"department": current.get("department"),
		"l1_officer": current.get("l1_officer"),
		"l2_officer": current.get("l2_officer"),
		"priority": current.get("priority") or "Normal",
		"sla_days": current.get("sla_days"),
		"auto_escalate": current.get("auto_escalate", 1),
		"notify_on_submit": current.get("notify_on_submit", 1),
		"active": current.get("active", 1),
	}
	if "service_category" in changes:
		merged["service_category"] = resolve_service_category(
			_present(changes, "service_category", "Service category")
		)
	if "department" in changes:
		merged["department"] = resolve_department(_present(changes, "department", "Department"))
	if "l1_officer" in changes:
		merged["l1_officer"] = resolve_officer(_present(changes, "l1_officer", "L1 officer"))
	if "l2_officer" in changes:
		merged["l2_officer"] = resolve_officer(changes["l2_officer"])
	if changes.get("priority"):
		merged["priority"] = changes["priority"]
	if changes.get("sla_days") is not None:
		merged["sla_days"] = changes["sla_days"]
	for flag in ("auto_escalate", "notify_on_submit", "active"):
		if changes.get(flag) is not None:
			merged[flag] = 1 if changes[flag] else 0
	return merged


def _present(changes: dict, key: str, label: str):
	value = changes.get(key)
	if value is None or (isinstance(value, str) and not value.strip()):
		frappe.throw(_("{0} is required.").format(label), frappe.ValidationError)
	return value


def _reject_duplicate(service_category: str):
	existing = _category_desk_name(service_category)
	if existing:
		frappe.throw(
			_("A category assignment already exists for {0} ({1}).").format(service_category, existing),
			frappe.DuplicateEntryError,
		)


def _page(page, page_size) -> tuple[int, int]:
	if page in (None, ""):
		page = 1
	if page_size in (None, ""):
		page_size = 20
	try:
		page_no = int(page)
		size = int(page_size)
	except (TypeError, ValueError):
		frappe.throw(_("page and page_size must be integers."), frappe.ValidationError)
	if page_no < 1 or size < 1 or size > 100:
		frappe.throw(
			_("page must be at least 1 and page_size must be between 1 and 100."),
			frappe.ValidationError,
		)
	return page_no, size


def _body(kwargs: dict) -> dict:
	return {key: value for key, value in kwargs.items() if key not in {"cmd", "assignment"}}


def _is_category_desk(doc) -> bool:
	if not doc.get("category_scope"):
		return False
	if doc.get("administrative_area_scope") or doc.get("grievance_type_scope"):
		return False
	return not (doc.get("service_provider_scope") or "").strip()


def _category_desk_filters(service_category=None, department=None, active=None) -> dict:
	filters = {
		"category_scope": service_category or ["is", "set"],
		"administrative_area_scope": ["is", "not set"],
		"grievance_type_scope": ["is", "not set"],
	}
	if department:
		filters["department_scope"] = department
	if active is not None:
		filters["active"] = active
	return filters


def _category_desk_name(service_category: str) -> str | None:
	for name in frappe.get_all(
		"Grievance RBAC Assignment",
		filters=_category_desk_filters(service_category),
		pluck="name",
	):
		if _is_category_desk(frappe.get_doc("Grievance RBAC Assignment", name)):
			return name
	return None


def _department_prefs(department: str) -> dict:
	return (
		frappe.db.get_value(
			"Grievance Department",
			department,
			["l1_role_level", "l2_role_level", "routing_strategy"],
			as_dict=True,
		)
		or {}
	)


class CategoryAssignmentRules(BaseModel):
	"""Desk-save checks for one category-only RBAC assignment."""

	model_config = ConfigDict(extra="ignore")

	service_category: str = Field(min_length=1)
	department: str = Field(min_length=1)
	l1_officer: str = Field(min_length=1)
	l2_officer: str | None = None
	priority: Literal["Low", "Normal", "High"]
	sla_days: int = Field(ge=1)
	l1_role_level: str | None = None
	l2_role_level: str | None = None
	routing_strategy: str | None = None

	@model_validator(mode="after")
	def _check_links(self):
		if self.l2_officer and self.l1_officer == self.l2_officer:
			raise ValueError("L1 and L2 officers must be different users.")
		_assert_officer(self.l1_officer, "L1 officer")
		if self.l2_officer:
			_assert_officer(self.l2_officer, "L2 officer")
		_assert_active_link(
			"Grievance Service Category", self.service_category, "is_active", "Service category"
		)
		_assert_active_link("Grievance Department", self.department, "active", "Department")
		if not self.l1_role_level:
			raise ValueError("Department must set an L1 role level.")
		_assert_role_level(self.l1_role_level, "L1 role level")
		if self.l2_officer:
			if not self.l2_role_level:
				raise ValueError("Department must set an L2 role level.")
			_assert_role_level(self.l2_role_level, "L2 role level")
		if self.routing_strategy and self.routing_strategy not in ROUTING_STRATEGIES:
			raise ValueError("Routing strategy must be Primary First, Round Robin, or Least Loaded.")
		return self


def _rules(state: dict) -> CategoryAssignmentRules:
	prefs = _department_prefs(state["department"])
	try:
		return CategoryAssignmentRules.model_validate(
			{
				**state,
				"l1_role_level": prefs.get("l1_role_level") or None,
				"l2_role_level": prefs.get("l2_role_level") or None,
				"routing_strategy": prefs.get("routing_strategy") or None,
			}
		)
	except ValidationError as exc:
		frappe.throw(_(_pydantic_message(exc)), frappe.ValidationError)


def _pydantic_message(exc: ValidationError) -> str:
	message = exc.errors()[0].get("msg") or "Validation failed"
	prefix = "Value error, "
	if message.startswith(prefix):
		return message[len(prefix) :]
	return message


def _assert_role_level(level, label):
	if not frappe.db.exists("Grievance Role Level", level):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, level), frappe.ValidationError)


def _assert_officer(user, label):
	if not user or not frappe.db.exists("User", user):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, user), frappe.ValidationError)
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} '{1}' is disabled.").format(label, user), frappe.ValidationError)
	if not OFFICER_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(
			_("{0} must be a Grievance Officer or Grievance Admin.").format(label),
			frappe.ValidationError,
		)


def _assert_active_link(doctype, name, flag_field, label):
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} '{1}' does not exist.").format(label, name), frappe.ValidationError)
	if not frappe.db.get_value(doctype, name, flag_field):
		frappe.throw(_("{0} '{1}' is inactive.").format(label, name), frappe.ValidationError)


def _sla_row(service_category: str) -> dict:
	row = frappe.db.get_value(
		"Grievance SLA Configuration",
		{"service_category": service_category},
		["name", "sla_days", "auto_escalate", "priority", "notify_on_submit", "active"],
		as_dict=True,
	)
	return row or {}


def _state(desk) -> dict:
	officers = list(desk.officers or [])
	primary = next((row for row in officers if row.is_primary), officers[0] if officers else None)
	secondary = next((row for row in officers if not primary or row.user != primary.user), None)
	sla = _sla_row(desk.category_scope)
	return {
		"service_category": desk.category_scope,
		"department": desk.department_scope,
		"l1_officer": primary.user if primary else None,
		"l2_officer": secondary.user if secondary else None,
		"priority": sla.get("priority") or "Normal",
		"sla_days": sla.get("sla_days"),
		"auto_escalate": sla.get("auto_escalate", 1),
		"notify_on_submit": sla.get("notify_on_submit", 1),
		"active": desk.active,
	}


def _write_officers(desk, state: dict, rules: CategoryAssignmentRules):
	desk.department_scope = state["department"]
	desk.active = 1 if state["active"] else 0
	if rules.routing_strategy:
		desk.routing_strategy = rules.routing_strategy
	desk.set("officers", [])
	desk.append(
		"officers",
		{
			"user": state["l1_officer"],
			"role_level": rules.l1_role_level,
			"is_primary": 1,
			"active": 1,
		},
	)
	if state.get("l2_officer"):
		desk.append(
			"officers",
			{
				"user": state["l2_officer"],
				"role_level": rules.l2_role_level,
				"reports_to": state["l1_officer"],
				"is_primary": 0,
				"active": 1,
			},
		)


def _save_sla(state: dict):
	values = {
		"sla_days": int(state["sla_days"]),
		"auto_escalate": 1 if state["auto_escalate"] else 0,
		"priority": state["priority"] or "Normal",
		"notify_on_submit": 1 if state["notify_on_submit"] else 0,
		"active": 1 if state["active"] else 0,
	}
	name = frappe.db.get_value(
		"Grievance SLA Configuration", {"service_category": state["service_category"]}, "name"
	)
	if name:
		frappe.db.set_value("Grievance SLA Configuration", name, values)
		return
	frappe.get_doc(
		{"doctype": "Grievance SLA Configuration", "service_category": state["service_category"], **values}
	).insert()


def _view(desk) -> dict:
	state = _state(desk)
	officers = list(desk.officers or [])
	primary = next((row for row in officers if row.is_primary), officers[0] if officers else None)
	secondary = next((row for row in officers if primary and row.user != primary.user), None)
	return {
		**state,
		"name": desk.name,
		"l1_role_level": primary.role_level if primary else None,
		"l2_role_level": secondary.role_level if secondary else None,
		"routing_strategy": desk.routing_strategy or None,
		"rbac_assignment": desk.name,
	}


@route("", methods=("GET",), summary="List category assignments")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="List category assignments",
	description="Admin list of category-to-department routing rules, filterable by category, department, priority, and active flag.",
	tags=["Administration"],
	response_model=CategoryAssignmentListData,
)
def list_assignments(
	service_category: str | None = None,
	department: str | None = None,
	priority: str | None = None,
	active: str | None = None,
	page: str | int | None = 1,
	page_size: str | int | None = 20,
):
	"""List category-only RBAC desks for the admin tab."""
	page_no, size = _page(page, page_size)
	service_category_name = resolve_service_category(service_category) if service_category else None
	department_name = resolve_department(department) if department else None
	priority_name = None
	if priority:
		try:
			priority_name = normalize_priority(priority)
		except ValueError as exc:
			frappe.throw(str(exc), frappe.ValidationError)
	active_flag = None
	if active not in (None, ""):
		try:
			active_flag = 1 if coerce_bool(active) else 0
		except ValueError as exc:
			frappe.throw(str(exc), frappe.ValidationError)

	rows = frappe.get_all(
		"Grievance RBAC Assignment",
		filters=_category_desk_filters(service_category_name, department_name, active_flag),
		fields=["name"],
		order_by="modified desc, name desc",
	)
	views = []
	for row in rows:
		desk = frappe.get_doc("Grievance RBAC Assignment", row.name)
		if not _is_category_desk(desk):
			continue
		if priority_name and (_sla_row(desk.category_scope).get("priority") or "Normal") != priority_name:
			continue
		views.append(_view(desk))
	total = len(views)
	window = views[(page_no - 1) * size : page_no * size]
	users = set()
	for view in window:
		users.add(view["l1_officer"])
		if view["l2_officer"]:
			users.add(view["l2_officer"])
	names = _user_names(users)
	total_pages = math.ceil(total / size) if total else 1
	return success_response(
		data={
			"assignments": [_record(view, names) for view in window],
			"pagination": {
				"page": page_no,
				"page_size": size,
				"total_count": total,
				"total_pages": total_pages,
				"has_next": page_no < total_pages,
				"has_prev": page_no > 1,
			},
		},
		message=_("Category assignments retrieved"),
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
		data={"assignment": _record(_view(_get_or_404(assignment)))},
		message=_("Category assignment retrieved"),
	)


@route("", methods=("POST",), summary="Create a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Create a category assignment",
	description="Create the category-only Grievance RBAC Assignment for one service category. One rule per category, including inactive rules.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def create_assignment(**kwargs):
	"""Create a category-only RBAC desk and the category SLA row."""
	state = _apply({}, CreateCategoryAssignment.model_validate(_body(kwargs)).model_dump())
	rules = _rules(state)
	_reject_duplicate(state["service_category"])
	desk = frappe.new_doc("Grievance RBAC Assignment")
	desk.effective_from = today()
	desk.category_scope = state["service_category"]
	desk.service_provider_scope = ""
	desk.assigned_by = frappe.session.user if frappe.session.user not in (None, "Guest") else "Administrator"
	_write_officers(desk, state, rules)
	desk.insert()
	_save_sla(state)
	desk.reload()
	return success_response(
		data={"assignment": _record(_view(desk))},
		message=_("Category assignment created"),
	)


@route("/<assignment>", methods=("PATCH",), summary="Update a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Update a category assignment",
	description="Change department, officers, priority, SLA window, or the auto-escalate, notify-on-submit, and active flags.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def update_assignment(assignment: str, **kwargs):
	"""Update a category-only RBAC desk. The service category is fixed once created."""
	desk = _get_or_404(assignment)
	changes = UpdateCategoryAssignment.model_validate(_body(kwargs)).model_dump(exclude_unset=True)
	if not changes:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	if "service_category" in changes:
		resolved = resolve_service_category(_present(changes, "service_category", "Service category"))
		if resolved != desk.category_scope:
			frappe.throw(
				_("Service category cannot be changed. Deactivate this assignment and create another."),
				frappe.ValidationError,
			)
		changes.pop("service_category")
	state = _apply(_state(desk), changes)
	rules = _rules(state)
	_write_officers(desk, state, rules)
	desk.save()
	_save_sla(state)
	desk.reload()
	return success_response(
		data={"assignment": _record(_view(desk))},
		message=_("Category assignment updated"),
	)


@route("/<assignment>", methods=("DELETE",), summary="Deactivate a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Deactivate a category assignment",
	description="Retire a category routing rule. The RBAC desk and its SLA row stay for audit and are marked inactive.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def deactivate_assignment(assignment: str):
	"""Deactivate a category-only RBAC desk and its SLA row. Repeating the call is a no-op."""
	desk = _get_or_404(assignment)
	if desk.active:
		desk.active = 0
		desk.save()
		state = _state(desk)
		state["active"] = 0
		_save_sla(state)
		desk.reload()
	return success_response(
		data={"assignment": _record(_view(desk))},
		message=_("Category assignment deactivated"),
	)
