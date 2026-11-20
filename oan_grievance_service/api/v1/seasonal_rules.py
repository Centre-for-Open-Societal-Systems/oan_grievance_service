# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Seasonal and urgent SLA rules (STG-514): `/api/v1/seasonal-rules`.

One record per rule (Grievance SLA Rule), linked to its service category, so a client lists,
creates, changes and deletes one rule at a time. A rule may name a grievance type of the category,
an administrative area (covering the areas inside it) and a recurring window of the year, and
sets the SLA days, urgency and priority of the cases it matches. `services/classification.py`
decides which rule a case matches; nothing here does.

Handlers stay thin. The window, the type's category and the no-tie check live in the rule's
`validate()`, so they hold for the Desk as well as the API. A rule is read when a case is filed
(and when it moves to another type or category), so a change applies to cases filed afterwards.
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

route = prefixed("/api/v1/seasonal-rules")

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
RECORD_FIELDS = ["name", "service_category", *EDITABLE, "modified"]

RULE_NOTE = (
	"A rule may name a grievance_type of its category, an administrative_area (covering the areas "
	"inside it) and a recurring window (start_month, start_day, end_month, end_day: all four, or "
	"none for all year; a start after the end wraps the year end, for example 10/1 to 4/30). It sets "
	"sla_days, is_urgent and priority for the cases it matches on their filing date; blank sla_days "
	"keeps the category's. The most specific matching rule wins: a type's rule before the "
	"category's, a narrower area before a wider one, a windowed rule before an all-year one. Two "
	"windowed rules, or two all-year rules, for the same type and area may not share a day. A "
	"change applies to cases filed afterwards."
)

Priority = Literal["Normal", "High", "Urgent"]


class SeasonalRuleRecord(BaseModel):
	name: str
	service_category: str
	rule_label: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None
	start_month: int | None = None
	start_day: int | None = None
	end_month: int | None = None
	end_day: int | None = None
	sla_days: int | None = None
	is_urgent: bool
	priority: str | None = None
	modified: str | None = None


class SeasonalRuleData(BaseModel):
	rule: SeasonalRuleRecord


class SeasonalRuleListData(BaseModel):
	rules: list[SeasonalRuleRecord]
	pagination: dict


class RuleRef(Body):
	rule_id: NonBlank


class _RuleFields(Body):
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
	sla_days: int | None = Field(None, ge=1, description="Blank keeps the category's sla_days")
	priority: Priority | None = Field(None, description="Blank: Urgent for an urgent rule, otherwise Normal")

	_blank = field_validator(
		"rule_label", "grievance_type", "administrative_area", "priority", mode="before"
	)(blank_to_none)


class CreateSeasonalRule(_RuleFields):
	service_category: NonBlank = Field(description="Id, name or code of the category")
	is_urgent: bool = False


class UpdateSeasonalRule(_RuleFields):
	"""Partial update. Omitted fields stay as they are; null clears a field."""

	rule_id: NonBlank
	is_urgent: bool | None = Field(None, description="null is false")


