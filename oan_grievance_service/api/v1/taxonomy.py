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

Handlers stay thin. The rules live in the doctype controllers (`validate`, `on_update`),
so they hold for the Desk as well as the API. What is here is request shape, the list
queries and the usage counts each record carries.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_permission,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.grievance_masters.doctype.grievance_service_category.grievance_service_category import (
	get_category,
	pending_rename,
	queue_rename,
)
from oan_grievance_service.grievance_sla.doctype.grievance_sla_configuration.grievance_sla_configuration import (
	save_sla_configuration,
)
from oan_grievance_service.permissions import assert_heads_category
from oan_grievance_service.services import category_assignment
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ROLE_DESK_HEAD
from oan_grievance_service.services.resolvers import new_or_resolved_department, resolve_service_category

route = prefixed("/api/v1")

CATEGORY = "Grievance Service Category"
DESK_HEAD_READ_ROLES = [*ADMIN_READ_ROLES, ROLE_DESK_HEAD]
TYPE = "Grievance Type"

NAME_LENGTH = 140

# The only fields a PATCH may write. The request schema already forbids anything else; this
# keeps that true if a schema ever grows a field the document should not take from a client.
CATEGORY_EDITABLE = ("category_name", "code", "sort_order", "is_active", "is_default")
TYPE_EDITABLE = ("type_name", "is_active")


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
	has_grievances: bool
	code_locked: bool
	renaming_to: str | None = None


class ServiceCategoryData(BaseModel):
	service_category: ServiceCategoryRecord


class GrievanceTypeRecord(BaseModel):
	grievance_type_id: str
	type_name: str
	service_category: str
	is_active: bool
	has_grievances: bool


class ServiceCategoryDetail(ServiceCategoryRecord):
	departments: list[str | None]
	sla_days: int | None = None
	auto_escalate: bool | None = None
	grievance_types: list[GrievanceTypeRecord]


class ServiceCategoryDetailData(BaseModel):
	service_category: ServiceCategoryDetail


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
	sla_days: int | None = Field(None, ge=1, description="Resolution deadline in working days")
	auto_escalate: bool = True
	notify_on_breach: bool = True
	department: NonBlank | None = Field(
		None, description="Also create the category assignment (RBAC desk) for this department"
	)
	create_department: bool = Field(
		False, description="Create the department; refused when it already exists"
	)

	_department = field_validator("department", mode="before")(blank_to_none)


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


def _category_records(docs: list) -> list[dict]:
	"""Project categories to API records, with one grouped query per count for the whole page."""
	names = [doc.name for doc in docs]
	types = _counts(TYPE, "service_category", names, is_active=1)
	assignments = _counts("Grievance RBAC Assignment", "category_scope", names, active=1)
	templates = _counts("Grievance Response Template", "service_category", names, is_active=1)
	grievances = _used("Grievance", "service_category", names)
	ticketed = _used("Grievance", "service_category", names, ticket_number=["is", "set"])
	return [
		{
			"category_name": doc.category_name,
			"code": doc.code,
			"sort_order": doc.sort_order or 0,
			"is_active": bool(doc.is_active),
			"is_default": bool(doc.is_default),
			"grievance_type_count": types.get(doc.name, 0),
			"assignment_count": assignments.get(doc.name, 0),
			"response_template_count": templates.get(doc.name, 0),
			"has_grievances": doc.name in grievances,
			"code_locked": doc.name in ticketed,
			"renaming_to": pending_rename(doc.name),
		}
		for doc in docs
	]


@route("/service-categories", methods=("GET",), summary="List service categories", tier="officer-core")
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
	filters = {} if is_active is None else {"is_active": 1 if is_active else 0}
	or_filters = _search(CATEGORY, search, ("category_name", "code"))
	docs = frappe.get_all(
		CATEGORY,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "category_name", "code", "sort_order", "is_active", "is_default"],
		order_by="sort_order asc, category_name asc",
		start=params.start,
		limit=params.page_size,
	)
	return success_response(
		data={
			"service_categories": _category_records(docs),
			"pagination": page_meta(_count(CATEGORY, filters, or_filters), params.page, params.page_size),
		},
		message=_("Service categories retrieved"),
	)


