# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer management for the Administration tabs: Nodal Officers (L1), Senior Officers (L2),
Department Heads (L3) and Reviewers, through one resource configured by `role` and `level`.

An officer is a User placed on category desks through Grievance RBAC Assignment Officer
rows, so what an admin edits here is what routing, permissions and escalation read. The
officer id is the User id, which is the officer's email.

A department head is an L3 officer: a department's final escalation rung, who approves its
reassignments. A Reviewer is placed on a department's desks too, with read-only access limited
to those desks and no cases assigned to them. `role` defaults to Officer, which is exactly the
behaviour this resource had before reviewers and L3 existed.

Officers are not deleted: set `status` to Inactive. Performance metrics (assigned, resolved,
average time, resolution rate) are served by the statistics API, not by this resource.

An officer who is given a temporary password at creation, or one reissued here, cannot sign in
until they replace it through `POST /api/v1/auth/password/initial`. The mechanism lives in
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
)
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import officer as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES

route = prefixed("/api/v1/officers")

Level = Literal["L1", "L2", "L3"]
Status = Literal["Active", "On Leave", "Inactive"]
Role = Literal["Officer", "Reviewer"]


def normalize_email(value: str) -> str:
	"""The one place an officer's email is validated and canonicalised. The User id is this value."""
	return validate_email_string(value.strip()).lower()


def field_errors(model: type[BaseModel], errors: dict[str, str]) -> None:
	"""Raise one validation error per field, so `details` names each field at fault."""
	raise ValidationError.from_exception_data(
		model.__name__,
		[
			InitErrorDetails(type=PydanticCustomError("role_field", message), loc=(field,), input=None)
			for field, message in errors.items()
		],
	)


def role_or_default(value):
	"""Before-validator: a missing or blank role means Officer."""
	return "Officer" if value is None or (isinstance(value, str) and not value.strip()) else value


def validate_temporary_password(value: str) -> str:
	"""The temporary-password rule, which oan_auth_service owns.

	Imported on use so this module still loads against an auth service that predates it.
	"""
	from oan_auth_service.api.utils import validate_temporary_password as rule

	return rule(value)


class OfficerAssignment(BaseModel):
	assignment: str
	service_category: str | None = None
	department: str | None = None
	level: Level | None = None
	region: str | None = None
	active: bool
	on_leave: bool


class OfficerRecord(BaseModel):
	name: str
	full_name: str
	role: Role
	designation: str | None = None
	level: Level | None = None
	department: str | None = None
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


class StatusCounts(BaseModel):
	active: int
	on_leave: int
	inactive: int
	total: int


class OfficerRef(Body):
	officer: NonBlank


class CreateOfficer(Body):
	"""An officer needs a level; a Reviewer is placed on the same desks without one.

	`designation`, `level`, `department` and `service_categories` are required for an Officer.
	A Reviewer needs the department and categories whose desks they read, and has no level or
	supervisor, so those are refused for them. The checks are in one model validator because
	they depend on `role`.
	"""

	full_name: NonBlank
	email: NonBlank
	temporary_password: str = Field(max_length=128)
	role: Role = "Officer"
	designation: NonBlank | None = None
	level: Level | None = None
	department: NonBlank | None = None
	service_categories: list[NonBlank] | None = None
	phone: str | None = None
	region: str | None = None
	status: Status = "Active"
	reports_to: str | None = None

	_email = field_validator("email")(normalize_email)
	_blank = field_validator(
		"phone", "region", "reports_to", "designation", "level", "department", mode="before"
	)(blank_to_none)
	_temporary_password = field_validator("temporary_password")(validate_temporary_password)

	@model_validator(mode="after")
	def _fields_follow_role(self):
		errors = {}
		if not self.department:
			errors["department"] = f"Required when role is {self.role}"
		if not self.service_categories:
			errors["service_categories"] = f"Required when role is {self.role}: give at least one"
		if self.role == "Officer":
			for field in ("designation", "level"):
				if not getattr(self, field):
					errors[field] = "Required when role is Officer"
		else:
			for field in ("level", "reports_to"):
				if getattr(self, field):
					errors[field] = f"Not accepted when role is {self.role}: it applies to officers only"
			if self.status == "On Leave":
				errors["status"] = "A Reviewer is Active or Inactive, not On Leave"
		if errors:
			field_errors(type(self), errors)
		return self


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


