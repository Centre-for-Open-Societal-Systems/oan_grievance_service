# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer management for the Administration Nodal Officers (L1) and Senior Officers (L2) tabs.

L1 and L2 officers are one entity told apart by `level`. Officers are not hard-deleted:
deactivate one by PATCHing `status` to Inactive. Performance metrics (assigned, resolved,
average time, resolution rate) are not part of this resource; the statistics API serves them.

Handlers stay thin. Field and link checks live in the profile's `validate()` and the
workflow lives in `services/officer.py`.
"""

from typing import Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_role,
	success_response,
	validate_email_string,
	validate_request,
)
from pydantic import BaseModel, field_validator

from oan_grievance_service.api.v1._pagination import PageParams, page_meta
from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import officer as service

route = prefixed("/api/v1/officers")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]
Level = Literal["L1", "L2"]
Status = Literal["Active", "On Leave", "Inactive"]


class OfficerRecord(BaseModel):
	name: str
	full_name: str
	designation: str
	level: Level
	department: str
	email: str
	phone: str | None = None
	region: str | None = None
	region_name: str | None = None
	status: Status
	service_categories: list[str]
	reports_to: str | None = None
	reports_to_name: str | None = None
	user: str | None = None


class OfficerData(BaseModel):
	officer: OfficerRecord


class OfficerListData(BaseModel):
	officers: list[OfficerRecord]
	pagination: dict


class OfficerRef(Body):
	officer: NonBlank


class CreateOfficer(Body):
	full_name: NonBlank
	designation: NonBlank
	level: Level
	department: NonBlank
	email: NonBlank
	phone: str | None = None
	region: str | None = None
	status: Status = "Active"
	service_categories: list[NonBlank] | None = None
	reports_to: str | None = None
	user: str | None = None

	_email = field_validator("email")(validate_email_string)
	_blank = field_validator("phone", "region", "reports_to", "user", mode="before")(blank_to_none)


class UpdateOfficer(Body):
	"""Partial update. Omitted fields stay as they are. `level` is fixed once created."""

	officer: NonBlank
	full_name: NonBlank = None
	designation: NonBlank = None
	department: NonBlank = None
	email: NonBlank = None
	phone: str | None = None
	region: str | None = None
	status: Status = None
	service_categories: list[NonBlank] = None
	reports_to: str | None = None
	user: str | None = None

	_email = field_validator("email")(validate_email_string)
	_blank = field_validator("phone", "region", "reports_to", "user", mode="before")(blank_to_none)


class ListOfficers(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	level: Level | None = None
	department: str | None = None
	status: Status | None = None
	q: str | None = None

	_blank = field_validator("level", "department", "status", "q", mode="before")(blank_to_none)


@route("", methods=("GET",), summary="List officers")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListOfficers)
@api_doc(
	summary="List officers",
	description="Admin list of L1 and L2 officers, filterable by level, department and status. "
	+ "q matches name, email or id.",
	tags=["Administration"],
	response_model=OfficerListData,
)
def list_officers(
	level: str | None = None,
	department: str | None = None,
	status: str | None = None,
	q: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List officer profiles, newest first.

	Numeric parameters also accept str: frappe checks annotations before validate_request
	runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	filters = service.list_filters(level=level, department=department, status=status)
	or_filters = service.search_filters(q)
	rows = frappe.get_all(
		service.DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=service.FIELDS,
		order_by="modified desc, name desc",
		offset=params.start,
		limit=params.page_size,
	)
	total = service.count(filters, or_filters)
	return success_response(
		data={"officers": service.records(rows), "pagination": page_meta(params, total)},
		message=_("Officers retrieved"),
	)


@route("/<officer>", methods=("GET",), summary="Get an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(OfficerRef)
@api_doc(
	summary="Get an officer",
	description="One L1 or L2 officer profile.",
	tags=["Administration"],
	response_model=OfficerData,
)
def get_officer(officer: str, **kwargs):
	"""Return one officer profile."""
	return success_response(
		data={"officer": service.record(service.get_profile(officer).name)},
		message=_("Officer retrieved"),
	)


@route("", methods=("POST",), summary="Create an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateOfficer)
@api_doc(
	summary="Create an officer",
	description="Create an L1 or L2 officer profile. reports_to is only for an L2 and must name an L1.",
	tags=["Administration"],
	response_model=OfficerData,
)
def create_officer(
	full_name: str,
	designation: str,
	level: str,
	department: str,
	email: str,
	phone: str | None = None,
	region: str | None = None,
	status: str = "Active",
	service_categories: list | None = None,
	reports_to: str | None = None,
	user: str | None = None,
	**kwargs,
):
	"""Create an officer profile."""
	doc = service.create(
		full_name=full_name,
		designation=designation,
		level=level,
		department=department,
		email=email,
		phone=phone,
		region=region,
		status=status,
		service_categories=service_categories,
		reports_to=reports_to,
		user=user,
	)
	return success_response(data={"officer": service.record(doc.name)}, message=_("Officer created"))


@route("/<officer>", methods=("PATCH",), summary="Update an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateOfficer, exclude_unset=True)
@api_doc(
	summary="Update an officer",
	description="Change contact details, department, region, status, service categories or supervisor. "
	+ "Set status to Inactive to deactivate. service_categories replaces the whole list. "
	+ "The level is fixed once created.",
	tags=["Administration"],
	response_model=OfficerData,
)
def update_officer(officer: str, **kwargs):
	"""Update an officer. `kwargs` holds only the fields the client sent."""
	doc = service.get_profile(officer)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(doc, kwargs)
	return success_response(data={"officer": service.record(doc.name)}, message=_("Officer updated"))
