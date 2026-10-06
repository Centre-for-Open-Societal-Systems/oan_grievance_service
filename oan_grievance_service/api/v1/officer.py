# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer management for the Administration Nodal Officers (L1) and Senior Officers (L2) tabs.

An officer is a User placed on category desks through Grievance RBAC Assignment Officer
rows, so what an admin edits here is what routing, permissions and escalation read. The
officer id is the User id, which is the officer's email.

Officers are not deleted: set `status` to Inactive. Performance metrics (assigned, resolved,
average time, resolution rate) are served by the statistics API, not by this resource.

An officer who is given a temporary password at creation, or one reissued here, cannot sign in
until they replace it through `POST /api/v1/auth/set-initial-password`. The mechanism lives in
oan_auth_service; this resource only decides who may issue one, and to whom.
"""

from typing import Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	check_rate_limit,
	handle_api_errors,
	page_meta,
	require_role,
	success_response,
	validate_email_string,
	validate_request,
	validate_temporary_password,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import officer as service

route = prefixed("/api/v1/officers")

ADMIN_ROLES = ["Grievance Admin", "System Manager", "Administrator"]
Level = Literal["L1", "L2"]
Status = Literal["Active", "On Leave", "Inactive"]


def normalize_email(value: str) -> str:
	"""The one place an officer's email is validated and canonicalised. The User id is this value."""
	return validate_email_string(value.strip()).lower()


class OfficerAssignment(BaseModel):
	assignment: str
	service_category: str | None = None
	department: str | None = None
	level: Level
	region: str | None = None
	active: bool
	on_leave: bool


class OfficerRecord(BaseModel):
	name: str
	full_name: str
	designation: str | None = None
	level: Level
	department: str
	email: str
	phone: str | None = None
	must_change_password: bool = False
	region: str | None = None
	region_name: str | None = None
	status: Status
	service_categories: list[str]
	reports_to: str | None = None
	reports_to_name: str | None = None
	assignments: list[OfficerAssignment]


class OfficerData(BaseModel):
	officer: OfficerRecord


class OfficerListData(BaseModel):
	officers: list[OfficerRecord]


class OfficerRef(Body):
	officer: NonBlank


class CreateOfficer(Body):
	full_name: NonBlank
	designation: NonBlank
	level: Level
	department: NonBlank
	email: NonBlank
	service_categories: list[NonBlank] = Field(min_length=1)
	phone: str | None = None
	region: str | None = None
	status: Status = "Active"
	reports_to: str | None = None
	temporary_password: str | None = Field(default=None, max_length=128)

	_email = field_validator("email")(normalize_email)
	_blank = field_validator("phone", "region", "reports_to", "temporary_password", mode="before")(
		blank_to_none
	)

	@field_validator("temporary_password")
	@classmethod
	def _temporary_password(cls, value: str | None) -> str | None:
		return None if value is None else validate_temporary_password(value)


class ResetTemporaryPassword(Body):
	officer: NonBlank
	temporary_password: str = Field(max_length=128)

	_temporary_password = field_validator("temporary_password")(validate_temporary_password)


class UpdateOfficer(Body):
	"""Partial update. Omitted fields stay as they are. `email` is fixed once created."""

	officer: NonBlank
	level: Level = None
	full_name: NonBlank = None
	designation: NonBlank = None
	department: NonBlank = None
	phone: str | None = None
	region: str | None = None
	status: Status = None
	service_categories: list[NonBlank] = Field(default=None, min_length=1)
	reports_to: str | None = None

	_blank = field_validator("phone", "region", "reports_to", mode="before")(blank_to_none)


