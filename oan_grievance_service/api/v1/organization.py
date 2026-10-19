# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Organization (NGO / FPO / Cooperative) registration and verification endpoints.

A representative registers one organization for their login and keeps it up to date
(`/orgs`, `/orgs/me`). Administrators work the queue of registrations and decide each
one (`/orgs`, `/orgs/<org_id>`, `/orgs/<org_id>/status`). An organization is In Review
until an administrator activates it; only then may its representative file grievances.
"""

import re
from datetime import datetime
from typing import Annotated, Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	SafeEmail,
	api_doc,
	handle_api_errors,
	page_meta,
	require_role,
	success_response,
	validate_request,
)
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import organization as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES, ROLE_SUBMITTER
from oan_grievance_service.services.identity import REGISTRATION_PATTERN

route = prefixed("/api/v1/orgs")

OrgType = Literal["NGO", "FPO", "Cooperative"]
OrgStatus = Literal["In Review", "Active", "Suspended"]


def _registration_number(value: str) -> str:
	if not REGISTRATION_PATTERN.match(value):
		raise ValueError("Registration number is not in a valid format.")
	return value


def _website(value: str | None) -> str | None:
	"""A bare "example.org" is taken as https://example.org."""
	if value is None:
		return value
	if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
		value = f"https://{value}"
	if len(value) > 255 or not re.match(r"^https?://[^\s/.]+(\.[^\s/.]+)+(/\S*)?$", value):
		raise ValueError("Website must be a valid http or https address.")
	return value


# Frappe's Data length, which is what the columns hold.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=140)]
RegistrationNumber = Annotated[Text, AfterValidator(_registration_number)]
# 3 to 10 letters and digits, with spaces or hyphens inside.
PostalCode = Annotated[
	str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{1,8}[A-Za-z0-9]$")
]
Website = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_website)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class OrganizationRecord(BaseModel):
	name: str
	legal_name: str
	brand_name: str | None = None
	org_type: str
	entity_type: str
	registration_number: str
	representative_user: str
	representative_name: str | None = None
	members_affected: int = 0
	contact_email: str
	contact_phone: str
	website: str | None = None
	street_address: str
	kebele: str | None = None
	woreda: str | None = None
	zone: str
	region: str
	country: str
	postal_code: str
	status: str
	status_reason: str | None = None
	creation: datetime
	modified: datetime


class OrganizationListData(BaseModel):
	organizations: list[OrganizationRecord]
	pagination: dict


# What the list reads: the record's own columns, and the workflow state as `status`.
LIST_FIELDS = [name for name in OrganizationRecord.model_fields if name != "status"] + [
	"workflow_state as status"
]


class OptionalFields(Body):
	"""What a registration may leave out, and the rules for each, shared by create and update."""

	brand_name: Text | None = None
	representative_name: Text | None = None
	members_affected: int | None = Field(default=None, ge=0, le=100_000_000)
	website: Website | None = None
	kebele: Text | None = None
	woreda: Text | None = None

	_blank = field_validator(
		"brand_name", "representative_name", "members_affected", "website", "kebele", "woreda", mode="before"
	)(blank_to_none)


class CreateOrganization(OptionalFields):
	legal_name: Text
	org_type: OrgType
	entity_type: Text
	registration_number: RegistrationNumber
	contact_email: SafeEmail
	contact_phone: Text
	street_address: Text
	zone: Text
	region: Text
	country: Text
	postal_code: PostalCode
	consent_given: Literal[True]


class UpdateOrganization(OptionalFields):
	"""Partial update: omitted fields stay as they are, and the optional ones accept null."""

	legal_name: Text = None
	org_type: OrgType = None
	entity_type: Text = None
	registration_number: RegistrationNumber = None
	contact_email: SafeEmail = None
	contact_phone: Text = None
	street_address: Text = None
	zone: Text = None
	region: Text = None
	country: Text = None
	postal_code: PostalCode = None


class OrganizationRef(Body):
	org_id: NonBlank


class SetOrganizationStatus(Body):
	org_id: NonBlank
	status: OrgStatus
	reason: Reason | None = Field(default=None, validate_default=True)

	_blank = field_validator("reason", mode="before")(blank_to_none)

	@field_validator("reason")
	@classmethod
	def _suspension_needs_a_reason(cls, value, info):
		if info.data.get("status") == "Suspended" and not value:
			raise ValueError("A reason is required to suspend an organization.")
		return value


