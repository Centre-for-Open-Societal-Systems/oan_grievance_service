# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Department CRUD for the Administration Departments screen.

A department is the unit a grievance is routed to. Its record is the write side of the
read-only `departments` list in `GET /api/v1/grievances/options`: `department_id`,
`department_name` and `email_account` carry the same values there, and this resource adds
the department head. The department id is its name and is fixed once created.

The head (`head_of_dept`) is who the `Department Head` recipient role reaches in SLA-breach
escalation notifications when no officer desk covering the case holds the department head
level. Assign or reassign it with PATCH; send null to clear it.

Departments are not deleted: DELETE retires one by clearing `active`, and is refused while
it has open cases. PATCH with `active` false is the same thing.

Handlers stay thin. Field and link checks live in the doctype's `validate()`, and the
workflow (open-case guard, short-name uniqueness) lives in `services/department.py`.
"""

from typing import Literal

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
	validate_email_string,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import department as service

route = prefixed("/api/v1/departments")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]
RoutingStrategy = Literal["Primary First", "Round Robin", "Least Loaded"]
# A department id is a document name, which Frappe caps at 140 characters.
NAME_MAX_LENGTH = 140


def normalize_email(value: str) -> str:
	"""The department mailbox is validated and stored lowercase."""
	return validate_email_string(value.strip()).lower()


class DepartmentRecord(BaseModel):
	department_id: str
	department_name: str
	short_name: str | None = None
	email_account: str
	phone: str | None = None
	head_of_dept: str | None = None
	head_of_dept_name: str | None = None
	active: bool
	l1_role_level: str | None = None
	l2_role_level: str | None = None
	routing_strategy: RoutingStrategy | None = None


class DepartmentData(BaseModel):
	department: DepartmentRecord


class DepartmentListData(BaseModel):
	departments: list[DepartmentRecord]


class DepartmentRef(Body):
	department: NonBlank


class CreateDepartment(Body):
	department_name: NonBlank = Field(max_length=NAME_MAX_LENGTH)
	email_account: NonBlank
	short_name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
	phone: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
	head_of_dept: str | None = None
	active: bool = True
	l1_role_level: str | None = None
	l2_role_level: str | None = None
	routing_strategy: RoutingStrategy | None = None

	_email = field_validator("email_account")(normalize_email)
	_blank = field_validator(
		"short_name",
		"phone",
		"head_of_dept",
		"l1_role_level",
		"l2_role_level",
		"routing_strategy",
		mode="before",
	)(blank_to_none)


class UpdateDepartment(Body):
	"""Partial update. Omitted fields stay as they are. `department_name` is fixed once created."""

	department: NonBlank
	email_account: NonBlank = None
	short_name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
	phone: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
	head_of_dept: str | None = None
	active: bool = None
	l1_role_level: str | None = None
	l2_role_level: str | None = None
	routing_strategy: RoutingStrategy | None = None

	_email = field_validator("email_account")(normalize_email)
	_blank = field_validator(
		"short_name",
		"phone",
		"head_of_dept",
		"l1_role_level",
		"l2_role_level",
		"routing_strategy",
		mode="before",
	)(blank_to_none)


class ListDepartments(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	active: bool | None = None
	head_of_dept: str | None = None
	q: str | None = None

	_blank = field_validator("active", "head_of_dept", "q", mode="before")(blank_to_none)


@route("", methods=("GET",), summary="List departments")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListDepartments)
@api_doc(
	summary="List departments",
	description="Admin list of departments by name, filterable by active flag and head of department. "
	+ "q matches name, short name or email. Unlike `GET /api/v1/grievances/options`, which offers "
	+ "active departments only, this list includes retired ones.",
	tags=["Administration"],
	response_model=DepartmentListData,
)
def list_departments(
	active: bool | str | None = None,
	head_of_dept: str | None = None,
	q: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List departments by name, filterable by active flag and head of department.

	q matches name, short name or email. Retired departments are included, unlike the
	departments offered by GET /api/v1/grievances/options.
	"""
	# Numeric and boolean parameters also accept str: frappe checks annotations before
	# validate_request runs, and a bare int would turn a bad value into its own type error.
	params = PageParams(page=page, page_size=page_size)
	names, total = service.list_departments(
		active=active,
		head_of_dept=head_of_dept,
		q=q,
		start=params.start,
		page_size=params.page_size,
	)
	return success_response(
		data={"departments": service.records(names)},
		message=_("Departments retrieved"),
		pagination=page_meta(total, params.page, params.page_size),
	)