@route(
	"/service-categories/<category>", methods=("GET",), summary="Get a service category", tier="officer-core"
)
@handle_api_errors
@require_role(DESK_HEAD_READ_ROLES)
@validate_request(CategoryRef)
@api_doc(
	summary="Get a service category",
	description="One service category, by name or ticket code, with the departments that have a "
	+ "category assignment for it (null is every department), its SLA and its grievance types. "
	+ "A Desk Head reads only the categories they head.",
	tags=["Administration"],
	response_model=ServiceCategoryDetailData,
)
def get_service_category(category: str, **kwargs):
	"""Return one service category with its departments, SLA and grievance types."""
	doc = get_category(category)
	user = frappe.session.user
	if not set(frappe.get_roles(user)) & set(ADMIN_READ_ROLES):
		assert_heads_category(user, doc.name)
	record = _category_records([doc])[0]
	sla = category_assignment.active_sla_rows([doc.name]).get(doc.name)
	record.update(
		{
			"departments": sorted(
				{
					desk.department_scope
					for desk in frappe.get_all(
						"Grievance RBAC Assignment",
						filters=category_assignment.desk_filters(category_scope=doc.name, active=1),
						fields=["department_scope"],
					)
				},
				key=lambda dept: (dept is not None, dept or ""),
			),
			"sla_days": sla.sla_days if sla else None,
			"auto_escalate": bool(sla.auto_escalate) if sla else None,
			"grievance_types": _type_records(
				frappe.get_all(
					TYPE,
					filters={"service_category": doc.name},
					fields=["name", "type_name", "service_category", "is_active"],
					order_by="type_name asc, name asc",
				)
			),
		}
	)
	return success_response(data={"service_category": record}, message=_("Service category retrieved"))


@route("/service-categories", methods=("POST",), summary="Create a service category", tier="officer-core")
@handle_api_errors
@require_permission(CATEGORY, "create")
@validate_request(CreateServiceCategory)
@api_doc(
	summary="Create a service category",
	description="Create a category with a name, a unique 3-character ticket code, a sort order and "
	+ "whether it is the default, the category unclassified cases fall to. Making it the default clears "
	+ "the flag on the previous default. "
	+ "The code uses the ticket alphabet, which excludes I, L, O and U. It cannot change once "
	+ "tickets have been issued under the category. "
	+ "Optional sla_days (default 7), auto_escalate, and notify_on_breach establish the category's SLA configuration atomically. "
	+ "Optional department also creates the category assignment for that department, with no officers yet; "
	+ "with create_department the department is created too, refused when it already exists.",
	tags=["Administration"],
	response_model=ServiceCategoryData,
)
def create_service_category(
	category_name: str,
	code: str,
	sort_order: int | str | None = None,
	is_active: bool | str = True,
	is_default: bool | str = False,
	sla_days: int | None = None,
	auto_escalate: bool = True,
	notify_on_breach: bool = True,
	department: str | None = None,
	create_department: bool = False,
	**kwargs,
):
	"""Create a service category."""
	if sort_order is None:
		top = frappe.get_all(CATEGORY, fields=[{"MAX": "sort_order", "as": "top"}])
		sort_order = (top[0].top or 0) + 1
	doc = frappe.get_doc(
		{
			"doctype": CATEGORY,
			"category_name": category_name,
			"code": code,
			"sort_order": sort_order,
			"is_active": 1 if is_active else 0,
			"is_default": 1 if is_default else 0,
		}
	).insert()
	save_sla_configuration(doc.name, sla_days or C.DEFAULT_SLA_DAYS, auto_escalate, notify_on_breach)
	if department:
		category_assignment.create(
			service_category=doc.name, department=new_or_resolved_department(department, create_department)
		)
	return success_response(
		data={"service_category": _category_records([doc])[0]}, message=_("Service category created")
	)


