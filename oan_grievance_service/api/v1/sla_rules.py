# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""SLA rules (STG-514): `/api/v1/sla-rules`, the one source of a case's SLA days, urgency and priority.

One record per rule (Grievance SLA Rule), linked to its service category, so a client lists,
creates, changes and deletes one rule at a time. Every rule carries `sla_days`, `is_urgent` and
`priority`. A rule may name a grievance type of the category, an administrative area (covering
the areas inside it) and a recurring window of the year. The category's base rule names none of
them and is its SLA wherever nothing narrower matches. `services/classification.py` decides which
rule a case matches; nothing here does.

Handlers stay thin. The window, the type's category, the no-tie check and the base rule's
protection live in the rule's `validate()` and `on_trash()`, so they hold for the Desk as well
as the API. A rule is read when a case is filed (and when it moves to another type or category),
so a change applies to cases filed afterwards.
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
	require_permission,
	require_role,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services.constants import ADMIN_READ_ROLES
from oan_grievance_service.services.resolvers import resolve_service_category

route = prefixed("/api/v1/sla-rules")

RULE = "Grievance SLA Rule"
WINDOW_FIELDS = ("start_month", "start_day", "end_month", "end_day")
# What a client may write. `service_category` only on create: a rule stays with its category.
EDITABLE = (
	"rule_label",
	"grievance_type",
	"administrative_area",
	*WINDOW_FIELDS,
	"sla_days",
	"is_urgent",
	"priority",
)
RECORD_FIELDS = ["name", "service_category", "is_base", *EDITABLE, "modified"]

RULE_NOTE = (
	"Every rule sets sla_days, is_urgent and priority for the cases it matches on their filing date. "
	"A rule may name a grievance_type of its category, an administrative_area (covering the areas "
	"inside it) and a recurring window (start_month, start_day, end_month, end_day: all four, or "
	"none for all year; a start after the end wraps the year end, for example 10/1 to 4/30). The "
	"category's base rule (is_base) names none of them: it is the category's SLA wherever no "
	"narrower rule matches, a category needs it before any other rule, and it cannot be deleted or "
	"narrowed. The most specific matching rule wins: a type's rule before the category's, a narrower "
	"area before a wider one, a windowed rule before an all-year one. Two windowed rules, or two "
	"all-year rules, for the same type and area may not share a day. A change applies to cases "
	"filed afterwards."
)

Priority = Literal["Normal", "High", "Urgent"]


class SlaRuleRecord(BaseModel):
	name: str
	service_category: str
	is_base: bool
	rule_label: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None
	start_month: int | None = None
	start_day: int | None = None
	end_month: int | None = None
	end_day: int | None = None
	sla_days: int
	is_urgent: bool
	priority: str
	modified: str | None = None


class SlaRuleData(BaseModel):
	rule: SlaRuleRecord


class SlaRuleListData(BaseModel):
	rules: list[SlaRuleRecord]
	pagination: dict


class RuleRef(Body):
	rule_id: NonBlank


class _RuleScope(Body):
	rule_label: str | None = Field(None, max_length=140, description="A name for administrators")
	grievance_type: NonBlank | None = Field(
		None, description="A type of the rule's category. Blank: every type"
	)
	administrative_area: NonBlank | None = Field(
		None, description="Covers the areas inside it. Blank: everywhere"
	)
	start_month: int | None = Field(None, ge=1, le=12)
	start_day: int | None = Field(None, ge=1, le=31)
	end_month: int | None = Field(None, ge=1, le=12)
	end_day: int | None = Field(None, ge=1, le=31, description="All four window fields, or none for all year")

	_blank = field_validator("rule_label", "grievance_type", "administrative_area", mode="before")(
		blank_to_none
	)


class CreateSlaRule(_RuleScope):
	service_category: NonBlank = Field(description="Id, name or code of the category")
	sla_days: int = Field(ge=1, description="Working days for a case this rule matches")
	is_urgent: bool = False
	priority: Priority


class UpdateSlaRule(_RuleScope):
	"""Partial update. Omitted fields stay as they are; null clears a scope field."""

	rule_id: NonBlank
	# Typed optional so an omitted field is told apart from a sent one; a sent null is refused.
	sla_days: int | None = Field(None, ge=1)
	is_urgent: bool | None = None
	priority: Priority | None = None


