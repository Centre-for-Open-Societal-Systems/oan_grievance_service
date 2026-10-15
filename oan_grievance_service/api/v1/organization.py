# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Organization (NGO / FPO / Cooperative) registration and verification endpoints.

A representative registers one organization for their login, keeps its profile up to
date and reads its verification status (`/orgs`, `/orgs/me`). Administrators work a
queue of registrations and decide each one (`/orgs`, `/orgs/<org_id>`,
`/orgs/<org_id>/status`). The organization is In Review until an administrator
activates it; only then may its representative file grievances.

Handlers stay thin. Field and format rules live in the Grievance Organization
controller, and the lifecycle and the link to the representative's submitter profile live
in `services/organization.py`.
"""

import re
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
from pydantic import AliasChoices, BaseModel, Field, StringConstraints, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import organization as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES, ADMIN_ROLES, ROLE_SUBMITTER
from oan_grievance_service.services.identity import REGISTRATION_PATTERN

route = prefixed("/api/v1/orgs")

OrgType = Literal["NGO", "FPO", "Cooperative"]
OrgStatus = Literal["In Review", "Active", "Suspended"]

# Frappe's Data length, which is what the columns hold.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=140)]

POSTAL_CODE_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9]|[ -](?=[A-Za-z0-9])){1,8}[A-Za-z0-9]$"
WEBSITE_MAX_LENGTH = 255
MEMBERS_MAX = 100_000_000

# What the registration screen sends the registration number as. The canonical name is
# registration_number, which is also what the profile and the docs call it.
REGISTRATION_ALIASES = AliasChoices("registration_number", "tax_registration_number")


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
	consent_given: bool = False
	consent_recorded_at: str | None = None
	status: str
	status_reason: str | None = None
	verified_by: str | None = None
	verified_on: str | None = None
	creation: str | None = None
	modified: str | None = None


class OrganizationListData(BaseModel):
	organizations: list[OrganizationRecord]
	pagination: dict


def _check_registration_number(value: str | None) -> str | None:
	if value is not None and not REGISTRATION_PATTERN.match(value):
		raise ValueError("Registration number is not in a valid format.")
	return value.upper() if value else value


def _check_consent(value: bool) -> bool:
	if value is not True:
		raise ValueError("Confirm you are authorised to register this organization.")
	return value


def _check_website(value: str | None) -> str | None:
	"""A web address with a dotted host. A bare "example.org" is taken as https://example.org."""
	if value is None:
		return value
	value = value.strip()
	if value and not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
		value = f"https://{value}"
	if len(value) > WEBSITE_MAX_LENGTH or not re.match(r"^https?://[^\s/.]+(\.[^\s/.]+)+(/\S*)?$", value):
		raise ValueError("Website must be a valid http or https address.")
	return value


def _check_postal_code(value: str | None) -> str | None:
	if value is not None and not re.match(POSTAL_CODE_PATTERN, value):
		raise ValueError("Postal code is not valid.")
	return value


class CreateOrganization(Body):
	"""Everything the registration screen collects. Status and verification fields are not accepted."""

	legal_name: Text
	brand_name: Text | None = None
	org_type: OrgType
	entity_type: Text
	registration_number: Text = Field(
		validation_alias=REGISTRATION_ALIASES,
		description="Registration / licence number. Also accepted as tax_registration_number.",
	)
	representative_name: Text | None = None
	members_affected: int | None = Field(
		default=None,
		ge=0,
		le=MEMBERS_MAX,
		description="Members affected (Cooperative / FPO). Optional; 0 when not given.",
	)
	contact_email: SafeEmail
	contact_phone: Text
	website: str | None = None
	street_address: Text
	kebele: Text | None = None
	woreda: Text | None = None
	zone: Text
	region: Text
	country: Text
	postal_code: Text
	consent_given: bool = Field(
		description="The representative confirms they are authorised to register the organization and "
		"agrees to the Privacy Policy. Must be true."
	)

	_blank = field_validator(
		"brand_name",
		"representative_name",
		"members_affected",
		"website",
		"kebele",
		"woreda",
		mode="before",
	)(blank_to_none)
	_registration = field_validator("registration_number")(_check_registration_number)
	_website = field_validator("website")(_check_website)
	_postal = field_validator("postal_code")(_check_postal_code)
	_consent = field_validator("consent_given")(_check_consent)


class UpdateOrganization(Body):
	"""Partial update. Omitted fields stay as they are; the optional ones accept null to clear."""

	legal_name: Text = None
	brand_name: Text | None = None
	org_type: OrgType = None
	entity_type: Text = None
	registration_number: Text = Field(
		default=None,
		validation_alias=REGISTRATION_ALIASES,
		description="Registration / licence number. Also accepted as tax_registration_number.",
	)
	representative_name: Text | None = None
	members_affected: int | None = Field(
		default=None,
		ge=0,
		le=MEMBERS_MAX,
		description="Members affected (Cooperative / FPO). Optional; 0 when not given.",
	)
	contact_email: SafeEmail = None
	contact_phone: Text = None
	website: str | None = None
	street_address: Text = None
	kebele: Text | None = None
	woreda: Text | None = None
	zone: Text = None
	region: Text = None
	country: Text = None
	postal_code: Text = None

	_blank = field_validator(
		"brand_name",
		"representative_name",
		"members_affected",
		"website",
		"kebele",
		"woreda",
		mode="before",
	)(blank_to_none)
	_registration = field_validator("registration_number")(_check_registration_number)
	_website = field_validator("website")(_check_website)
	_postal = field_validator("postal_code")(_check_postal_code)