class OfficerFilters(Body):
	"""Filters shared by the list and the status counts.

	Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered
	list. A Reviewer has no level, so asking for one with that role is refused rather than
	answered with an empty page.
	"""

	role: Role = "Officer"
	level: Level | None = None
	department: str | None = None
	service_category: str | None = None
	region: str | None = None
	q: str | None = None

	_role = field_validator("role", mode="before")(role_or_default)
	_blank = field_validator("level", "department", "service_category", "region", "q", mode="before")(
		blank_to_none
	)

	@model_validator(mode="after")
	def _filters_follow_role(self):
		if self.role == "Reviewer" and self.level:
			field_errors(
				type(self), {"level": "Not accepted when role is Reviewer: it applies to officers only"}
			)
		return self


class StatusCountsQuery(OfficerFilters):
	"""The list's filters without `status`, which the counts break down by."""


class ListOfficers(PageParams, OfficerFilters):
	status: Status | None = None

	_status_blank = field_validator("status", mode="before")(blank_to_none)

	@model_validator(mode="after")
	def _status_follows_role(self):
		if self.role == "Reviewer" and self.status == "On Leave":
			field_errors(type(self), {"status": "A Reviewer is Active or Inactive, not On Leave"})
		return self


@route("", methods=("GET",), summary="List officers")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListOfficers)
@api_doc(
	summary="List officers",
	description="Admin list of officers (role Officer, the default) or Reviewers. level L3 lists the "
	+ "department heads, which the Admin tab shows. Filterable by level, department, status, "
	+ "service category and region. region matches an account's area exactly. q matches name "
	+ "or email. A Reviewer has no level, so level is refused for role Reviewer, and so is "
	+ "status On Leave. Listing Reviewers needs an admin role: a Review Officer reads Officer "
	+ "lists only.",
	tags=["Administration"],
	response_model=OfficerListData,
)
def list_officers(
	role: str = "Officer",
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
	"""List accounts of one role by name.

	Numeric parameters also accept str: frappe checks annotations before validate_request
	runs, and a bare int would turn a bad value into its own type error.
	"""
	service.assert_may_read(role)
	params = PageParams(page=page, page_size=page_size)
	ids, total = service.list_officers(
		role=role,
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


# Registered before `/<officer>` so the dynamic route never captures "status-counts".
@route("/status-counts", methods=("GET",), summary="Count officers by status")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(StatusCountsQuery)
@api_doc(
	summary="Count officers by status",
	description="Active, On Leave and Inactive totals for the officers tabs, so the UI shows the "
	+ "counts from here instead of hardcoding them. Takes the list's filters (role, level, "
	+ "department, service_category, region, q) without status, so a tab's counts follow the "
	+ "same filters as its list. Each person is counted once, in the status the record shows: "
	+ "the most available of their desk rows wins. The list's status filter matches an account "
	+ "with a row in that status, so one whose rows differ can be on two pages and is still one "
	+ "count here. A Reviewer has no On Leave. Counting Reviewers needs an admin role, as "
	+ "listing them does.",
	tags=["Administration"],
	response_model=StatusCounts,
)
def officer_status_counts(
	role: str = "Officer",
	level: str | None = None,
	department: str | None = None,
	service_category: str | None = None,
	region: str | None = None,
	q: str | None = None,
	**kwargs,
):
	"""Count the accounts the list would return, by status."""
	service.assert_may_read(role)
	counts = service.status_counts(
		role=role,
		level=level,
		department=department,
		service_category=service_category,
		region=region,
		q=q,
	)
	return success_response(data=counts, message=_("Officer status counts retrieved"))


@route("/<officer>", methods=("GET",), summary="Get an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(OfficerRef)
@api_doc(
	summary="Get an officer",
	description="One officer, department head or Reviewer with the desks (RBAC assignments) "
	+ "they sit on. The id is the account's email. Reading a Reviewer needs an admin role.",
	tags=["Administration"],
	response_model=OfficerData,
)
def get_officer(officer: str, **kwargs):
	"""Return one account."""
	found = service.record(officer)
	service.assert_may_read(found["role"])
	return success_response(data={"officer": found}, message=_("Officer retrieved"))


@route("", methods=("POST",), summary="Create an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(CreateOfficer)
@api_doc(
	summary="Create an officer",
	description="Make a login an officer or a Reviewer on the category desks of a department. "
	+ "role defaults to Officer. An Officer needs level (L1, L2 or L3), department, "
	+ "service_categories and designation; level L3 makes a department head, who is reached "
	+ "by escalation and is not assigned first-line cases. A Reviewer needs department and "
	+ "service_categories and takes no level or reports_to: they read those desks' cases only, "
	+ "are never assigned one and cannot change one. Every service category must already have "
	+ "a category assignment for the department. reports_to is an L2 officer and is only for "
	+ "an L1. The login is created when the email is new. An existing login keeps its own "
	+ "roles and is given the officer or review role; a System Manager or Administrator is "
	+ "never managed here. temporary_password is required: the account holder signs in with it "
	+ "only to replace it through /api/v1/auth/password/initial. It is applied to a new login "
	+ "only. An email that already has a login keeps its own password and the message says so.",
	tags=["Administration"],
	response_model=OfficerData,
)
def create_officer(
	full_name: str,
	email: str,
	temporary_password: str,
	role: str = "Officer",
	designation: str | None = None,
	level: str | None = None,
	department: str | None = None,
	service_categories: list | None = None,
	phone: str | None = None,
	region: str | None = None,
	status: str = "Active",
	reports_to: str | None = None,
	**kwargs,
):
	"""Create an officer, department head or reviewer."""
	user_id, password_applied = service.create(
		role=role,
		full_name=full_name,
		designation=designation,
		level=level,
		department=department,
		email=email,
		service_categories=service_categories,
		temporary_password=temporary_password,
		phone=phone,
		region=region,
		status=status,
		reports_to=reports_to,
	)
	message = _("{0} created").format(role)
	if not password_applied:
		message = _("{0} created. {1} already had a login, so their existing password is unchanged.").format(
			role, user_id
		)
	return success_response(data={"officer": service.record(user_id)}, message=message)


@route("/<officer>", methods=("PATCH",), summary="Update an officer")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateOfficer, exclude_unset=True)
@api_doc(
	summary="Update an officer",
	description="Change name, designation, phone, level, department, region, status, service "
	+ "categories or supervisor. Set status to Inactive to deactivate: the account's desk rows "
	+ "are retired too, and a Reviewer's login is disabled and its sessions and refresh tokens "
	+ "ended, so it cannot sign in. service_categories replaces the whole list. Changing level "
	+ "clears the officer's supervisor, and an L2 with L1 officers reporting to them cannot "
	+ "change level. A Reviewer has no level or supervisor, so those are refused, and so is "
	+ "On Leave. An account cannot deactivate itself. email and role are fixed once created. A "
	+ "System Manager or Administrator is never updated here.",
	tags=["Administration"],
	response_model=OfficerData,
)
def update_officer(officer: str, **kwargs):
	"""Update an account. `kwargs` holds only the fields the client sent."""
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(officer, kwargs)
	return success_response(data={"officer": service.record(officer)}, message=_("Officer updated"))


@route("/<officer>/password-resets", methods=("POST",), summary="Issue a new temporary password")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(ResetTemporaryPassword)
@api_doc(
	summary="Issue a new temporary password",
	description="Set a new temporary password on an officer or Reviewer who cannot sign in, for "
	+ "example after a forgotten password. The account's current sessions end at once and its "
	+ "holder must replace the password through /api/v1/auth/password/initial before signing "
	+ "in. Not available for an account that itself holds an admin role, nor for your own "
	+ "account. Limited to 10 requests per admin every 5 minutes, and logged.",
	tags=["Administration"],
	response_model=OfficerData,
)
def reset_temporary_password(officer: str, temporary_password: str, **kwargs):
	"""Reissue a temporary password to an officer or reviewer. The account must already exist."""
	check_rate_limit(f"rl:officer_temporary_password:{frappe.session.user}", limit=10, window=300)

	service.reset_temporary_password(officer, temporary_password)
	found = service.record(officer)
	frappe.logger("oan_grievance_service").info(
		f"temporary password reissued by={frappe.session.user} for={officer} role={found['role']}"
	)
	return success_response(
		data={"officer": found},
		message=_("Temporary password issued. The officer must set their own password before signing in."),
	)