class ListSeasonalRules(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None
	is_urgent: bool | None = None

	_blank = field_validator("is_urgent", mode="before")(blank_to_none)


def _records(rows: list) -> list[dict]:
	return [
		SeasonalRuleRecord(
			name=row.name,
			service_category=row.service_category,
			# Int columns store a blank as 0.
			**{field: row.get(field) or None for field in EDITABLE if field != "is_urgent"},
			is_urgent=bool(row.is_urgent),
			modified=to_tz_aware_iso(row.modified),
		).model_dump()
		for row in rows
	]


def _record(name: str) -> dict:
	return _records(frappe.get_all(RULE, filters={"name": name}, fields=RECORD_FIELDS))[0]


def _get_rule(rule_id: str):
	if not frappe.db.exists(RULE, rule_id):
		frappe.throw(_("Seasonal rule '{0}' was not found.").format(rule_id), frappe.DoesNotExistError)
	return frappe.get_doc(RULE, rule_id)


def _values(kwargs: dict) -> dict:
	"""The editable fields the client sent, as the document stores them."""
	values = {key: kwargs[key] for key in EDITABLE if key in kwargs}
	if "is_urgent" in values:
		values["is_urgent"] = 1 if values["is_urgent"] else 0
	return values


@route("", methods=("GET",), summary="List seasonal and urgent SLA rules", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListSeasonalRules)
@api_doc(
	summary="List seasonal and urgent SLA rules",
	description="The seasonal and urgent rules, by service category and then oldest first. Filter by "
	+ "service_category (id, name or code), grievance_type, administrative_area or is_urgent. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SeasonalRuleListData,
)
def list_rules(
	service_category: str | None = None,
	grievance_type: str | None = None,
	administrative_area: str | None = None,
	is_urgent: bool | str | None = None,
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
		message=_("Seasonal rules retrieved"),
	)


@route("/<rule_id>", methods=("GET",), summary="Get a seasonal or urgent SLA rule", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(RuleRef)
@api_doc(
	summary="Get a seasonal or urgent SLA rule",
	description="One rule. The id is the Grievance SLA Rule's name.",
	tags=["Administration"],
	response_model=SeasonalRuleData,
)
def get_rule(rule_id: str, **kwargs):
	"""Return one rule."""
	return success_response(
		data={"rule": _record(_get_rule(rule_id).name)},
		message=_("Seasonal rule retrieved"),
	)


@route("", methods=("POST",), summary="Create a seasonal or urgent SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "create")
@validate_request(CreateSeasonalRule)
@api_doc(
	summary="Create a seasonal or urgent SLA rule",
	description="Add one rule to a service category. Only service_category is required: a rule "
	+ "that names nothing else and sets nothing applies to the whole category all year and says "
	+ "'standard here'. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SeasonalRuleData,
)
def create_rule(service_category: str, **kwargs):
	"""Create a rule. `kwargs` holds the other fields of the request."""
	doc = frappe.get_doc(
		{"doctype": RULE, "service_category": resolve_service_category(service_category), **_values(kwargs)}
	)
	doc.insert()
	return success_response(data={"rule": _record(doc.name)}, message=_("Seasonal rule created"))


@route("/<rule_id>", methods=("PATCH",), summary="Update a seasonal or urgent SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "write")
@validate_request(UpdateSeasonalRule, exclude_unset=True)
@api_doc(
	summary="Update a seasonal or urgent SLA rule",
	description="Change any field of one rule. Omitted fields stay as they are; null clears a field "
	+ "(all four window fields null makes the rule all year). The service category is fixed. "
	+ "Cases already filed keep the SLA and class they were filed with. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SeasonalRuleData,
)
def update_rule(rule_id: str, **kwargs):
	"""Update one rule. `kwargs` holds only the fields the client sent."""
	doc = _get_rule(rule_id)
	values = _values(kwargs)
	if not values:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update(values)
	doc.save()
	return success_response(data={"rule": _record(doc.name)}, message=_("Seasonal rule updated"))


@route("/<rule_id>", methods=("DELETE",), summary="Delete a seasonal or urgent SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "delete")
@validate_request(RuleRef)
@api_doc(
	summary="Delete a seasonal or urgent SLA rule",
	description="Remove one rule. Cases already filed keep the SLA and class it gave them: the case "
	+ "stores them, not the rule. Frappe keeps a copy of the deleted rule in Deleted Documents.",
	tags=["Administration"],
	response_model=SeasonalRuleData,
)
def delete_rule(rule_id: str, **kwargs):
	"""Delete one rule and return it as it was."""
	doc = _get_rule(rule_id)
	record = _record(doc.name)
	frappe.delete_doc(RULE, doc.name)
	return success_response(data={"rule": record}, message=_("Seasonal rule deleted"))
