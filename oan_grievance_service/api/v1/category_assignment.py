# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Category assignment CRUD for the Administration Category Assignments tab.

Design section 3.8 defines admin routing as list / create / update / deactivate
under `/api/v1/<resource>`. The catalogue's `rbac-assignments` resource is the
desk (category, type, provider, area, officer roster). This resource is the
one-row-per-category rule that tab edits: service category, department, L1, L2,
priority, SLA window, auto-escalate, and notify-on-submit. Saving projects the
desk and the SLA configuration; see GrievanceCategoryAssignment.
"""

import math

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import api_doc, handle_api_errors, require_role, success_response
from pydantic import BaseModel, ConfigDict, Field, field_validator

route = prefixed("/api/v1/category-assignments")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]
PRIORITIES = ("Low", "Normal", "High")
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

	@field_validator("l2_officer", "l1_officer", "service_category", "department", mode="before")
	@classmethod
	def _blank(cls, value):
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
	if not name or not frappe.db.exists("Grievance Category Assignment", name):
		frappe.throw(
			_("Category assignment '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	return frappe.get_doc("Grievance Category Assignment", name)


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
		"rbac_assignment": source.get("rbac_assignment") or None,
	}


def _apply(doc, changes: dict):
	if changes.get("service_category"):
		doc.service_category = resolve_service_category(changes["service_category"])
	if changes.get("department"):
		doc.department = resolve_department(changes["department"])
	if changes.get("l1_officer"):
		doc.l1_officer = resolve_officer(changes["l1_officer"])
	if "l2_officer" in changes:
		doc.l2_officer = resolve_officer(changes["l2_officer"])
	if changes.get("priority"):
		doc.priority = changes["priority"]
	if changes.get("sla_days") is not None:
		doc.sla_days = changes["sla_days"]
	for flag in ("auto_escalate", "notify_on_submit", "active"):
		if changes.get(flag) is not None:
			setattr(doc, flag, 1 if changes[flag] else 0)


def _reject_duplicate(service_category: str):
	existing = frappe.db.get_value(
		"Grievance Category Assignment",
		{"service_category": service_category},
		"name",
	)
	if existing:
		frappe.throw(
			_("A category assignment already exists for {0} ({1}).").format(service_category, existing),
			frappe.DuplicateEntryError,
		)


def _page(page, page_size) -> tuple[int, int]:
	try:
		page_no = int(page or 1)
		size = int(page_size or 20)
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
	page: int = 1,
	page_size: int = 20,
):
	"""List category assignments for the admin tab."""
	page_no, size = _page(page, page_size)
	filters = {}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	if department:
		filters["department"] = resolve_department(department)
	if priority:
		try:
			filters["priority"] = normalize_priority(priority)
		except ValueError as exc:
			frappe.throw(str(exc), frappe.ValidationError)
	if active not in (None, ""):
		try:
			filters["active"] = 1 if coerce_bool(active) else 0
		except ValueError as exc:
			frappe.throw(str(exc), frappe.ValidationError)

	total = frappe.db.count("Grievance Category Assignment", filters)
	rows = frappe.get_all(
		"Grievance Category Assignment",
		filters=filters,
		fields=[
			"name",
			"service_category",
			"department",
			"l1_officer",
			"l2_officer",
			"priority",
			"sla_days",
			"auto_escalate",
			"notify_on_submit",
			"active",
			"rbac_assignment",
		],
		order_by="modified desc",
		limit_start=(page_no - 1) * size,
		limit=size,
	)
	names = _user_names({row.l1_officer for row in rows} | {row.l2_officer for row in rows if row.l2_officer})
	total_pages = math.ceil(total / size) if total else 1
	return success_response(
		data={
			"assignments": [_record(row, names) for row in rows],
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
	description="One category routing rule, including the desk it projects onto.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def get_assignment(assignment: str):
	"""Return one category assignment."""
	return success_response(
		data={"assignment": _record(_get_or_404(assignment))},
		message=_("Category assignment retrieved"),
	)


@route("", methods=("POST",), summary="Create a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Create a category assignment",
	description="Create the routing rule for one service category. One active rule per category.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def create_assignment(**kwargs):
	"""Create a category assignment and project it onto routing and SLA."""
	body = CreateCategoryAssignment.model_validate(_body(kwargs))
	doc = frappe.new_doc("Grievance Category Assignment")
	_apply(doc, body.model_dump())
	_reject_duplicate(doc.service_category)
	doc.insert()
	doc.reload()
	return success_response(
		data={"assignment": _record(doc)},
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
	"""Update fields that were sent. The service category is fixed once created."""
	doc = _get_or_404(assignment)
	changes = UpdateCategoryAssignment.model_validate(_body(kwargs)).model_dump(exclude_unset=True)
	if not changes:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	if changes.get("service_category"):
		resolved = resolve_service_category(changes["service_category"])
		if resolved != doc.service_category:
			frappe.throw(
				_("Service category cannot be changed. Deactivate this assignment and create another."),
				frappe.ValidationError,
			)
		changes.pop("service_category")
	_apply(doc, changes)
	doc.save()
	doc.reload()
	return success_response(
		data={"assignment": _record(doc)},
		message=_("Category assignment updated"),
	)


@route("/<assignment>", methods=("DELETE",), summary="Deactivate a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@api_doc(
	summary="Deactivate a category assignment",
	description="Retire a routing rule. The record stays for audit and its desk and SLA row are marked inactive.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def deactivate_assignment(assignment: str):
	"""Deactivate a category assignment. Repeating the call is a no-op."""
	doc = _get_or_404(assignment)
	if doc.active:
		doc.active = 0
		doc.save()
		doc.reload()
	return success_response(
		data={"assignment": _record(doc)},
		message=_("Category assignment deactivated"),
	)