class ListOrganizations(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	status: OrgStatus | None = None
	org_type: OrgType | None = None
	search: str | None = Field(default=None, max_length=100)
	sort: Literal["newest", "oldest"] = "newest"

	_blank = field_validator("status", "org_type", "search", mode="before")(blank_to_none)


def organization_record(org) -> dict:
	"""The organization as the API shows it: the document, with its workflow state as `status`."""
	return {**org.as_dict(), "status": org.workflow_state}


@route("", methods=("POST",), summary="Register an organization", tier="citizen-intake")
@handle_api_errors
@require_role([ROLE_SUBMITTER])
@validate_request(CreateOrganization)
@api_doc(
	summary="Register an organization",
	description="Register an NGO, FPO or cooperative with the caller as its authorized "
	+ "representative. It starts In Review and cannot file grievances until an administrator "
	+ "sets it Active. One organization per account, and one account per registration number. "
	+ "consent_given must be true.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def register_organization(**kwargs):
	kwargs.pop("consent_given")
	org = service.create(frappe.session.user, kwargs)
	return success_response(
		data=organization_record(org), message=_("Organization registered and awaiting verification")
	)


@route("/me", methods=("GET",), summary="Get my organization", tier="citizen-intake")
@handle_api_errors
@require_role([ROLE_SUBMITTER])
@api_doc(
	summary="Get my organization",
	description="The organization the caller is the authorized representative of, with its "
	+ "verification status. 404 until one is registered.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def get_my_organization(**kwargs):
	org = service.get_for_user(frappe.session.user)
	return success_response(data=organization_record(org), message=_("Organization retrieved"))


@route("/me", methods=("PATCH",), summary="Update my organization", tier="citizen-intake")
@handle_api_errors
@require_role([ROLE_SUBMITTER])
@validate_request(UpdateOrganization, exclude_unset=True)
@api_doc(
	summary="Update my organization",
	description="Edit the profile fields the caller sent. Not allowed while Suspended. "
	+ "Changing legal_name, org_type or registration_number on an Active organization sends it "
	+ "back to In Review, because the new details were never verified. The status itself "
	+ "cannot be set here.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def update_my_organization(**kwargs):
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	org = service.update(service.get_for_user(frappe.session.user), kwargs)
	return success_response(data=organization_record(org), message=_("Organization updated"))


@route("", methods=("GET",), summary="List organizations", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListOrganizations)
@api_doc(
	summary="List organizations",
	description="The admin verification queue. Filter by status (In Review for the pending "
	+ "queue), organization type, or a search over legal name, brand name and registration "
	+ "number. sort=oldest lists the longest-waiting registration first.",
	tags=["Organizations"],
	response_model=OrganizationListData,
)
def list_organizations(**kwargs):
	params = PageParams(page=kwargs.pop("page"), page_size=kwargs.pop("page_size"))
	rows, total = service.list_orgs(LIST_FIELDS, start=params.start, page_length=params.page_size, **kwargs)
	return success_response(
		data={"organizations": rows, "pagination": page_meta(total, params.page, params.page_size)},
		message=_("Organizations retrieved"),
	)


@route("/<org_id>", methods=("GET",), summary="Get an organization", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(OrganizationRef)
@api_doc(
	summary="Get an organization",
	description="One organization with its representative, contact details and the status of "
	+ "its verification, for the admin review screen.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def get_organization(org_id: str, **kwargs):
	return success_response(
		data=organization_record(service.get_org(org_id)), message=_("Organization retrieved")
	)


@route(
	"/<org_id>/status",
	methods=("PATCH",),
	summary="Set an organization's verification status",
	tier="officer-core",
)
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(SetOrganizationStatus)
@api_doc(
	summary="Set an organization's verification status",
	description="The administrator's decision, taken through the Organization Workflow. "
	+ "In Review to Active verifies it and lets its representative file grievances. Active or "
	+ "In Review to Suspended withdraws or rejects it and needs a reason. Suspended to Active "
	+ "reinstates it, and Suspended to In Review reopens the review. Other moves are refused. "
	+ "Review Officers cannot decide.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def set_organization_status(org_id: str, status: str, reason: str | None = None, **kwargs):
	org = service.set_status(service.get_org(org_id), status, reason)
	return success_response(
		data=organization_record(org), message=_("Organization status set to {0}").format(status)
	)