@route(
	"/service-categories/<category>",
	methods=("PATCH",),
	summary="Update a service category",
	tier="officer-core",
)
@handle_api_errors
@require_permission(CATEGORY, "write")
@validate_request(UpdateServiceCategory, exclude_unset=True)
@api_doc(
	summary="Update a service category",
	description="Change the name, ticket code, sort order, active flag or default flag. Renaming keeps every "
	+ "grievance, routing rule, SLA and template linked to the category. The ticket code cannot "
	+ "change once tickets exist. Exactly one category is the default: send is_default true to make this "
	+ "one the default (the previous default loses the flag), and it cannot be deactivated while it "
	+ "is. Deactivating a category deactivates its types.",
	tags=["Administration"],
	response_model=ServiceCategoryData,
)
def update_service_category(category: str, **kwargs):
	"""Update a service category. `kwargs` holds only the fields the client sent."""
	doc = get_category(category)
	changes = _editable(kwargs, CATEGORY_EDITABLE)
	new_name = changes.pop("category_name", None)
	if new_name is None and not changes:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update(changes)
	doc.save()
	if new_name and new_name != doc.name:
		# Every link to the category has to follow, which can take a while on a busy site, so the
		# move is a background job. The record keeps its old name and shows `renaming_to` until
		# the job is done. Where there is no worker to wait for, it has already finished.
		queue_rename(doc, new_name)
		if not frappe.db.exists(CATEGORY, doc.name):
			doc = frappe.get_doc(CATEGORY, new_name)
	message = (
		_("Service category updated. Renaming is in progress.")
		if pending_rename(doc.name)
		else _("Service category updated")
	)
	return success_response(data={"service_category": _category_records([doc])[0]}, message=message)


@route(
	"/service-categories/<category>",
	methods=("DELETE",),
	summary="Deactivate a service category",
	tier="officer-core",
)
@handle_api_errors
@require_permission(CATEGORY, "write")
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
	doc = get_category(category)
	if doc.is_active:
		doc.is_active = 0
		doc.save()
	return success_response(
		data={"service_category": _category_records([doc])[0]}, message=_("Service category deactivated")
	)


# Grievance types
# ---------------


class GrievanceTypeData(BaseModel):
	grievance_type: GrievanceTypeRecord


class GrievanceTypeListData(BaseModel):
	grievance_types: list[GrievanceTypeRecord]
	pagination: dict


class TypeRef(Body):
	grievance_type: NonBlank


class CreateGrievanceType(Body):
	service_category: NonBlank = Field(description="Parent category, by name or ticket code")
	type_name: NonBlank = Field(max_length=NAME_LENGTH, description="Unique within the category")
	is_active: bool = True


class UpdateGrievanceType(Body):
	"""Partial update. The category is fixed once the type is made."""

	grievance_type: NonBlank
	type_name: NonBlank = Field(None, max_length=NAME_LENGTH)
	is_active: bool = None