class ListSlaRules(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None
	is_urgent: bool | None = None
	is_base: bool | None = None

	_blank = field_validator("is_urgent", "is_base", mode="before")(blank_to_none)


def _records(rows: list) -> list[dict]:
	scope = ("rule_label", "grievance_type", "administrative_area", *WINDOW_FIELDS)
	return [
		SlaRuleRecord(
			name=row.name,
			service_category=row.service_category,
			is_base=bool(row.is_base),
			# Int columns store a blank as 0.
			**{field: row.get(field) or None for field in scope},
			sla_days=row.sla_days or 0,
			is_urgent=bool(row.is_urgent),
			priority=row.priority,
			modified=to_tz_aware_iso(row.modified),
		).model_dump()
		for row in rows
	]


def _record(name: str) -> dict:
	return _records(frappe.get_all(RULE, filters={"name": name}, fields=RECORD_FIELDS))[0]


def _get_rule(rule_id: str):
	if not frappe.db.exists(RULE, rule_id):
		frappe.throw(_("SLA rule '{0}' was not found.").format(rule_id), frappe.DoesNotExistError)
	return frappe.get_doc(RULE, rule_id)


def _values(kwargs: dict) -> dict:
	"""The editable fields the client sent, as the document stores them."""
	values = {key: kwargs[key] for key in EDITABLE if key in kwargs}
	for field in ("sla_days", "is_urgent", "priority"):
		# These are never cleared: every rule carries all three.
		if field in values and values[field] is None:
			frappe.throw(
				_("{0} cannot be empty: every SLA rule sets it.").format(field), frappe.ValidationError
			)
	if "is_urgent" in values:
		values["is_urgent"] = 1 if values["is_urgent"] else 0
	return values


@route("", methods=("GET",), summary="List SLA rules", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListSlaRules)
@api_doc(
	summary="List SLA rules",
	description="The SLA rules, by service category and then oldest first. Filter by service_category "
	+ "(id, name or code), grievance_type, administrative_area, is_urgent or is_base. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleListData,
)
def list_rules(
	service_category: str | None = None,
	grievance_type: str | None = None,
	administrative_area: str | None = None,
	is_urgent: bool | str | None = None,
	is_base: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List rules.

	Numeric and boolean parameters also accept str: frappe checks annotations before
	validate_request runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	filters: dict = {}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	if grievance_type:
		filters["grievance_type"] = grievance_type
	if administrative_area:
		filters["administrative_area"] = administrative_area
	if is_urgent is not None:
		filters["is_urgent"] = 1 if is_urgent else 0
	if is_base is not None:
		filters["is_base"] = 1 if is_base else 0
	rows = frappe.get_all(
		RULE,
		filters=filters,
		fields=RECORD_FIELDS,
		order_by="service_category asc, creation asc, name asc",
		offset=params.start,
		limit_page_length=params.page_size,
	)
	return success_response(
		data={
			"rules": _records(rows),
			"pagination": page_meta(frappe.db.count(RULE, filters), params.page, params.page_size),
		},
		message=_("SLA rules retrieved"),
	)


@route("/<rule_id>", methods=("GET",), summary="Get an SLA rule", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(RuleRef)
@api_doc(
	summary="Get an SLA rule",
	description="One rule. The id is the Grievance SLA Rule's name.",
	tags=["Administration"],
	response_model=SlaRuleData,
)
def get_rule(rule_id: str, **kwargs):
	"""Return one rule."""
	return success_response(
		data={"rule": _record(_get_rule(rule_id).name)},
		message=_("SLA rule retrieved"),
	)


@route("", methods=("POST",), summary="Create an SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "create")
@validate_request(CreateSlaRule)
@api_doc(
	summary="Create an SLA rule",
	description="Add one rule to a service category. service_category, sla_days and priority are "
	+ "required; is_urgent defaults to false. A category's first rule must be its base rule (no "
	+ "type, area or window); creating a category with sla_days, or setting sla_days on "
	+ "/sla-configurations, makes it. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleData,
)
def create_rule(service_category: str, **kwargs):
	"""Create a rule. `kwargs` holds the other fields of the request."""
	doc = frappe.get_doc(
		{"doctype": RULE, "service_category": resolve_service_category(service_category), **_values(kwargs)}
	)
	doc.insert()
	return success_response(data={"rule": _record(doc.name)}, message=_("SLA rule created"))


@route("/<rule_id>", methods=("PATCH",), summary="Update an SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "write")
@validate_request(UpdateSlaRule, exclude_unset=True)
@api_doc(
	summary="Update an SLA rule",
	description="Change any field of one rule but service_category. Omitted fields stay as they are; "
	+ "null clears rule_label, grievance_type, administrative_area or a window field (all four null "
	+ "makes the rule all year). sla_days, is_urgent and priority cannot be cleared. The base rule "
	+ "cannot be narrowed. Cases already filed keep the SLA and class they were filed with. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleData,
)
def update_rule(rule_id: str, **kwargs):
	"""Update one rule. `kwargs` holds only the fields the client sent."""
	doc = _get_rule(rule_id)
	values = _values(kwargs)
	if not values:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update(values)
	doc.save()
	return success_response(data={"rule": _record(doc.name)}, message=_("SLA rule updated"))


@route("/<rule_id>", methods=("DELETE",), summary="Delete an SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "delete")
@validate_request(RuleRef)
@api_doc(
	summary="Delete an SLA rule",
	description="Remove one rule. The base rule cannot be deleted (VALIDATION_ERROR): change its "
	+ "sla_days instead. Cases already filed keep the SLA and class a rule gave them: the case "
	+ "stores them, not the rule. Frappe keeps a copy of the deleted rule in Deleted Documents.",
	tags=["Administration"],
	response_model=SlaRuleData,
)
def delete_rule(rule_id: str, **kwargs):
	"""Delete one rule and return it as it was."""
	doc = _get_rule(rule_id)
	record = _record(doc.name)
	frappe.delete_doc(RULE, doc.name)
	return success_response(data={"rule": record}, message=_("SLA rule deleted"))
