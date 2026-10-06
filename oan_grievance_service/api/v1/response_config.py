# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Response template CRUD for the Administration Response Templates tab.

Templates are administrator-owned master data, each written for one Grievance workflow
action. They are never deleted: DELETE deactivates, so the usage count keeps meaning
something. The template's code is its identifier and is fixed once created.

A template's text may be sent and read as two parts, `action_taken` and
`resolution_summary`, which are stored together as its body; see
`services/response_body.py`.

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
from pydantic import Field, field_validator, model_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import response_body

route = prefixed("/api/v1/response-templates")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]

TEMPLATE_DOCTYPE = "Grievance Response Template"
TEMPLATE_FIELDS = [
	"name",
	"title",
	"workflow_action",
	"department",
	"service_category",
	"body",
	"note",
	"usage_count",
	"is_active",
]


# Response templates
# ------------------


class TemplateRef(Body):
	template: NonBlank


def _check_body_parts(model, require_body: bool):
	"""`action_taken` and `resolution_summary` come together, in place of `reason`/`body`."""
	has_text = bool(model.body or model.reason)
	if model.action_taken is None and model.resolution_summary is None:
		if require_body and not has_text:
			raise ValueError("Reason (or body, or action_taken with resolution_summary) is required")
		return
	if model.action_taken is None or model.resolution_summary is None:
		raise ValueError("action_taken and resolution_summary are sent together")
	if has_text:
		raise ValueError("Send either reason/body or action_taken with resolution_summary, not both")


class CreateResponseTemplateRequest(Body):
	title: NonBlank
	action: NonBlank | None = Field(None, description="Grievance workflow action the template is written for")
	workflow_action: NonBlank | None = Field(
		None, description="Grievance workflow action the template is written for"
	)
	department: NonBlank | None = Field(None, description="Omit for every department")
	service_category: NonBlank | None = Field(None, description="Omit for every category")
	reason: NonBlank | None = Field(None, description="Jinja template for the response's reason")
	body: NonBlank | None = Field(None, description="Jinja template for the response's reason")
	action_taken: NonBlank | None = Field(
		None, description="Jinja template for the reason's first part; sent with resolution_summary"
	)
	resolution_summary: NonBlank | None = Field(
		None, description="Jinja template for the reason's second part; sent with action_taken"
	)
	note: str | None = Field(None, description="Optional Jinja template for internal notes")
	is_active: bool = True

	_scope_blank = field_validator("department", "service_category", "note", mode="before")(blank_to_none)

	@model_validator(mode="after")
	def check_action_and_reason(self):
		if not self.workflow_action and not self.action:
			raise ValueError("Action (or workflow_action) is required")
		_check_body_parts(self, require_body=True)
		if not self.workflow_action:
			self.workflow_action = self.action
		if not self.body:
			self.body = self.reason
		return self


class UpdateResponseTemplateRequest(Body):
	"""Partial update. The code is the identifier and cannot change; null clears a scope."""

	template: NonBlank
	title: NonBlank = None
	action: NonBlank = None
	workflow_action: NonBlank = None
	department: NonBlank | None = None
	service_category: NonBlank | None = None
	reason: NonBlank = None
	body: NonBlank = None
	action_taken: NonBlank = None
	resolution_summary: NonBlank = None
	note: str | None = None
	is_active: bool = None

	_scope_blank = field_validator("department", "service_category", "note", mode="before")(blank_to_none)

	@model_validator(mode="after")
	def check_body_parts(self):
		_check_body_parts(self, require_body=False)
		return self


class ListResponseTemplatesRequest(PageParams, Body):
	action: str | None = None
	workflow_action: str | None = None
	department: str | None = None
	service_category: str | None = None
	category: str | None = None
	is_active: bool | None = None

	_active_blank = field_validator("is_active", mode="before")(blank_to_none)