class ListGrievanceTypes(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	is_active: bool | None = None
	search: str | None = Field(None, description="Matches the type name")

	_active_blank = field_validator("is_active", mode="before")(blank_to_none)


def _type_records(docs: list) -> list[dict]:
	"""Project types to API records, with one grouped query for the whole page's usage."""
	grievances = _used("Grievance", "grievance_type", [doc.name for doc in docs])
	return [
		{
			"grievance_type_id": doc.name,
			"type_name": doc.type_name,
			"service_category": doc.service_category,
			"is_active": bool(doc.is_active),
			"has_grievances": doc.name in grievances,
		}
		for doc in docs
	]


@route("/grievance-types", methods=("GET",), summary="List grievance types", tier="officer-core")
@handle_api_errors
@require_role(DESK_HEAD_READ_ROLES)
@validate_request(ListGrievanceTypes)
@api_doc(
	summary="List grievance types",
	description="Admin list of grievance types by category and name, active and inactive, filterable by "
	+ "service category, active flag and text in the name.",
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
	filters = {} if is_active is None else {"is_active": 1 if is_active else 0}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	or_filters = _search(TYPE, search, ("type_name",))
	docs = frappe.get_all(
		TYPE,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "type_name", "service_category", "is_active"],
		# Name alone is not unique across categories, so the id breaks ties and pages never overlap.
		order_by="service_category asc, type_name asc, name asc",
		start=params.start,
		limit=params.page_size,
	)
	return success_response(
		data={
			"grievance_types": _type_records(docs),
			"pagination": page_meta(_count(TYPE, filters, or_filters), params.page, params.page_size),
		},
		message=_("Grievance types retrieved"),
	)


@route(
	"/grievance-types/<grievance_type>", methods=("GET",), summary="Get a grievance type", tier="officer-core"
)
@handle_api_errors
@require_role(DESK_HEAD_READ_ROLES)
@validate_request(TypeRef)
@api_doc(
	summary="Get a grievance type",
	description="One grievance type, by id.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def get_grievance_type(grievance_type: str, **kwargs):
	"""Return one grievance type. An unknown id is a 404."""
	return success_response(
		data={"grievance_type": _type_records([frappe.get_doc(TYPE, grievance_type)])[0]},
		message=_("Grievance type retrieved"),
	)


@route("/grievance-types", methods=("POST",), summary="Create a grievance type", tier="officer-core")
@handle_api_errors
@require_permission(TYPE, "create")
@validate_request(CreateGrievanceType)
@api_doc(
	summary="Create a grievance type",
	description="Create a type under an active service category. The name is unique within the "
	+ "category.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def create_grievance_type(
	service_category: str,
	type_name: str,
	is_active: bool | str = True,
	**kwargs,
):
	"""Create a grievance type under a category."""
	category = resolve_service_category(service_category)
	assert_heads_category(frappe.session.user, category)
	doc = frappe.get_doc(
		{
			"doctype": TYPE,
			"service_category": category,
			"type_name": type_name,
			"is_active": 1 if is_active else 0,
		}
	).insert()
	return success_response(
		data={"grievance_type": _type_records([doc])[0]}, message=_("Grievance type created")
	)


@route(
	"/grievance-types/<grievance_type>",
	methods=("PATCH",),
	summary="Update a grievance type",
	tier="officer-core",
)
@handle_api_errors
@require_permission(TYPE, "write")
@validate_request(UpdateGrievanceType, exclude_unset=True)
@api_doc(
	summary="Update a grievance type",
	description="Change the name or active flag. The service category cannot change. A type "
	+ "cannot be reactivated while its category is inactive, and the catch-all Other type of the "
	+ "default category cannot be deactivated.",
	tags=["Administration"],
	response_model=GrievanceTypeData,
)
def update_grievance_type(grievance_type: str, **kwargs):
	"""Update a grievance type. `kwargs` holds only the fields the client sent."""
	doc = frappe.get_doc(TYPE, grievance_type)
	assert_heads_category(frappe.session.user, doc.service_category)
	changes = _editable(kwargs, TYPE_EDITABLE)
	if not changes:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update(changes)
	doc.save()
	return success_response(
		data={"grievance_type": _type_records([doc])[0]}, message=_("Grievance type updated")
	)


@route(
	"/grievance-types/<grievance_type>",
	methods=("DELETE",),
	summary="Deactivate a grievance type",
	tier="officer-core",
)
@handle_api_errors
@require_permission(TYPE, "write")
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
	doc = frappe.get_doc(TYPE, grievance_type)
	assert_heads_category(frappe.session.user, doc.service_category)
	if doc.is_active:
		doc.is_active = 0
		doc.save()
	return success_response(
		data={"grievance_type": _type_records([doc])[0]}, message=_("Grievance type deactivated")
	)


# Helpers
# -------


def _editable(sent: dict, allowed: tuple[str, ...]) -> dict:
	"""The sent fields a PATCH may write, with flags as the 0/1 a Check field stores."""
	return {
		fieldname: int(value) if isinstance(value, bool) else value
		for fieldname, value in sent.items()
		if fieldname in allowed
	}


def _search(doctype: str, text: str | None, fieldnames: tuple[str, ...]) -> list | None:
	"""Match the text in any of the fields, or None when there is no text."""
	text = (text or "").strip()
	if not text:
		return None
	return [[doctype, fieldname, "like", f"%{text}%"] for fieldname in fieldnames]


def _count(doctype: str, filters: dict, or_filters: list | None) -> int:
	rows = frappe.get_all(
		doctype, filters=filters, or_filters=or_filters, fields=[{"COUNT": "*", "as": "total"}]
	)
	return int(rows[0].total) if rows else 0


def _used(doctype: str, fieldname: str, names: list[str], **filters) -> set[str]:
	"""Which of `names` some row refers to.

	One lookup that stops at the first match for each name, so what it costs follows the page and
	not the size of the table. A count of grievances per category would read every one of them.
	"""
	return {name for name in names if frappe.db.exists(doctype, {fieldname: name, **filters})}


def _counts(doctype: str, fieldname: str, names: list[str], **filters) -> dict[str, int]:
	"""Rows per value of `fieldname` among `names`, in one grouped query."""
	if not names:
		return {}
	rows = frappe.get_all(
		doctype,
		filters={fieldname: ["in", names], **filters},
		fields=[fieldname, {"COUNT": "*", "as": "total"}],
		group_by=fieldname,
	)
	return {row[fieldname]: int(row.total) for row in rows}