class ListOfficers(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	level: Level | None = None
	department: str | None = None
	status: Status | None = None
	service_category: str | None = None
	region: str | None = None
	q: str | None = None

	_blank = field_validator(
		"level", "department", "status", "service_category", "region", "q", mode="before"
	)(blank_to_none)


@route("", methods=("GET",), summary="List officers")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ListOfficers)
@api_doc(
	summary="List officers",
	description="Admin list of L1 and L2 officers, filterable by level, department, status, service "
	+ "category and region. region matches an officer's area exactly. q matches name or email.",
	tags=["Administration"],
	response_model=OfficerListData,
)
def list_officers(
	level: str | None = None,
	department: str | None = None,
	status: str | None = None,
	service_category: str | None = None,
	region: str | None = None,
	q: str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List officers by name.

	Numeric parameters also accept str: frappe checks annotations before validate_request
	runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	ids, total = service.list_officers(
		level=level,
		department=department,
		status=status,
		service_category=service_category,
		region=region,
		q=q,
		start=params.start,
		page_size=params.page_size,
	)
	return success_response(
		data={"officers": service.records(ids)},
		message=_("Officers retrieved"),
		pagination=page_meta(total, params.page, params.page_size),
	)


@route("/<officer>", methods=("GET",), summary="Get an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(OfficerRef)
@api_doc(
	summary="Get an officer",
	description="One L1 or L2 officer with the desks (RBAC assignments) they sit on. The id is the "
	+ "officer's email.",
	tags=["Administration"],
	response_model=OfficerData,
)
def get_officer(officer: str, **kwargs):
	"""Return one officer."""
	return success_response(data={"officer": service.record(officer)}, message=_("Officer retrieved"))


@route("", methods=("POST",), summary="Create an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateOfficer)
@api_doc(
	summary="Create an officer",
	description="Make a login an L1 or L2 officer on the category desks of a department. "
	+ "The login is created when the email is new. Every service category must already have a "
	+ "category assignment for the department. reports_to is an L2 officer and is only for an L1. "
	+ "temporary_password is optional and only for a new login: the officer can sign in with it only "
	+ "to replace it through set-initial-password.",
	tags=["Administration"],
	response_model=OfficerData,
)
def create_officer(
	full_name: str,
	designation: str,
	level: str,
	department: str,
	email: str,
	service_categories: list,
	phone: str | None = None,
	region: str | None = None,
	status: str = "Active",
	reports_to: str | None = None,
	temporary_password: str | None = None,
	**kwargs,
):
	"""Create an officer."""
	user_id = service.create(
		full_name=full_name,
		designation=designation,
		level=level,
		department=department,
		email=email,
		service_categories=service_categories,
		phone=phone,
		region=region,
		status=status,
		reports_to=reports_to,
		temporary_password=temporary_password,
	)
	return success_response(data={"officer": service.record(user_id)}, message=_("Officer created"))


@route("/<officer>", methods=("PATCH",), summary="Update an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateOfficer, exclude_unset=True)
@api_doc(
	summary="Update an officer",
	description="Change name, designation, phone, level, department, region, status, service categories "
	+ "or supervisor. Set status to Inactive to deactivate: the officer's desk rows are retired too. "
	+ "service_categories replaces the whole list. Changing level clears the officer's supervisor, and "
	+ "an L2 with L1 officers reporting to them cannot change level. email is fixed once created.",
	tags=["Administration"],
	response_model=OfficerData,
)
def update_officer(officer: str, **kwargs):
	"""Update an officer. `kwargs` holds only the fields the client sent."""
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(officer, kwargs)
	return success_response(data={"officer": service.record(officer)}, message=_("Officer updated"))


@route("/<officer>/temporary-password", methods=("POST",), summary="Issue a new temporary password")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ResetTemporaryPassword)
@api_doc(
	summary="Issue a new temporary password",
	description="Set a new temporary password on an officer who cannot sign in, for example after a "
	+ "forgotten password. The officer's current sessions end at once and they must replace the "
	+ "password through set-initial-password before signing in. Not available for an account that "
	+ "itself holds an admin role.",
	tags=["Administration"],
	response_model=OfficerData,
)
def reset_temporary_password(officer: str, temporary_password: str, **kwargs):
	"""Reissue a temporary password to an officer. The officer must already exist."""
	check_rate_limit(f"rl:officer_temporary_password:{frappe.session.user}", limit=10, window=300)

	# This sets a password the caller knows, so it must never reach an account more powerful
	# than the caller is entitled to manage. Officers are not admins; an account that is both
	# is recovered through the System Manager endpoint in oan_auth_service instead.
	if set(frappe.get_roles(officer)) & set(ADMIN_ROLES):
		frappe.throw(_("A temporary password cannot be issued for this account."), frappe.PermissionError)

	service.reset_temporary_password(officer, temporary_password)
	frappe.logger("oan_grievance_service").info(
		f"temporary password reissued by={frappe.session.user} for={officer}"
	)
	return success_response(
		data={"officer": service.record(officer)},
		message=_("Temporary password issued. The officer must set their own password before signing in."),
	)
