# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""SLA settings for the Administration SLA tab: the global policy and the per-category windows.

Two surfaces, each backed by one record:

- `/api/v1/sla-policy` is the installation-wide policy: the deferral ceiling, who approves
  a deferral, and the default auto-escalate threshold.
- `/api/v1/sla-configurations` is the per-category windows: `sla_days`, `auto_escalate` and
  `notify_on_breach`. These are the same values the Category Assignments tab shows as
  `sla_days` and `auto_escalate`, stored once on the category's SLA configuration and
  shared by every department that serves the category.

Handlers stay thin. Field and range checks live in the doctypes' `validate()`, so they hold
for the Desk as well as the API. What is here is request shape and the projection of the records.
The settings are read when a case's clock is armed, so a
change applies to cases that start, resume or re-arm after it, and leaves armed cases alone.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime
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
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES
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
	sla_days: int = Field(default=None, ge=1)
	auto_escalate: bool = None
	notify_on_breach: bool = None


class ListSlaConfigurations(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	department: str | None = None


def _text(modified) -> str | None:
	"""`modified` as the ISO-8601 text the other admin endpoints return."""
	return get_datetime(modified).isoformat() if modified else None


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


def _configuration_records(rows: list) -> list[dict]:
	"""Project SLA rows to API records with one query for the departments of the whole page."""
	departments: dict[str, list[str]] = {row.service_category: [] for row in rows}
	if departments:
		for desk in frappe.get_all(
			category_assignment.DOCTYPE,
			filters=category_assignment.desk_filters(category_scope=["in", list(departments)], active=1),
			fields=["category_scope", "department_scope"],
			order_by="department_scope asc",
		):
			departments[desk.category_scope].append(desk.department_scope)
	return [
		SlaConfigurationRecord(
			name=row.name,
			service_category=row.service_category,
			departments=departments[row.service_category],
			sla_days=row.sla_days,
			auto_escalate=bool(row.auto_escalate),
			notify_on_breach=bool(row.notify_on_breach),
			modified=_text(row.modified),
		).model_dump()
		for row in rows
	]


def _configuration_record(name: str) -> dict:
	rows = frappe.get_all(SLA_CONFIGURATION, filters={"name": name}, fields=CONFIGURATION_FIELDS)
	return _configuration_records(rows)[0]


@route("/sla-policy", methods=("GET",), summary="Get the global SLA policy")
@frappe.whitelist()
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


@route("/sla-policy", methods=("PATCH",), summary="Update the global SLA policy")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
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


@route("/sla-configurations", methods=("GET",), summary="List per-category SLA settings")
@frappe.whitelist()
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
			filters=category_assignment.desk_filters(
				department_scope=resolve_department(department), active=1
			),
			pluck="category_scope",
		)
		if "service_category" in filters:
			served = [name for name in served if name == filters["service_category"]]
		filters["service_category"] = ["in", served or [""]]
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


@route("/sla-configurations/<config>", methods=("PATCH",), summary="Update a category's SLA settings")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateSlaConfiguration, exclude_unset=True)
@api_doc(
	summary="Update a category's SLA settings",
	description="Change sla_days, auto_escalate or notify_on_breach on one service category's SLA "
	+ "configuration. Omitted fields stay as they are. The values are shared with every department "
	+ "serving the category and with its category assignments, so a change here shows there. "
	+ "The service category is fixed.",
	tags=["Administration"],
	response_model=SlaConfigurationData,
)
def update_sla_configuration(config: str, **kwargs):
	"""Update one category's SLA configuration. `kwargs` holds only the fields the client sent."""
	doc = get_active_config(config)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update({key: kwargs[key] for key in CONFIGURATION_EDITABLE if key in kwargs})
	doc.save()
	return success_response(
		data={"sla_configuration": _configuration_record(doc.name)},
		message=_("SLA configuration updated"),
	)