def _template_record(row) -> dict:
	action_val = (
		getattr(row, "workflow_action", None)
		if hasattr(row, "workflow_action")
		else (row.get("workflow_action") if isinstance(row, dict) else None)
	)
	body_val = (
		getattr(row, "body", None)
		if hasattr(row, "body")
		else (row.get("body") if isinstance(row, dict) else None)
	)
	note_val = (
		getattr(row, "note", None)
		if hasattr(row, "note")
		else (row.get("note") if isinstance(row, dict) else None)
	)
	name_val = (
		getattr(row, "name", None)
		if hasattr(row, "name")
		else (row.get("name") if isinstance(row, dict) else None)
	)
	title_val = (
		getattr(row, "title", None)
		if hasattr(row, "title")
		else (row.get("title") if isinstance(row, dict) else None)
	)
	dept_val = (
		getattr(row, "department", None)
		if hasattr(row, "department")
		else (row.get("department") if isinstance(row, dict) else None)
	)
	cat_val = (
		getattr(row, "service_category", None)
		if hasattr(row, "service_category")
		else (row.get("service_category") if isinstance(row, dict) else None)
	)
	usage_val = (
		getattr(row, "usage_count", 0)
		if hasattr(row, "usage_count")
		else (row.get("usage_count") if isinstance(row, dict) else 0)
	)
	active_val = (
		getattr(row, "is_active", 0)
		if hasattr(row, "is_active")
		else (row.get("is_active") if isinstance(row, dict) else 0)
	)
	return {
		"template": name_val,
		"title": title_val,
		"action": action_val,
		"workflow_action": action_val,
		"department": dept_val,
		"service_category": cat_val,
		"reason": body_val or "",
		"body": body_val or "",
		"reason_parts": response_body.split(body_val),
		"note": note_val or "",
		"usage_count": usage_val or 0,
		"is_active": bool(active_val),
	}


def _get_template(name: str):
	if not frappe.db.exists(TEMPLATE_DOCTYPE, name):
		frappe.throw(_("Response Template '{0}' does not exist.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(TEMPLATE_DOCTYPE, name)


@route("", methods=("GET",), summary="List response templates")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListResponseTemplatesRequest)
def list_response_templates(
	action: str | None = None,
	workflow_action: str | None = None,
	department: str | None = None,
	service_category: str | None = None,
	category: str | None = None,
	is_active: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""Admin list of response templates with their usage counts."""
	params = PageParams(page=page, page_size=page_size)
	wf_action = action or workflow_action
	cat = service_category or category
	filters = {
		key: value
		for key, value in (
			("workflow_action", wf_action),
			("department", department),
			("service_category", cat),
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


@route("/<template>", methods=("GET",), summary="Get a response template")
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


@route("", methods=("POST",), summary="Create a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateResponseTemplateRequest)
def create_response_template(
	title: str,
	workflow_action: str | None = None,
	action: str | None = None,
	body: str | None = None,
	reason: str | None = None,
	action_taken: str | None = None,
	resolution_summary: str | None = None,
	note: str | None = None,
	department: str | None = None,
	service_category: str | None = None,
	is_active: bool | str = True,
	**kwargs,
):
	"""Create a response template for one workflow action, optionally scoped."""
	wf_action = workflow_action or action
	if action_taken is not None:
		body_text = response_body.compose(action_taken, resolution_summary)
	else:
		body_text = body or reason
	doc = frappe.get_doc(
		{
			"doctype": TEMPLATE_DOCTYPE,
			"title": title,
			"workflow_action": wf_action,
			"department": department,
			"service_category": service_category,
			"body": body_text,
			"note": note,
			"is_active": 1 if is_active else 0,
		}
	).insert()
	return success_response(
		data={"response_template": _template_record(doc)}, message=_("Response template created")
	)


@route("/<template>", methods=("PATCH",), summary="Update a response template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateResponseTemplateRequest, exclude_unset=True)
def update_response_template(template: str, **kwargs):
	"""Change a template's text, scope, workflow action or active flag. Edits are tracked."""
	doc = _get_template(template)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	if "action" in kwargs:
		kwargs["workflow_action"] = kwargs.pop("action")
	if "reason" in kwargs:
		kwargs["body"] = kwargs.pop("reason")
	if "action_taken" in kwargs:
		kwargs["body"] = response_body.compose(kwargs.pop("action_taken"), kwargs.pop("resolution_summary"))
	if "is_active" in kwargs:
		kwargs["is_active"] = 1 if kwargs["is_active"] else 0
	doc.update(kwargs)
	doc.save()
	return success_response(
		data={"response_template": _template_record(doc)}, message=_("Response template updated")
	)


@route("/<template>", methods=("DELETE",), summary="Deactivate a response template")
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