class OrganizationRef(Body):
	org_id: NonBlank


class SetOrganizationStatus(Body):
	org_id: NonBlank
	status: OrgStatus
	reason: str | None = Field(default=None, max_length=500)

	_blank = field_validator("reason", mode="before")(blank_to_none)


class ListOrganizations(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	status: OrgStatus | None = None
	org_type: OrgType | None = None
	search: str | None = Field(default=None, max_length=100)
	sort: Literal["newest", "oldest"] = "newest"

	_blank = field_validator("status", "org_type", "search", mode="before")(blank_to_none)


@route("", methods=("POST",), summary="Register an organization")
@handle_api_errors
@require_role([ROLE_SUBMITTER])
@validate_request(CreateOrganization)
@api_doc(
	summary="Register an organization",
	description="Register an NGO, FPO or cooperative with the caller as its authorized "
	+ "representative. It starts In Review and cannot file grievances until an administrator "
	+ "sets it Active. One organization per account, and one account per registration number. "
	+ "The registration number may also be sent as tax_registration_number.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def register_organization(
	legal_name: str,
	org_type: str,
	entity_type: str,
	registration_number: str,
	contact_email: str,
	contact_phone: str,
	street_address: str,
	zone: str,
	region: str,
	country: str,
	postal_code: str,
	consent_given: bool | str | None,
	brand_name: str | None = None,
	representative_name: str | None = None,
	members_affected: int | str | None = None,
	website: str | None = None,
	kebele: str | None = None,
	woreda: str | None = None,
	**kwargs,
):
	"""Create the caller's organization In Review and block its profile until it is verified."""
	org = service.create(
		frappe.session.user,
		{
			"legal_name": legal_name,
			"org_type": org_type,
			"entity_type": entity_type,
			"registration_number": registration_number,
			"contact_email": contact_email,
			"contact_phone": contact_phone,
			"street_address": street_address,
			"zone": zone,
			"region": region,
			"country": country,
			"postal_code": postal_code,
			"consent_given": consent_given,
			"brand_name": brand_name,
			"representative_name": representative_name,
			"members_affected": members_affected,
			"website": website,
			"kebele": kebele,
			"woreda": woreda,
		},
	)
	return success_response(
		data=service.record(org),
		message=_("Organization registered and awaiting verification"),
	)


@route("/me", methods=("GET",), summary="Get my organization")
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
	"""Return the caller's own organization."""
	org = service.require_for_user(frappe.session.user)
	return success_response(data=service.record(org), message=_("Organization retrieved"))


@route("/me", methods=("PATCH",), summary="Update my organization")
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
	"""Update the caller's organization. `kwargs` holds only the fields the client sent."""
	org = service.require_for_user(frappe.session.user)
	if not kwargs:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	service.update(org, kwargs)
	return success_response(data=service.record(org), message=_("Organization updated"))


@route("", methods=("GET",), summary="List organizations")
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
def list_organizations(
	status: str | None = None,
	org_type: str | None = None,
	search: str | None = None,
	sort: str = "newest",
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""One page of organizations, filtered, ordered and paged in SQL."""
	params = PageParams(page=page, page_size=page_size)
	rows, total = service.list_orgs(
		status=status,
		org_type=org_type,
		search=search,
		sort=sort,
		start=params.start,
		page_length=params.page_size,
	)
	return success_response(
		data={
			"organizations": rows,
			"pagination": page_meta(total, params.page, params.page_size),
		},
		message=_("Organizations retrieved"),
	)


@route("/<org_id>", methods=("GET",), summary="Get an organization")
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
	"""Return one organization by id."""
	return success_response(data=service.record(service.get_org(org_id)), message=_("Organization retrieved"))


@route("/<org_id>/status", methods=("PATCH",), summary="Set an organization's verification status")
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(SetOrganizationStatus)
@api_doc(
	summary="Set an organization's verification status",
	description="The administrator's decision. In Review to Active verifies it and lets its "
	+ "representative file grievances. Active or In Review to Suspended withdraws or rejects "
	+ "it and needs a reason. Suspended to Active reinstates it, and Suspended to In Review "
	+ "reopens the review. Other moves are refused. Review Officers cannot decide.",
	tags=["Organizations"],
	response_model=OrganizationRecord,
)
def set_organization_status(org_id: str, status: str, reason: str | None = None, **kwargs):
	"""Move an organization to `status`, recording who decided and why."""
	org = service.get_org(org_id)
	service.set_status(org, status, reason)
	return success_response(
		data=service.record(org),
		message=_("Organization status set to {0}").format(status),
	)
