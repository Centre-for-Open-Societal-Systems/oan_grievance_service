# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Response template CRUD for the Administration Response Templates tab (STG-405).

A response template is the pre-written action-taken and resolution-summary wording an
officer starts from when filing a formal response, scoped by service category, an optional
subcategory (grievance type), and response type. Wording may carry `{{ name }}` placeholders.

Validation and the version number live on the doctype. Editing wording or scope raises
`version` by one, and Frappe's change log keeps what each edit replaced, which GET returns
as `versions`. DELETE removes a template that was never used and only deactivates one that was.
"""

import json
from typing import Annotated, Any, Literal

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
from pydantic import BaseModel, Field, StringConstraints, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.grievance_management.doctype.grievance_response.grievance_response import (
	ACTION_TAKEN_LIMIT,
)
from oan_grievance_service.grievance_masters.doctype.grievance_response_template.grievance_response_template import (
	VERSIONED_FIELDS,
	extract_placeholders,
)
from oan_grievance_service.services.resolvers import resolve_grievance_type, resolve_service_category

route = prefixed("/api/v1/response-templates")

DOCTYPE = "Grievance Response Template"
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
	"use_count",
	"last_used_on",
	"creation",
	"modified",
	"modified_by",
]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=140)]
ActionTaken = Annotated[
	str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ACTION_TAKEN_LIMIT)
]
ResolutionSummary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
# The same values as the doctype's `response_type` Select; a test keeps the two in step.
ResponseType = Literal["Resolved", "Partially Resolved", "Referred to another dept", "Requires further info"]


class FieldChange(BaseModel):
	field: str
	old: Any = None
	new: Any = None


class TemplateVersionRecord(BaseModel):
	version: int = Field(description="The template's version once this edit was saved")
	edited_by: str
	edited_on: str
	changes: list[FieldChange]


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


class UpdateTemplate(Body):
	"""Partial update. Omitted fields stay as they are, so only grievance_type accepts null."""

	template: NonBlank
	title: Title = None
	service_category: NonBlank = None
	grievance_type: NonBlank | None = None
	response_type: ResponseType = None
	action_taken: ActionTaken = None
	resolution_summary: ResolutionSummary = None
	is_active: bool = None

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


def _iso(value) -> str | None:
	if not value:
		return None
	return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _get(name: str):
	"""The template document, or a 404."""
	if not frappe.db.exists(DOCTYPE, name):
		frappe.throw(
			_("Response template '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
			title=_("Not Found"),
		)
	return frappe.get_doc(DOCTYPE, name)


def _grievance_type(value: str, category: str | None) -> str:
	name = resolve_grievance_type(value, category)
	if not name:
		frappe.throw(_("Grievance type '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def _records(rows: list) -> list[dict]:
	"""Project template rows with one query each for category and type display names."""
	if not rows:
		return []
	categories = frappe.get_all(
		"Grievance Service Category",
		filters={"name": ["in", list({row.service_category for row in rows})]},
		fields=["name", "category_name"],
	)
	categories = {row.name: row.category_name for row in categories}
	types = frappe.get_all(
		"Grievance Type",
		filters={"name": ["in", list({row.grievance_type for row in rows if row.grievance_type})]},
		fields=["name", "type_name"],
	)
	types = {row.name: row.type_name for row in types}
	return [
		{
			"id": row.name,
			"title": row.title,
			"service_category": row.service_category,
			"service_category_name": categories.get(row.service_category),
			"grievance_type": row.grievance_type or None,
			"grievance_type_name": types.get(row.grievance_type),
			"response_type": row.response_type,
			"action_taken": row.action_taken,
			"resolution_summary": row.resolution_summary,
			"placeholders": extract_placeholders(row.action_taken, row.resolution_summary),
			"version": row.version,
			"is_active": bool(row.is_active),
			"use_count": row.use_count or 0,
			"last_used_on": _iso(row.last_used_on),
			"created_on": _iso(row.creation),
			"modified_on": _iso(row.modified),
			"modified_by": row.modified_by,
		}
		for row in rows
	]


def _versions(doc) -> list[dict]:
	"""What each edit changed, newest first, from Frappe's change log.

	`old` is the wording the edit replaced. Frappe stores every value as text, so a
	number or flag comes back as a string. Edits that touched nothing but bookkeeping
	fields are left out.
	"""
	shown = {*VERSIONED_FIELDS, "is_active", "version"}
	logs = frappe.get_all(
		"Version",
		filters={"ref_doctype": DOCTYPE, "docname": doc.name},
		fields=["owner", "creation", "data"],
		order_by="creation desc, name desc",
	)
	versions = []
	version_after = doc.version
	for log in logs:
		changed = [
			{"field": field, "old": old, "new": new}
			for field, old, new in json.loads(log.data).get("changed", [])
			if field in shown
		]
		if not changed:
			continue
		versions.append(
			{
				"version": version_after,
				"edited_by": log.owner,
				"edited_on": _iso(log.creation),
				"changes": changed,
			}
		)
		for change in changed:
			if change["field"] == "version":
				version_after = int(change["old"])
	return versions


def _detail(doc) -> dict:
	"""One template with what each edit changed."""
	row = frappe.get_all(DOCTYPE, filters={"name": doc.name}, fields=TEMPLATE_FIELDS)[0]
	return {**_records([row])[0], "versions": _versions(doc)}


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
	+ "Edit history is on the single-template route.",
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
		filters["service_category"] = resolve_service_category(service_category)
	if grievance_type:
		filters["grievance_type"] = _grievance_type(grievance_type, filters.get("service_category"))
	if response_type:
		filters["response_type"] = response_type
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0
	if q:
		escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
		filters["title"] = ["like", f"%{escaped}%"]
	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=TEMPLATE_FIELDS,
		order_by="modified desc, name desc",
		offset=params.start,
		limit=params.page_size,
	)
	return success_response(
		data={
			"templates": _records(rows),
			"pagination": page_meta(frappe.db.count(DOCTYPE, filters), params.page, params.page_size),
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
	description="One template and what each edit changed, newest first. `old` is the wording "
	+ "the edit replaced. The current wording is on the template itself.",
	tags=["Response Templates"],
	response_model=TemplateData,
)
def get_template(template: str, **kwargs):
	"""Return one template with its edit history."""
	return success_response(
		data={"template": _detail(_get(template))},
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
	category = resolve_service_category(service_category)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"title": title,
			"service_category": category,
			"grievance_type": _grievance_type(grievance_type, category) if grievance_type else None,
			"response_type": response_type,
			"action_taken": action_taken,
			"resolution_summary": resolution_summary,
			"is_active": 1 if is_active else 0,
		}
	).insert(ignore_permissions=True)
	return success_response(
		data={"template": _detail(doc)},
		message=_("Response template created"),
	)


@route("/<template>", methods=("PATCH",), summary="Update a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateTemplate, exclude_unset=True)
@api_doc(
	summary="Update a response template",
	description="Partial update. A change to the title, response type, category, subcategory, or "
	+ "either body text raises the version by one and is kept in the edit history. Switching "
	+ "is_active alone does not raise it.",
	tags=["Response Templates"],
	response_model=TemplateData,
)
def update_template(template: str, **kwargs):
	"""Update a template. `kwargs` holds only the fields the client sent."""
	doc = _get(template)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	if "service_category" in kwargs:
		kwargs["service_category"] = resolve_service_category(kwargs["service_category"])
	if kwargs.get("grievance_type"):
		category = kwargs.get("service_category", doc.service_category)
		kwargs["grievance_type"] = _grievance_type(kwargs["grievance_type"], category)
	doc.update(kwargs)
	doc.save(ignore_permissions=True)
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
	description="A template that has not been used in a response is deleted, and deleted is true. "
	+ "A used template is kept as a record of what officers sent and deactivated instead, and "
	+ "deleted is false. Repeating the call on a deactivated template changes nothing.",
	tags=["Response Templates"],
	response_model=TemplateDeleteData,
)
def delete_template(template: str, **kwargs):
	"""Delete an unused template, or deactivate a used one."""
	doc = _get(template)
	deleted = not doc.use_count
	if deleted:
		record = _detail(doc)
		frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
	else:
		if doc.is_active:
			doc.is_active = 0
			doc.save(ignore_permissions=True)
		record = _detail(doc)
	return success_response(
		data={"template": record, "deleted": deleted},
		message=_("Response template deleted") if deleted else _("Response template deactivated"),
	)
