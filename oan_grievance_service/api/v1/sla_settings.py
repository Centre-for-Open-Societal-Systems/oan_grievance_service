# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""SLA settings for the Administration SLA tab: the global policy and the per-category windows.

Two surfaces, each backed by one record (see `services/sla_settings.py`):

- `/api/v1/sla-policy` is the installation-wide policy: the deferral ceiling, who approves
  a deferral, and the default auto-escalate threshold.
- `/api/v1/sla-configurations` is the per-category windows: `sla_days`, `auto_escalate` and
  `notify_on_breach`. These are the same values the Category Assignments tab shows as
  `sla_days` and `auto_escalate`, stored once on the category's SLA configuration and
  shared by every department that serves the category.

Handlers stay thin. Field and range checks live in the doctypes' `validate()`, so they hold
for the Desk as well as the API. The settings are read when a case's clock is armed, so a
change applies to cases that start, resume or re-arm after it, and leaves armed cases alone.
"""

from typing import Literal

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
from oan_grievance_service.services import sla_settings as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES

route = prefixed("/api/v1")

DEFERRAL_APPROVAL = Literal["l2_approval", "l1_self_approve"]


class GlobalSlaPolicyRecord(BaseModel):
	max_deferral_days: int
	auto_escalation_threshold: int
	deferral_approval: DEFERRAL_APPROVAL
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
	deferral_approval: DEFERRAL_APPROVAL = Field(
		default=None,
		description="l2_approval needs a senior officer to decide. l1_self_approve lets the assignee",
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


def _policy_data(policy: dict) -> dict:
	return {"policy": GlobalSlaPolicyRecord(**_text_modified(policy)).model_dump()}


def _config_data(record: dict) -> dict:
	return {"sla_configuration": SlaConfigurationRecord(**_text_modified(record)).model_dump()}


def _text_modified(record: dict) -> dict:
	"""The response carries `modified` as the ISO-8601 text the other admin endpoints use."""
	modified = record.get("modified")
	return {**record, "modified": get_datetime(modified).isoformat() if modified else None}


@route("/sla-policy", methods=("GET",), summary="Get the global SLA policy")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@api_doc(
	summary="Get the global SLA policy",
	description="The installation-wide SLA settings: the most days one deferral may add, the default "
	+ "auto-escalate threshold in percent, and who approves a deferral (l2_approval or l1_self_approve). "
	+ "Categories without a threshold of their own follow this one.",
	tags=["Administration"],
	response_model=GlobalSlaPolicyData,
)
def get_global_policy(**kwargs):
	"""Return the installation-wide SLA policy."""
	return success_response(
		data=_policy_data(service.global_policy()),
		message=_("Global SLA policy retrieved"),
	)


@route("/sla-policy", methods=("PATCH",), summary="Update the global SLA policy")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateGlobalSlaPolicy, exclude_unset=True)
@api_doc(
	summary="Update the global SLA policy",
	description="Change any of max_deferral_days, auto_escalation_threshold and deferral_approval. "
	+ "Omitted fields stay as they are. A new threshold applies to cases whose escalation is armed "
	+ "after the change. A new deferral setting applies to the next deferral request.",
	tags=["Administration"],
	response_model=GlobalSlaPolicyData,
)
def update_global_policy(**kwargs):
	"""Update the installation-wide policy. `kwargs` holds only the fields the client sent."""
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	return success_response(
		data=_policy_data(service.update_global_policy(kwargs)),
		message=_("Global SLA policy updated"),
	)


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
	records, total = service.list_configs(
		service_category=service_category,
		department=department,
		start=params.start,
		page_size=params.page_size,
	)
	return success_response(
		data={
			"sla_configurations": [
				SlaConfigurationRecord(**_text_modified(record)).model_dump() for record in records
			],
			"pagination": page_meta(total, params.page, params.page_size),
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
	record = service.get_config(config)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update_config(record, kwargs)
	return success_response(
		data=_config_data(service.config_record(record.name)),
		message=_("SLA configuration updated"),
	)