@route("/<department>", methods=("GET",), summary="Get a department")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(DepartmentRef)
@api_doc(
	summary="Get a department",
	description="One department with its notification email, phone, head of department and routing "
	+ "preferences. The id is the department name.",
	tags=["Administration"],
	response_model=DepartmentData,
)
def get_department(department: str, **kwargs):
	"""Return one department with its notification email, phone, head and routing preferences.

	The id is the department name.
	"""
	return success_response(
		data={"department": service.record(service.get_department(department).name)},
		message=_("Department retrieved"),
	)


@route("", methods=("POST",), summary="Create a department")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateDepartment)
@api_doc(
	summary="Create a department",
	description="Create a department with the email account it is notified at and, optionally, its "
	+ "head. The name must be new and becomes the department id. The head must be an enabled "
	+ "Grievance Officer or Grievance Admin. A department needs l1_role_level (and l2_role_level "
	+ "for a senior officer) before a category assignment can name it.",
	tags=["Administration"],
	response_model=DepartmentData,
)
def create_department(
	department_name: str,
	email_account: str,
	short_name: str | None = None,
	phone: str | None = None,
	head_of_dept: str | None = None,
	active: bool | str = True,
	l1_role_level: str | None = None,
	l2_role_level: str | None = None,
	routing_strategy: str | None = None,
	**kwargs,
):
	"""Create a department with the email account it is notified at and, optionally, its head.

	The name must be new and becomes the department id. The head must be an enabled Grievance
	Officer or Grievance Admin. A department needs l1_role_level (and l2_role_level for a
	senior officer) before a category assignment can name it.
	"""
	name = service.create(
		department_name=department_name,
		email_account=email_account,
		short_name=short_name,
		phone=phone,
		head_of_dept=head_of_dept,
		active=active,
		l1_role_level=l1_role_level,
		l2_role_level=l2_role_level,
		routing_strategy=routing_strategy,
	)
	return success_response(data={"department": service.record(name)}, message=_("Department created"))


@route("/<department>", methods=("PATCH",), summary="Update a department")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateDepartment, exclude_unset=True)
@api_doc(
	summary="Update a department",
	description="Change the email account, short name, phone, head of department, routing preferences "
	+ "or active flag. Send head_of_dept to assign or reassign the head, or null to clear it. "
	+ "Setting active to false retires the department and is refused while it has open cases. "
	+ "department_name is fixed once created.",
	tags=["Administration"],
	response_model=DepartmentData,
)
def update_department(department: str, **kwargs):
	"""Change the email account, short name, phone, head, routing preferences or active flag.

	Send head_of_dept to assign or reassign the head, or null to clear it. Setting active to
	false retires the department and is refused while it has open cases. department_name is
	fixed once created. Only the fields the client sent are changed.
	"""
	doc = service.get_department(department)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(doc, kwargs)
	return success_response(data={"department": service.record(doc.name)}, message=_("Department updated"))


@route("/<department>", methods=("DELETE",), summary="Retire a department")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(DepartmentRef)
@api_doc(
	summary="Retire a department",
	description="Retire a department. The record stays for the cases, assignments and templates that "
	+ "link to it and is marked inactive, so it drops out of `GET /api/v1/grievances/options`. "
	+ "Refused while the department has open cases. Same as PATCH with active false.",
	tags=["Administration"],
	response_model=DepartmentData,
)
def deactivate_department(department: str, **kwargs):
	"""Retire a department: it stays on record and is marked inactive.

	It drops out of the departments offered by GET /api/v1/grievances/options. Refused while
	the department has open cases. Same as PATCH with active false. Repeating the call is a no-op.
	"""
	doc = service.get_department(department)
	service.deactivate(doc)
	return success_response(
		data={"department": service.record(doc.name)}, message=_("Department deactivated")
	)
