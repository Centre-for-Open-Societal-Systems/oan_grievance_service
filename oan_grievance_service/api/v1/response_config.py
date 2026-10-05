# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Response template CRUD for the Administration Response Templates tab.

Templates are administrator-owned master data, each written for one Grievance workflow
action. They are never deleted: DELETE deactivates, so the usage count keeps meaning
something. The template's code is its identifier and is fixed once created.

Handlers stay thin. Field and link checks live in each doctype's `validate()`.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	handle_api_errors,
	page_meta,
	require_role,
	success_response,
	validate_request,
)
from pydantic import Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none

template_route = prefixed("/api/v1/response-templates")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]

TEMPLATE_DOCTYPE = "Grievance Response Template"
TEMPLATE_FIELDS = [
	"name",
	"title",
	"workflow_action",
	"department",
	"service_category",
	"body",
	"usage_count",
	"is_active",
]


# Response templates
# ------------------


class TemplateRef(Body):
	template: NonBlank


class CreateResponseTemplateRequest(Body):
	template: NonBlank = Field(description="Template code; the template's identifier")
	title: NonBlank
	workflow_action: NonBlank = Field(description="Grievance workflow action the template is written for")
	department: NonBlank | None = Field(None, description="Omit for every department")
	service_category: NonBlank | None = Field(None, description="Omit for every category")
	body: NonBlank = Field(description="Jinja template for the response's reason")
	is_active: bool = True

	_scope_blank = field_validator("department", "service_category", mode="before")(blank_to_none)


class UpdateResponseTemplateRequest(Body):
	"""Partial update. The code is the identifier and cannot change; null clears a scope."""

	template: NonBlank
	title: NonBlank = None
	workflow_action: NonBlank = None
	department: NonBlank | None = None
	service_category: NonBlank | None = None
	body: NonBlank = None
	is_active: bool = None

	_scope_blank = field_validator("department", "service_category", mode="before")(blank_to_none)


class ListResponseTemplatesRequest(PageParams, Body):
	workflow_action: str | None = None
	department: str | None = None
	service_category: str | None = None
	is_active: bool | None = None

	_active_blank = field_validator("is_active", mode="before")(blank_to_none)


def _template_record(row) -> dict:
	return {
		"template": row.name,
		"title": row.title,
		"workflow_action": row.workflow_action,
		"department": row.department,
		"service_category": row.service_category,
		"body": row.body,
		"usage_count": row.usage_count or 0,
		"is_active": bool(row.is_active),
	}


def _get_template(name: str):
	if not frappe.db.exists(TEMPLATE_DOCTYPE, name):
		frappe.throw(_("Response Template '{0}' does not exist.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(TEMPLATE_DOCTYPE, name)


@template_route("", methods=("GET",), summary="List response templates")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListResponseTemplatesRequest)
def list_response_templates(
	workflow_action: str | None = None,
	department: str | None = None,
	service_category: str | None = None,
	is_active: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""Admin list of response templates with their usage counts."""
	params = PageParams(page=page, page_size=page_size)
	filters = {
		key: value
		for key, value in (
			("workflow_action", workflow_action),
			("department", department),
			("service_category", service_category),
		)
		if value
	}
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0
	rows = frappe.get_all(
		TEMPLATE_DOCTYPE,
		filters=filters,
		fields=TEMPLATE_FIELDS,
		order_by="modified desc, name desc",
		offset=params.start,
		limit_page_length=params.page_size,
	)
	return success_response(
		data={
			"response_templates": [_template_record(row) for row in rows],
			"pagination": page_meta(
				frappe.db.count(TEMPLATE_DOCTYPE, filters), params.page, params.page_size
			),
		},
		message=_("Response templates retrieved"),
	)


@template_route("/<template>", methods=("GET",), summary="Get a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(TemplateRef)
def get_response_template(template: str, **kwargs):
	"""Return one response template, unrendered."""
	return success_response(
		data={"response_template": _template_record(_get_template(template))},
		message=_("Response template retrieved"),
	)


@template_route("", methods=("POST",), summary="Create a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateResponseTemplateRequest)
def create_response_template(
	template: str,
	title: str,
	workflow_action: str,
	body: str,
	department: str | None = None,
	service_category: str | None = None,
	is_active: bool | str = True,
	**kwargs,
):
	"""Create a response template for one workflow action, optionally scoped."""
	doc = frappe.get_doc(
		{
			"doctype": TEMPLATE_DOCTYPE,
			"template_code": template,
			"title": title,
			"workflow_action": workflow_action,
			"department": department,
			"service_category": service_category,
			"body": body,
			"is_active": 1 if is_active else 0,
		}
	).insert()
	return success_response(
		data={"response_template": _template_record(doc)}, message=_("Response template created")
	)


@template_route("/<template>", methods=("PATCH",), summary="Update a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateResponseTemplateRequest, exclude_unset=True)
def update_response_template(template: str, **kwargs):
	"""Change a template's text, scope, workflow action or active flag. Edits are tracked."""
	doc = _get_template(template)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	if "is_active" in kwargs:
		kwargs["is_active"] = 1 if kwargs["is_active"] else 0
	doc.update(kwargs)
	doc.save()
	return success_response(
		data={"response_template": _template_record(doc)}, message=_("Response template updated")
	)


@template_route("/<template>", methods=("DELETE",), summary="Deactivate a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(TemplateRef)
def deactivate_response_template(template: str, **kwargs):
	"""Retire a template. Same as PATCH with is_active false; repeating it is a no-op."""
	doc = _get_template(template)
	doc.is_active = 0
	doc.save()
	return success_response(
		data={"response_template": _template_record(doc)}, message=_("Response template deactivated")
	)
