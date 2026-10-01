# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Response template CRUD for the Administration Response Templates tab (STG-405).

A response template is the pre-written action-taken and resolution-summary wording an
officer starts from when filing a formal response, scoped by service category, an optional
subcategory (grievance type), and response type. Wording may carry `{{ name }}` placeholders.

Editing is versioned: PATCH never overwrites the old wording. It is copied to the template's
history first and `version` goes up by one. GET one template returns that history, newest
first. DELETE removes a template that was never used and only deactivates one that was.

Handlers stay thin. Field and link checks live in the doctype's `validate()` and the
versioning in `services/response_template.py`.
"""

from typing import Annotated, Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, StringConstraints, field_validator

from oan_grievance_service.api.v1._pagination import PageParams, page_meta
from oan_grievance_service.api.v1._schemas import Body, NonBlank, PartialBody, blank_to_none
from oan_grievance_service.grievance_management.doctype.grievance_response.grievance_response import (
	ACTION_TAKEN_LIMIT,
)
from oan_grievance_service.services import response_template as service

route = prefixed("/api/v1/response-templates")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]

TEMPLATE_FIELDS = [
	"name",
	"title",
	"service_category",
	"grievance_type",
	"response_type",
	"action_taken",
	"resolution_summary",
	"version",
	"is_active",
	"creation",
	"modified",
	"modified_by",
]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=140)]
ActionTaken = Annotated[
	str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ACTION_TAKEN_LIMIT)
]
ResolutionSummary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
ChangeNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
# Spelled out so the OpenAPI schema and pydantic see the same literal. A test pins it to
# `service.RESPONSE_TYPES`.
ResponseType = Literal["Resolved", "Partially Resolved", "Referred to another dept", "Requires further info"]


def _iso(value) -> str | None:
	if not value:
		return None
	return value.isoformat() if hasattr(value, "isoformat") else str(value)


class TemplateVersionRecord(BaseModel):
	version: int
	title: str | None = None
	response_type: str | None = None
	service_category: str | None = None
	grievance_type: str | None = None
	action_taken: str | None = None
	resolution_summary: str | None = None
	replaced_on: str | None = None
	replaced_by: str | None = None
	change_note: str | None = None


class TemplateRecord(BaseModel):
	id: str
	title: str
	service_category: str
	service_category_name: str | None = None
	grievance_type: str | None = None
	grievance_type_name: str | None = None
	response_type: str
	action_taken: str
	resolution_summary: str
	placeholders: list[str]
	version: int
	is_active: bool
	use_count: int
	last_used_on: str | None = None
	created_on: str | None = None
	modified_on: str | None = None
	modified_by: str | None = None


class TemplateDetailRecord(TemplateRecord):
	versions: list[TemplateVersionRecord]


class TemplateData(BaseModel):
	template: TemplateDetailRecord


class TemplateListData(BaseModel):
	templates: list[TemplateRecord]
	pagination: dict


class TemplateDeleteData(BaseModel):
	template: TemplateDetailRecord
	deleted: bool


class TemplateRef(Body):
	template: NonBlank


class CreateTemplate(Body):
	title: Title
	service_category: NonBlank
	grievance_type: NonBlank | None = None
	response_type: ResponseType
	action_taken: ActionTaken
	resolution_summary: ResolutionSummary
	is_active: bool = True

	_type_blank = field_validator("grievance_type", mode="before")(blank_to_none)


class UpdateTemplate(PartialBody):
	"""Partial update. Omitted fields stay as they are, so only grievance_type accepts null."""

	template: NonBlank
	title: Title = None
	service_category: NonBlank = None
	grievance_type: NonBlank | None = None
	response_type: ResponseType = None
	action_taken: ActionTaken = None
	resolution_summary: ResolutionSummary = None
	is_active: bool = None
	expected_version: int = Field(default=None, ge=1)
	change_note: ChangeNote | None = None

	_type_blank = field_validator("grievance_type", mode="before")(blank_to_none)


class ListTemplates(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	grievance_type: str | None = None
	response_type: ResponseType | None = None
	is_active: bool | None = None
	q: str | None = None

	_blank = field_validator(
		"service_category", "grievance_type", "response_type", "is_active", "q", mode="before"
	)(blank_to_none)


def _record(row, categories: dict, types: dict, used: dict) -> dict:
	"""Project a template row to the API record."""
	return {
		"id": row.name,
		"title": row.title,
		"service_category": row.service_category,
		"service_category_name": categories.get(row.service_category),
		"grievance_type": row.grievance_type or None,
		"grievance_type_name": types.get(row.grievance_type),
		"response_type": row.response_type,
		"action_taken": row.action_taken or "",
		"resolution_summary": row.resolution_summary or "",
		"placeholders": service.extract_placeholders(row.action_taken, row.resolution_summary),
		"version": row.version or 1,
		"is_active": bool(row.is_active),
		"use_count": used[row.name].use_count if row.name in used else 0,
		"last_used_on": _iso(used[row.name].last_used_on) if row.name in used else None,
		"created_on": _iso(row.creation),
		"modified_on": _iso(row.modified),
		"modified_by": row.modified_by,
	}


def _records(rows: list) -> list[dict]:
	"""Project rows with one query each for category names, type names, and usage."""
	if not rows:
		return []
	categories = {
		row.name: row.category_name
		for row in frappe.get_all(
			"Grievance Service Category",
			filters={"name": ["in", list({row.service_category for row in rows if row.service_category})]},
			fields=["name", "category_name"],
		)
	}
	types = {
		row.name: row.type_name
		for row in frappe.get_all(
			"Grievance Type",
			filters={"name": ["in", list({row.grievance_type for row in rows if row.grievance_type})]},
			fields=["name", "type_name"],
		)
	}
	used = service.usage([row.name for row in rows])
	return [_record(row, categories, types, used) for row in rows]


def _detail(doc) -> dict:
	"""One template with its history, newest version first."""
	record = _records([doc])[0]
	record["versions"] = [
		{
			"version": row.version,
			"title": row.title,
			"response_type": row.response_type,
			"service_category": row.service_category,
			"grievance_type": row.grievance_type,
			"action_taken": row.action_taken,
			"resolution_summary": row.resolution_summary,
			"replaced_on": _iso(row.retired_on),
			"replaced_by": row.retired_by,
			"change_note": row.change_note,
		}
		for row in sorted(doc.versions, key=lambda row: row.version, reverse=True)
	]
	return record


@route("", methods=("GET",), summary="List response templates")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListTemplates)
@api_doc(
	summary="List response templates",
	description="Admin list of response templates, newest edit first. Filter by service category, "
	+ "subcategory (grievance type), response type, active flag, or a title search. "
	+ "Each row carries its current version, use count, and last-used time. "
	+ "History is on the single-template route.",
	tags=["Response Templates"],
	response_model=TemplateListData,
)
def list_templates(
	service_category: str | None = None,
	grievance_type: str | None = None,
	response_type: str | None = None,
	is_active: bool | str | None = None,
	q: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List templates, filtered and paged in SQL.

	Numeric and boolean parameters also accept str: frappe checks annotations before
	validate_request runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	filters: dict = {}
	if service_category:
		filters["service_category"] = service.resolve_service_category(service_category)
	if grievance_type:
		filters["grievance_type"] = service.resolve_type(grievance_type, filters.get("service_category"))
	if response_type:
		filters["response_type"] = response_type
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0
	if q:
		escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
		filters["title"] = ["like", f"%{escaped}%"]
	rows = frappe.get_all(
		service.DOCTYPE,
		filters=filters,
		fields=TEMPLATE_FIELDS,
		order_by="modified desc, name desc",
		offset=params.start,
		limit=params.page_size,
	)
	return success_response(
		data={
			"templates": _records(rows),
			"pagination": page_meta(params, frappe.db.count(service.DOCTYPE, filters)),
		},
		message=_("Response templates retrieved"),
	)


@route("/<template>", methods=("GET",), summary="Get a response template with its history")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(TemplateRef)
@api_doc(
	summary="Get a response template with its history",
	description="One template and every earlier version of its wording, newest first. "
	+ "The current wording is on the template itself.",
	tags=["Response Templates"],
	response_model=TemplateData,
)
def get_template(template: str, **kwargs):
	"""Return one template with its version history."""
	return success_response(
		data={"template": _detail(service.get(template))},
		message=_("Response template retrieved"),
	)


@route("", methods=("POST",), summary="Create a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateTemplate)
@api_doc(
	summary="Create a response template",
	description="Create a template at version 1. The subcategory is optional and must belong to "
	+ "the service category. Use {{ name }} placeholders in the wording for values the officer "
	+ "fills in.",
	tags=["Response Templates"],
	response_model=TemplateData,
)
def create_template(
	title: str,
	service_category: str,
	response_type: str,
	action_taken: str,
	resolution_summary: str,
	grievance_type: str | None = None,
	is_active: bool | str = True,
	**kwargs,
):
	"""Create a template."""
	doc = service.create(
		title=title,
		service_category=service_category,
		grievance_type=grievance_type,
		response_type=response_type,
		action_taken=action_taken,
		resolution_summary=resolution_summary,
		is_active=is_active,
	)
	return success_response(
		data={"template": _detail(doc)},
		message=_("Response template created"),
	)


@route("/<template>", methods=("PATCH",), summary="Update a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateTemplate)
@api_doc(
	summary="Update a response template",
	description="Partial update. A change to the title, response type, category, subcategory, or "
	+ "either body text saves the old wording to history and raises the version by one. Switching "
	+ "is_active alone does not. Send expected_version to refuse overwriting an edit you have not "
	+ "seen. change_note is stored with the replaced version.",
	tags=["Response Templates"],
	response_model=TemplateData,
)
def update_template(template: str, **kwargs):
	"""Update a template. `kwargs` holds only the fields the client sent."""
	doc = service.get(template)
	expected_version = kwargs.pop("expected_version", None)
	change_note = kwargs.pop("change_note", None)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(doc, kwargs, expected_version=expected_version, change_note=change_note)
	return success_response(
		data={"template": _detail(doc)},
		message=_("Response template updated"),
	)


@route("/<template>", methods=("DELETE",), summary="Delete or deactivate a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(TemplateRef)
@api_doc(
	summary="Delete or deactivate a response template",
	description="A template never used in a response is deleted with its history, and deleted is "
	+ "true. A template that has been used is kept as evidence and deactivated instead, and deleted "
	+ "is false. Repeating the call on a deactivated template changes nothing.",
	tags=["Response Templates"],
	response_model=TemplateDeleteData,
)
def delete_template(template: str, **kwargs):
	"""Delete an unused template, or deactivate a used one."""
	doc = service.get(template)
	deleted = service.remove(doc)
	return success_response(
		data={"template": _detail(doc), "deleted": deleted},
		message=_("Response template deleted") if deleted else _("Response template deactivated"),
	)
