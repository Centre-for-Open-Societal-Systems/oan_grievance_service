# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Category assignment CRUD for the Administration Category Assignments tab.

A category assignment is a category-only Grievance RBAC Assignment: one desk per
(department, service category), with no area, type, or provider scope. Several
departments can serve the same category. SLA days and auto-escalate are stored on
the category's Grievance SLA Configuration, which is shared by every department
serving that category. The desk's own `active` flag is the only on/off switch, so
deactivating a desk never touches the SLA row. Area-aware desks are left alone and
still win when they are the nearer match. Desk officers are assigned standard role levels.

Handlers stay thin. Field and link checks live in the desk's `validate()`, and the
workflow (officer projection, SLA row) lives in `services/category_assignment.py`.
"""

from collections import defaultdict

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import category_assignment as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES
from oan_grievance_service.services.resolvers import resolve_department, resolve_service_category

route = prefixed("/api/v1/category-assignments")

DESK_FIELDS = ["name", "category_scope", "department_scope", "routing_strategy", "active"]


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
	pagination: dict


class AssignmentRef(Body):
	assignment: NonBlank


class CreateCategoryAssignment(Body):
	service_category: NonBlank
	department: NonBlank
	l1_officer: NonBlank
	l2_officer: NonBlank | None = None
	sla_days: int = Field(ge=1)
	auto_escalate: bool = True
	active: bool = True

	_l2_blank = field_validator("l2_officer", mode="before")(blank_to_none)


class UpdateCategoryAssignment(Body):
	"""Partial update. Omitted fields stay as they are, so only l2_officer accepts null."""

	assignment: NonBlank
	department: NonBlank = None
	l1_officer: NonBlank = None
	l2_officer: NonBlank | None = None
	sla_days: int = Field(default=None, ge=1)
	auto_escalate: bool = None
	active: bool = None

	_l2_blank = field_validator("l2_officer", mode="before")(blank_to_none)


class ListCategoryAssignments(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	department: str | None = None
	active: bool | None = None

	_active_blank = field_validator("active", mode="before")(blank_to_none)


def _records(desks: list) -> list[dict]:
	"""Project desk rows to API records with one query each for officers, SLA rows, and names."""
	if not desks:
		return []
	officers = defaultdict(list)
	for row in frappe.get_all(
		"Grievance RBAC Assignment Officer",
		filters={"parent": ["in", [desk.name for desk in desks]], "parenttype": service.DOCTYPE},
		fields=["parent", "user", "role_level", "is_primary", "active", "reports_to"],
		order_by="parent, idx",
	):
		officers[row.parent].append(row)
	sla_rows = service.active_sla_rows({desk.category_scope for desk in desks})
	splits = [service.split_officers(officers[desk.name]) for desk in desks]
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
	return _records(frappe.get_all(service.DOCTYPE, filters={"name": name}, fields=DESK_FIELDS))[0]


SLA_NOTE = (
	"The SLA window and auto-escalate flag belong to the service category, not to this rule. "
	"Departments that serve the same category share one SLA row, so changing them here "
	"changes them for every department's rule on that category."
)


@route("", methods=("GET",), summary="List category assignments")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListCategoryAssignments)
@api_doc(
	summary="List category assignments",
	description="Admin list of category-to-department routing rules, filterable by category, department, and active flag. "
	+ "sla_days and auto_escalate are the category's shared values.",
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
	filters = service.desk_filters()
	if service_category:
		filters["category_scope"] = resolve_service_category(service_category)
	if department:
		filters["department_scope"] = resolve_department(department)
	if active is not None:
		filters["active"] = 1 if active else 0
	desks = frappe.get_all(
		service.DOCTYPE,
		filters=filters,
		fields=DESK_FIELDS,
		order_by="modified desc, name desc",
		offset=params.start,
		limit_page_length=params.page_size,
	)
	return success_response(
		data={
			"assignments": _records(desks),
			"pagination": page_meta(frappe.db.count(service.DOCTYPE, filters), params.page, params.page_size),
		},
		message=_("Category assignments retrieved"),
	)


@route("/<assignment>", methods=("GET",), summary="Get a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(AssignmentRef)
@api_doc(
	summary="Get a category assignment",
	description="One category routing rule. The id is the category-only Grievance RBAC Assignment.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def get_assignment(assignment: str, **kwargs):
	"""Return one category-only RBAC assignment."""
	return success_response(
		data={"assignment": _record(service.get_desk(assignment).name)},
		message=_("Category assignment retrieved"),
	)


@route("", methods=("POST",), summary="Create a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateCategoryAssignment)
@api_doc(
	summary="Create a category assignment",
	description="Create the category-only Grievance RBAC Assignment for one department and service category. "
	+ "One rule per department and category, including inactive rules. "
	+ SLA_NOTE,
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
	desk = service.create(
		service_category=service_category,
		department=department,
		l1_officer=l1_officer,
		l2_officer=l2_officer,
		sla_days=sla_days,
		auto_escalate=auto_escalate,
		active=active,
	)
	return success_response(
		data={"assignment": _record(desk.name)},
		message=_("Category assignment created"),
	)


@route("/<assignment>", methods=("PATCH",), summary="Update a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateCategoryAssignment, exclude_unset=True)
@api_doc(
	summary="Update a category assignment",
	description="Change department, officers, SLA window, or the auto-escalate and active flags. "
	+ "The service category is fixed once created. "
	+ "Changing l1_officer or l2_officer replaces only that seat. Other officers on the desk, "
	+ "such as those added on the Nodal Officers tab, are kept. "
	+ SLA_NOTE,
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def update_assignment(assignment: str, **kwargs):
	"""Update a category-only RBAC desk. `kwargs` holds only the fields the client sent."""
	desk = service.get_desk(assignment)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(desk, kwargs)
	return success_response(
		data={"assignment": _record(desk.name)},
		message=_("Category assignment updated"),
	)


@route("/<assignment>", methods=("DELETE",), summary="Deactivate a category assignment")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(AssignmentRef)
@api_doc(
	summary="Deactivate a category assignment",
	description="Retire a category routing rule. The RBAC desk stays for audit and is marked inactive. The category's SLA row is not changed. Same as PATCH with active false.",
	tags=["Administration"],
	response_model=CategoryAssignmentData,
)
def deactivate_assignment(assignment: str, **kwargs):
	"""Deactivate a category-only RBAC desk. Repeating the call is a no-op."""
	desk = service.get_desk(assignment)
	service.update(desk, {"active": False})
	return success_response(
		data={"assignment": _record(desk.name)},
		message=_("Category assignment deactivated"),
	)
