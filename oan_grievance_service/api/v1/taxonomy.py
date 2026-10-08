# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Service category and grievance type CRUD for the Administration taxonomy tab.

Categories and types are the reference data the submission wizard, the category
assignments and the response templates all point at. They are never deleted: DELETE
retires a record by clearing its active flag, and every reader that offers or validates
against the flag follows. Retiring a category retires its types.

A category is identified by its name or its ticket code, a type by its id. A category's
ticket code is frozen once tickets have been issued under it, and a type cannot move to
another category.

Handlers stay thin. Field and link checks live in each doctype's `validate()`, and
cross-doctype work (rename, usage counts) in `services/taxonomy.py`.
"""

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
from oan_grievance_service.services import taxonomy as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES

category_route = prefixed("/api/v1/service-categories")
type_route = prefixed("/api/v1/grievance-types")

NAME_LENGTH = 140


# Service categories
# ------------------


class ServiceCategoryRecord(BaseModel):
	category_name: str
	code: str
	sort_order: int
	is_active: bool
	is_default: bool
	grievance_type_count: int
	assignment_count: int
	response_template_count: int
	grievance_count: int
	code_locked: bool


class ServiceCategoryData(BaseModel):
	service_category: ServiceCategoryRecord


class ServiceCategoryListData(BaseModel):
	service_categories: list[ServiceCategoryRecord]
	pagination: dict


class CategoryRef(Body):
	category: NonBlank


class CreateServiceCategory(Body):
	category_name: NonBlank = Field(max_length=NAME_LENGTH)
	code: NonBlank = Field(description="The 3-character CATEGORY segment of the ticket number")
	sort_order: int | None = Field(None, ge=0, description="Omit to place the category last")
	is_active: bool = True
	is_default: bool = Field(False, description="Make this the category unclassified cases fall to")


class UpdateServiceCategory(Body):
	"""Partial update. Omitted fields stay as they are."""

	category: NonBlank
	category_name: NonBlank = Field(None, max_length=NAME_LENGTH)
	code: NonBlank = None
	sort_order: int = Field(default=None, ge=0)
	is_active: bool = None
	is_default: bool = Field(default=None, description="true makes this the default. false is refused")


class ListServiceCategories(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	is_active: bool | None = None
	search: str | None = Field(None, description="Matches the name or code")

	_active_blank = field_validator("is_active", mode="before")(blank_to_none)


def _category_record(doc) -> dict:
	return service.category_records([doc])[0]


@category_route("", methods=("GET",), summary="List service categories")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListServiceCategories)
@api_doc(
	summary="List service categories",
	description="Admin list of service categories in display order, active and inactive, filterable by "
	+ "active flag and by text in the name or code. Each record counts what refers to it.",
	tags=["Administration"],
	response_model=ServiceCategoryListData,
)
def list_service_categories(
	is_active: bool | str | None = None,
	search: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List categories for the admin tab.

	Numeric and boolean parameters also accept str: frappe checks annotations before
	validate_request runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	rows, total = service.list_categories(
		is_active=is_active, search=search, start=params.start, page_size=params.page_size
	)
	return success_response(
		data={
			"service_categories": service.category_records(rows),
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Service categories retrieved"),
	)


@category_route("/<category>", methods=("GET",), summary="Get a service category")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(CategoryRef)
@api_doc(
	summary="Get a service category",
	description="One service category, by name or ticket code.",
	tags=["Administration"],
	response_model=ServiceCategoryData,
)
def get_service_category(category: str, **kwargs):
	"""Return one service category."""
	return success_response(
		data={"service_category": _category_record(service.get_category(category))},
		message=_("Service category retrieved"),
	)


@category_route("", methods=("POST",), summary="Create a service category")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateServiceCategory)
@api_doc(
	summary="Create a service category",
	description="Create a category with a name, a unique 3-character ticket code, a sort order and "
	+ "whether it is the default, the category unclassified cases fall to. Making it the default clears "
	+ "the flag on the previous default. "
	+ "The code uses the ticket alphabet, which excludes I, L, O and U. It cannot change once "
	+ "tickets have been issued under the category.",
	tags=["Administration"],
	response_model=ServiceCategoryData,
)
def create_service_category(
	category_name: str,
	code: str,
	sort_order: int | str | None = None,
	is_active: bool | str = True,
	is_default: bool | str = False,
	**kwargs,
):
	"""Create a service category."""
	doc = service.create_category(
		category_name=category_name,
		code=code,
		sort_order=sort_order,
		is_active=is_active,
		is_default=is_default,
	)
	return success_response(
		data={"service_category": _category_record(doc)}, message=_("Service category created")
	)


@category_route("/<category>", methods=("PATCH",), summary="Update a service category")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateServiceCategory, exclude_unset=True)
@api_doc(
	summary="Update a service category",
	description="Change the name, ticket code, sort order, active flag or default flag. Renaming keeps every "
	+ "grievance, routing rule, SLA and template linked to the category. The ticket code cannot "
	+ "change once tickets exist. Exactly one category is the default: send is_default true to make this "
	+ "one the default (the previous default loses the flag), and it cannot be deactivated while it "
	+ "is. "
	+ "Deactivating a category deactivates its types.",
	tags=["Administration"],
	response_model=ServiceCategoryData,
)
def update_service_category(category: str, **kwargs):
	"""Update a service category. `kwargs` holds only the fields the client sent."""
	doc = service.get_category(category)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc = service.update_category(doc, kwargs)
	return success_response(
		data={"service_category": _category_record(doc)}, message=_("Service category updated")
	)


@category_route("/<category>", methods=("DELETE",), summary="Deactivate a service category")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CategoryRef)
@api_doc(
	summary="Deactivate a service category",
	description="Retire a category and its types. They stay on the grievances filed under them and "
	+ "drop out of the submission dropdowns. Routing rules and templates already set up on the "
	+ "category are left as they are. The default category cannot be retired: make another "
	+ "category the default first. Same as PATCH with is_active false.",
	tags=["Administration"],
	response_model=ServiceCategoryData,
)
def deactivate_service_category(category: str, **kwargs):
	"""Deactivate a service category. Repeating the call is a no-op."""
	doc = service.deactivate_category(service.get_category(category))
	return success_response(
		data={"service_category": _category_record(doc)}, message=_("Service category deactivated")
	)


# Grievance types
# ---------------


class GrievanceTypeRecord(BaseModel):
	grievance_type_id: str
	type_name: str
	code: str
	service_category: str
	is_active: bool
	grievance_count: int


class GrievanceTypeData(BaseModel):
	grievance_type: GrievanceTypeRecord


class GrievanceTypeListData(BaseModel):
	grievance_types: list[GrievanceTypeRecord]
	pagination: dict


class TypeRef(Body):
	grievance_type: NonBlank


class CreateGrievanceType(Body):
	service_category: NonBlank = Field(description="Parent category, by name or ticket code")
	type_name: NonBlank = Field(max_length=NAME_LENGTH)
	code: NonBlank = Field(max_length=30, description="Unique within the category")
	is_active: bool = True


class UpdateGrievanceType(Body):
	"""Partial update. The category is fixed once the type is made."""

	grievance_type: NonBlank
	type_name: NonBlank = Field(None, max_length=NAME_LENGTH)
	code: NonBlank = Field(None, max_length=30)
	is_active: bool = None


class ListGrievanceTypes(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	is_active: bool | None = None
	search: str | None = Field(None, description="Matches the name or code")

	_active_blank = field_validator("is_active", mode="before")(blank_to_none)


def _type_record(doc) -> dict:
	return service.type_records([doc])[0]


@type_route("", methods=("GET",), summary="List grievance types")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListGrievanceTypes)
@api_doc(
	summary="List grievance types",
	description="Admin list of grievance types by name, active and inactive, filterable by service "
	+ "category, active flag and text in the name or code.",
	tags=["Administration"],
	response_model=GrievanceTypeListData,
)
def list_grievance_types(
	service_category: str | None = None,
	is_active: bool | str | None = None,
	search: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List grievance types for the admin tab."""
	params = PageParams(page=page, page_size=page_size)
	rows, total = service.list_types(
		service_category=service_category,
		is_active=is_active,
		search=search,
		start=params.start,
		page_size=params.page_size,
	)
	return success_response(
		data={
			"grievance_types": service.type_records(rows),
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Grievance types retrieved"),
	)


@type_route("/<grievance_type>", methods=("GET",), summary="Get a grievance type")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(TypeRef)
@api_doc(
	summary="Get a grievance type",
	description="One grievance type, by id.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def get_grievance_type(grievance_type: str, **kwargs):
	"""Return one grievance type."""
	return success_response(
		data={"grievance_type": _type_record(service.get_type(grievance_type))},
		message=_("Grievance type retrieved"),
	)


@type_route("", methods=("POST",), summary="Create a grievance type")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateGrievanceType)
@api_doc(
	summary="Create a grievance type",
	description="Create a type under an active service category. The name and the code are each "
	+ "unique within the category.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def create_grievance_type(
	service_category: str,
	type_name: str,
	code: str,
	is_active: bool | str = True,
	**kwargs,
):
	"""Create a grievance type under a category."""
	doc = service.create_type(
		service_category=service_category, type_name=type_name, code=code, is_active=is_active
	)
	return success_response(data={"grievance_type": _type_record(doc)}, message=_("Grievance type created"))


@type_route("/<grievance_type>", methods=("PATCH",), summary="Update a grievance type")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateGrievanceType, exclude_unset=True)
@api_doc(
	summary="Update a grievance type",
	description="Change the name, code or active flag. The service category cannot change. A type "
	+ "cannot be reactivated while its category is inactive, and the catch-all Other type of the "
	+ "default category cannot be deactivated.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def update_grievance_type(grievance_type: str, **kwargs):
	"""Update a grievance type. `kwargs` holds only the fields the client sent."""
	doc = service.get_type(grievance_type)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc = service.update_type(doc, kwargs)
	return success_response(data={"grievance_type": _type_record(doc)}, message=_("Grievance type updated"))


@type_route("/<grievance_type>", methods=("DELETE",), summary="Deactivate a grievance type")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(TypeRef)
@api_doc(
	summary="Deactivate a grievance type",
	description="Retire a type. It stays on the grievances filed under it and drops out of the "
	+ "submission dropdowns. Same as PATCH with is_active false.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def deactivate_grievance_type(grievance_type: str, **kwargs):
	"""Deactivate a grievance type. Repeating the call is a no-op."""
	doc = service.deactivate_type(service.get_type(grievance_type))
	return success_response(
		data={"grievance_type": _type_record(doc)}, message=_("Grievance type deactivated")
	)
