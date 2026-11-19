# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""SLA settings for the Administration SLA tab: the global policy and the per-category windows.

Two surfaces, each backed by one record:

- `/api/v1/sla-policy` is the installation-wide policy: the deferral ceiling, who approves
  a deferral, and the default auto-escalate threshold.
- `/api/v1/sla-configurations` is the per-category windows: `sla_days`, `auto_escalate` and
  `notify_on_breach`. These are the same values the Category Assignments tab shows as
  `sla_days` and `auto_escalate`, stored once on the category's SLA configuration and
  shared by every department that serves the category. Its `rules` are the seasonal and
  urgent rules (STG-514) that change `sla_days`, urgency and priority for the cases they match.

Handlers stay thin. Field and range checks live in the doctypes' `validate()`, so they hold
for the Desk as well as the API. What is here is request shape and the projection of the records.
The settings are read when a case's clock is armed, so a
change applies to cases that start, resume or re-arm after it, and leaves armed cases alone.
"""

from typing import Literal

import frappe
from frappe import N_, _
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
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1._schemas import Body, NonBlank
from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
	auto_escalation_threshold,
	get_policy,
	max_deferral_days,
	requires_supervisor_approval,
)
from oan_grievance_service.grievance_sla.doctype.grievance_sla_configuration.grievance_sla_configuration import (
	get_active_config,
)
from oan_grievance_service.services import category_assignment
from oan_grievance_service.services.constants import ADMIN_READ_ROLES
from oan_grievance_service.services.resolvers import resolve_department, resolve_service_category

route = prefixed("/api/v1")

POLICY = "Grievance Deferral Policy"
SLA_CONFIGURATION = "Grievance SLA Configuration"
CONFIGURATION_FIELDS = [
	"name",
	"service_category",
	"sla_days",
	"auto_escalate",
	"notify_on_breach",
	"modified",
]

# The only fields a PATCH may write. The request schema already forbids anything else; this
# keeps that true if a schema ever grows a field the document should not take from a client.
POLICY_EDITABLE = ("max_deferral_days", "auto_escalation_threshold", "requires_supervisor_approval")
CONFIGURATION_EDITABLE = ("sla_days", "auto_escalate", "notify_on_breach")
# A seasonal or urgent rule of a configuration (Grievance SLA Rule, STG-514). `rules` is written as a
# whole: the list sent replaces every rule of the configuration.
RULE = "Grievance SLA Rule"
RULE_FIELDS = (
	"rule_label",
	"grievance_type",
	"administrative_area",
	"start_month",
	"start_day",
	"end_month",
	"end_day",
	"sla_days",
	"is_urgent",
	"priority",
)


class SlaRuleRecord(BaseModel):
	rule_label: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None
	start_month: int | None = None
	start_day: int | None = None
	end_month: int | None = None
	end_day: int | None = None
	sla_days: int | None = None
	is_urgent: bool = False
	priority: str | None = None


class SlaRuleBody(Body):
	"""One rule. Name a type, an area and a window only to narrow it; all are optional."""

	rule_label: str | None = Field(None, max_length=140, description="A name for administrators")
	grievance_type: NonBlank | None = Field(None, description="A type of this category. Blank: every type")
	administrative_area: NonBlank | None = Field(
		None, description="Covers the areas inside it. Blank: everywhere"
	)
	start_month: int | None = Field(None, ge=1, le=12)
	start_day: int | None = Field(None, ge=1, le=31)
	end_month: int | None = Field(None, ge=1, le=12)
	end_day: int | None = Field(None, ge=1, le=31, description="All four window fields, or none for all year")
	sla_days: int | None = Field(None, ge=1, description="Blank keeps the category's sla_days")
	is_urgent: bool = False
	priority: Literal["Normal", "High", "Urgent"] | None = Field(
		None, description="Blank: Urgent for an urgent rule, otherwise Normal"
	)


class GlobalSlaPolicyRecord(BaseModel):
	max_deferral_days: int
	auto_escalation_threshold: int
	requires_supervisor_approval: bool
	modified: str | None = None


class GlobalSlaPolicyData(BaseModel):
	policy: GlobalSlaPolicyRecord


class SlaConfigurationRecord(BaseModel):
	name: str
	service_category: str
	departments: list[str]
	sla_days: int
	auto_escalate: bool
	notify_on_breach: bool
	rules: list[SlaRuleRecord] = []
	modified: str | None = None


class SlaConfigurationData(BaseModel):
	sla_configuration: SlaConfigurationRecord


class SlaConfigurationListData(BaseModel):
	sla_configurations: list[SlaConfigurationRecord]
	pagination: dict


class UpdateGlobalSlaPolicy(Body):
	"""Partial update. Omitted fields stay as they are."""

	max_deferral_days: int = Field(
		default=None, ge=1, description="Most days any single deferral may add to the SLA clock"
	)
	auto_escalation_threshold: int = Field(
		default=None,
		ge=1,
		le=100,
		description="Percent of the SLA window consumed before escalation. 100 is at the deadline",
	)
	requires_supervisor_approval: bool = Field(
		default=None,
		description="true: a senior officer decides a deferral. false: the assigned officer may approve it",
	)


class SlaConfigurationRef(Body):
	config: NonBlank


class UpdateSlaConfiguration(Body):
	"""Partial update. Omitted fields stay as they are."""

	config: NonBlank
	sla_days: int = Field(default=None, ge=1, title=N_("SLA days"))
	auto_escalate: bool = None
	notify_on_breach: bool = None
	rules: list[SlaRuleBody] | None = Field(
		None, description="Replaces every seasonal and urgent rule. [] or null removes them all."
	)


class ListSlaConfigurations(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	department: str | None = None


def _text(modified) -> str | None:
	"""`modified` as the ISO-8601 UTC text the other endpoints return."""
	return to_tz_aware_iso(modified)


def _policy_data() -> dict:
	"""The installation-wide policy, with the defaults filled in while the Single is untouched."""
	return {
		"policy": GlobalSlaPolicyRecord(
			max_deferral_days=max_deferral_days(),
			auto_escalation_threshold=auto_escalation_threshold(),
			requires_supervisor_approval=requires_supervisor_approval(),
			modified=_text(get_policy().modified),
		).model_dump()
	}


def _rules_of(names: list[str]) -> dict[str, list[dict]]:
	"""The rules of each named configuration, in table order, with one query for all of them."""
	rules: dict[str, list[dict]] = {name: [] for name in names}
	if not names:
		return rules
	for row in frappe.get_all(
		RULE,
		filters={"parent": ["in", names], "parenttype": SLA_CONFIGURATION, "parentfield": "sla_rules"},
		fields=["parent", *RULE_FIELDS],
		order_by="parent asc, idx asc",
	):
		rules[row.parent].append(
			SlaRuleRecord(
				**{field: row.get(field) or None for field in RULE_FIELDS if field != "is_urgent"},
				is_urgent=bool(row.is_urgent),
			).model_dump()
		)
	return rules


def _configuration_records(rows: list) -> list[dict]:
	"""Project SLA rows to API records with one query for the departments of the whole page."""
	rules = _rules_of([row.name for row in rows])
	departments: dict[str, list[str]] = {row.service_category: [] for row in rows}
	if departments:
		for desk in frappe.get_all(
			category_assignment.DOCTYPE,
			filters=category_assignment.serving_filters(
				category_scope=["in", list(departments)], department_scope=["is", "set"]
			),
			fields=["category_scope", "department_scope"],
			order_by="department_scope asc",
		):
			if desk.department_scope not in departments[desk.category_scope]:
				departments[desk.category_scope].append(desk.department_scope)
	return [
		SlaConfigurationRecord(
			name=row.name,
			service_category=row.service_category,
			departments=departments[row.service_category],
			sla_days=row.sla_days,
			auto_escalate=bool(row.auto_escalate),
			notify_on_breach=bool(row.notify_on_breach),
			rules=rules[row.name],
			modified=_text(row.modified),
		).model_dump()
		for row in rows
	]


def _configuration_record(name: str) -> dict:
	rows = frappe.get_all(SLA_CONFIGURATION, filters={"name": name}, fields=CONFIGURATION_FIELDS)
	return _configuration_records(rows)[0]


@route("/sla-policy", methods=("GET",), summary="Get the global SLA policy", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@api_doc(
	summary="Get the global SLA policy",
	description="The installation-wide SLA settings: the most days one deferral may add, the default "
	+ "auto-escalate threshold in percent, and whether a deferral needs a senior officer's approval "
	+ "(requires_supervisor_approval; false lets the assigned officer approve it). "
	+ "Categories without a threshold of their own follow this one.",
	tags=["Administration"],
	response_model=GlobalSlaPolicyData,
)
def get_global_policy(**kwargs):
	"""Return the installation-wide SLA policy."""
	return success_response(
		data=_policy_data(),
		message=_("Global SLA policy retrieved"),
	)


@route("/sla-policy", methods=("PATCH",), summary="Update the global SLA policy", tier="officer-core")
@handle_api_errors
@require_permission("Grievance Deferral Policy", "write")
@validate_request(UpdateGlobalSlaPolicy, exclude_unset=True)
@api_doc(
	summary="Update the global SLA policy",
	description="Change any of max_deferral_days, auto_escalation_threshold and requires_supervisor_approval. "
	+ "Omitted fields stay as they are. A new threshold applies to cases whose escalation is armed "
	+ "after the change. A new deferral setting applies to the next deferral request.",
	tags=["Administration"],
	response_model=GlobalSlaPolicyData,
)
def update_global_policy(**kwargs):
	"""Update the installation-wide policy. `kwargs` holds only the fields the client sent."""
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	policy = frappe.get_doc(POLICY)
	policy.update({key: kwargs[key] for key in POLICY_EDITABLE if key in kwargs})
	policy.save()
	return success_response(data=_policy_data(), message=_("Global SLA policy updated"))


@route("/sla-configurations", methods=("GET",), summary="List per-category SLA settings", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListSlaConfigurations)
@api_doc(
	summary="List per-category SLA settings",
	description="The SLA window, auto-escalate flag and notify-on-breach flag of each service category, "
	+ "with the departments that have an active category assignment for it. These are the same "
	+ "sla_days and auto_escalate values the category assignments show. Filter by service category "
	+ "or department.",
	tags=["Administration"],
	response_model=SlaConfigurationListData,
)
def list_sla_configurations(
	service_category: str | None = None,
	department: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List the active per-category SLA configurations.

	Numeric parameters also accept str: frappe checks annotations before validate_request
	runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	filters: dict = {"active": 1}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	if department:
		served = frappe.get_all(
			category_assignment.DOCTYPE,
			filters=category_assignment.serving_filters(
				department_scope=resolve_department(department),
			),
			pluck="category_scope",
		)
		if "service_category" in filters:
			served = [name for name in served if name == filters["service_category"]]
		filters["service_category"] = ["in", list(set(served)) or [""]]
	rows = frappe.get_all(
		SLA_CONFIGURATION,
		filters=filters,
		fields=CONFIGURATION_FIELDS,
		order_by="service_category asc, name asc",
		offset=params.start,
		limit_page_length=params.page_size,
	)
	return success_response(
		data={
			"sla_configurations": _configuration_records(rows),
			"pagination": page_meta(
				frappe.db.count(SLA_CONFIGURATION, filters), params.page, params.page_size
			),
		},
		message=_("SLA configurations retrieved"),
	)


@route(
	"/sla-configurations/<config>",
	methods=("PATCH",),
	summary="Update a category's SLA settings",
	tier="officer-core",
)
@handle_api_errors
@require_permission("Grievance SLA Configuration", "write")
@validate_request(UpdateSlaConfiguration, exclude_unset=True)
@api_doc(
	summary="Update a category's SLA settings",
	description="Change sla_days, auto_escalate, notify_on_breach or the seasonal and urgent rules on "
	+ "one service category's SLA configuration. Omitted fields stay as they are. The values are "
	+ "shared with every department serving the category and with its category assignments, so a "
	+ "change here shows there. The service category is fixed. "
	+ "rules replaces every rule. A rule may name a grievance_type of the category, an "
	+ "administrative_area (covering the areas inside it) and a recurring window (start_month, "
	+ "start_day, end_month, end_day; a start after the end wraps the year end), and sets sla_days, "
	+ "is_urgent and priority for the cases it matches on their filing date. The most specific "
	+ "matching rule wins. Two rules for the same type and area may not share a day. Rules apply to "
	+ "cases filed afterwards.",
	tags=["Administration"],
	response_model=SlaConfigurationData,
)
def update_sla_configuration(config: str, **kwargs):
	"""Update one category's SLA configuration. `kwargs` holds only the fields the client sent."""
	doc = get_active_config(config)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update({key: kwargs[key] for key in CONFIGURATION_EDITABLE if key in kwargs})
	if "rules" in kwargs:
		doc.set("sla_rules", [])
		for rule in kwargs["rules"] or []:
			doc.append(
				"sla_rules",
				{
					**{field: rule.get(field) for field in RULE_FIELDS},
					"is_urgent": 1 if rule.get("is_urgent") else 0,
				},
			)
	doc.save()
	return success_response(
		data={"sla_configuration": _configuration_record(doc.name)},
		message=_("SLA configuration updated"),
	)
