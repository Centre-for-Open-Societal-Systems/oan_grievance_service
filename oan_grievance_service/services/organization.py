# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Organization (NGO / FPO / Cooperative) registration, verification and filing rights.

What belongs here: the verification lifecycle, the link between an organization and its
authorized representative's submitter profile, and the check that decides whether a
representative may file for the organization.

What does not belong here: field and format rules (the Grievance Organization
controller) and request shapes (api/v1/organization.py).

One organization has exactly one authorized representative login, and that login has
exactly one Submitter Profile. The profile is what filing resolves to, so the
organization's verification status is mirrored onto the profile's block flag. The
filing check reads the organization itself as well, so the two cannot drift apart.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service import permissions
from oan_grievance_service.services import constants as C

DOCTYPE = "Grievance Organization"
PROFILE = "Grievance Submitter Profile"

# Who may move an organization from one status to the next. The admin API is the only
# caller of every move but the last, which update() makes when a representative changes
# what the verification was about.
TRANSITIONS = {
	(C.ORG_STATUS_IN_REVIEW, C.ORG_STATUS_ACTIVE),
	(C.ORG_STATUS_IN_REVIEW, C.ORG_STATUS_SUSPENDED),
	(C.ORG_STATUS_ACTIVE, C.ORG_STATUS_SUSPENDED),
	(C.ORG_STATUS_SUSPENDED, C.ORG_STATUS_ACTIVE),
	(C.ORG_STATUS_SUSPENDED, C.ORG_STATUS_IN_REVIEW),
	(C.ORG_STATUS_ACTIVE, C.ORG_STATUS_IN_REVIEW),
}

# Statuses an administrator may set through the API. Active to In Review is automatic.
ADMIN_TARGET_STATUSES = (C.ORG_STATUS_ACTIVE, C.ORG_STATUS_SUSPENDED, C.ORG_STATUS_IN_REVIEW)

# Changing any of these voids a verification, because it is what the administrator checked.
IDENTITY_FIELDS = ("legal_name", "org_type", "registration_number")

_FPO_ENTITY_TYPES = (
	"Farmer Producer Organisation",
	"Primary Cooperative",
	"Cooperative Union",
	"Cooperative Federation",
)
# Mirrors the choices on the STG-450 registration screen.
ENTITY_TYPES = {
	"NGO": (
		"Local NGO",
		"International NGO",
		"Community-Based Organisation",
		"Civil Society Organisation",
	),
	"FPO": _FPO_ENTITY_TYPES,
	"Cooperative": _FPO_ENTITY_TYPES,
}

# Fields a representative may send. Everything else is server-owned.
PROFILE_FIELDS = (
	"legal_name",
	"brand_name",
	"org_type",
	"entity_type",
	"registration_number",
	"representative_name",
	"contact_email",
	"contact_phone",
	"website",
	"street_address",
	"kebele",
	"woreda",
	"zone",
	"region",
	"country",
	"postal_code",
)

RECORD_FIELDS = [
	"name",
	"legal_name",
	"brand_name",
	"org_type",
	"entity_type",
	"registration_number",
	"representative_user",
	"representative_name",
	"contact_email",
	"contact_phone",
	"website",
	"street_address",
	"kebele",
	"woreda",
	"zone",
	"region",
	"country",
	"postal_code",
	"verification_status",
	"status_reason",
	"verified_by",
	"verified_on",
	"creation",
	"modified",
]

# Prefix that marks a profile block as ours, so verifying an organization never lifts a
# block an officer placed for another reason.
BLOCK_PREFIX = "Organization"


def normalise_registration_number(value: str | None) -> str:
	return (value or "").strip().upper()


def get_org(org_id: str):
	"""The organization `org_id`, or a 404."""
	if not frappe.db.exists(DOCTYPE, org_id):
		frappe.throw(
			_("Organization '{0}' was not found.").format(org_id),
			frappe.DoesNotExistError,
			title=_("Not Found"),
		)
	return frappe.get_doc(DOCTYPE, org_id)


def get_for_user(user: str):
	"""The organization `user` is the authorized representative of, or None."""
	name = frappe.db.get_value(DOCTYPE, {"representative_user": user}, "name")
	return frappe.get_doc(DOCTYPE, name) if name else None


def require_for_user(user: str):
	org = get_for_user(user)
	if not org:
		frappe.throw(
			_("No organization is registered for your account."),
			frappe.DoesNotExistError,
			title=_("Not Found"),
		)
	return org


def create(user: str, values: dict):
	"""Register an organization with `user` as its authorized representative.

	It starts In Review and its representative's profile is blocked until an
	administrator activates it.
	"""
	if get_for_user(user):
		frappe.throw(
			_("Your account already represents a registered organization."),
			frappe.DuplicateEntryError,
			title=_("Already Registered"),
		)

	registration_number = normalise_registration_number(values.get("registration_number"))
	holder = frappe.db.get_value(PROFILE, {"dedupe_key": f"org:{registration_number}"}, "user")
	if frappe.db.exists(DOCTYPE, {"registration_number": registration_number}) or (holder and holder != user):
		frappe.throw(
			_("An organization with registration number '{0}' is already registered.").format(
				registration_number
			),
			frappe.DuplicateEntryError,
			title=_("Already Registered"),
		)

	values = {key: values.get(key) for key in PROFILE_FIELDS if values.get(key) is not None}
	if not values.get("representative_name"):
		values["representative_name"] = frappe.db.get_value("User", user, "full_name") or user

	org = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			**values,
			"representative_user": user,
			"verification_status": C.ORG_STATUS_IN_REVIEW,
		}
	)
	org.insert(ignore_permissions=True)
	return org


def update(org, values: dict):
	"""Apply a representative's edit.

	A suspended organization is read-only to its representative. Changing the name, type
	or registration number of an Active one sends it back to In Review: the new details
	were never checked, so the verification no longer covers them.
	"""
	if org.verification_status == C.ORG_STATUS_SUSPENDED:
		frappe.throw(
			_("This organization is suspended and cannot be edited. Contact an administrator."),
			frappe.PermissionError,
			title=_("Organization Suspended"),
		)

	values = {key: values[key] for key in PROFILE_FIELDS if key in values}
	if "registration_number" in values:
		values["registration_number"] = normalise_registration_number(values["registration_number"])
		taken = frappe.db.get_value(
			DOCTYPE, {"registration_number": values["registration_number"], "name": ["!=", org.name]}
		)
		if taken:
			frappe.throw(
				_("An organization with registration number '{0}' is already registered.").format(
					values["registration_number"]
				),
				frappe.DuplicateEntryError,
				title=_("Already Registered"),
			)

	changed_identity = [
		field for field in IDENTITY_FIELDS if field in values and values[field] != org.get(field)
	]
	org.update(values)
	if changed_identity and org.verification_status == C.ORG_STATUS_ACTIVE:
		org.verification_status = C.ORG_STATUS_IN_REVIEW
		org.status_reason = _("{0} changed; the organization must be verified again.").format(
			", ".join(frappe.unscrub(field) for field in changed_identity)
		)
	org.save(ignore_permissions=True)
	return org


def set_status(org, status: str, reason: str | None = None):
	"""An administrator's decision. The controller enforces the transition table."""
	if status not in ADMIN_TARGET_STATUSES:
		frappe.throw(
			_("Status must be one of: {0}.").format(", ".join(ADMIN_TARGET_STATUSES)),
			frappe.ValidationError,
			title=_("Invalid Status"),
		)
	org.verification_status = status
	org.status_reason = (reason or "").strip() or None
	org.save(ignore_permissions=True)
	return org


def validate_transition(before: str | None, after: str, reason: str | None):
	"""Raise unless `before` -> `after` is a legal move; used by the controller."""
	if before is None:
		if after != C.ORG_STATUS_IN_REVIEW:
			frappe.throw(
				_("A new organization starts {0}.").format(C.ORG_STATUS_IN_REVIEW),
				frappe.ValidationError,
				title=_("Invalid Status"),
			)
		return
	if before == after:
		return
	if (before, after) not in TRANSITIONS:
		frappe.throw(
			_("An organization cannot move from {0} to {1}.").format(before, after),
			frappe.ValidationError,
			title=_("Invalid Status Change"),
		)
	if after == C.ORG_STATUS_SUSPENDED and not (reason or "").strip():
		frappe.throw(
			_("A reason is required to suspend an organization."),
			frappe.ValidationError,
			title=_("Reason Required"),
		)


def sync_profile(org):
	"""Make the representative's Submitter Profile reflect `org`.

	Run after every save. The profile takes the organization's type, name and contact
	details, links to it, and is blocked unless the organization is Active. A profile
	anchored to a phone number is re-anchored to the registration number. A profile
	anchored to a Fayda ID keeps that key: the identity is the person's, and the
	organization is reached through the link.
	"""
	from oan_grievance_service.grievance_management.doctype.grievance_submitter_profile.grievance_submitter_profile import (
		split_dedupe_key,
	)
	from oan_grievance_service.services.identity import SCHEME_ORG, SCHEME_PHONE

	name = frappe.db.get_value(PROFILE, {"user": org.representative_user}, "name")
	profile = frappe.get_doc(PROFILE, name) if name else frappe.new_doc(PROFILE)

	org_key = f"{SCHEME_ORG}:{org.registration_number}"
	scheme, _value = split_dedupe_key(profile.dedupe_key)
	if not profile.dedupe_key or scheme in (SCHEME_PHONE, SCHEME_ORG):
		profile.dedupe_key = org_key
		profile.flags.org_identity_sync = True

	profile.user = org.representative_user
	profile.organization = org.name
	profile.submitter_type = org.org_type
	profile.submitter_name = org.legal_name
	profile.contact_mobile = org.contact_phone
	profile.contact_email = org.contact_email
	profile.active = 1

	if org.verification_status == C.ORG_STATUS_ACTIVE:
		if profile.is_blocked and (profile.blocked_reason or "").startswith(BLOCK_PREFIX):
			profile.is_blocked = 0
			profile.blocked_reason = None
	else:
		profile.is_blocked = 1
		profile.blocked_reason = _("Organization is {0}: {1}").format(
			org.verification_status, org.status_reason or _("pending verification.")
		)
	profile.save(ignore_permissions=True)


def assert_may_file(profile_name: str | None, user: str):
	"""Raise unless `user` may file a grievance as `profile_name`.

	A profile with no organization is not this module's business. One that has an
	organization may file only while that organization is Active, and only for its
	authorized representative or for staff filing on a caller's behalf.
	"""
	if not profile_name:
		return
	org_name = frappe.db.get_value(PROFILE, profile_name, "organization")
	if not org_name:
		return

	org = frappe.db.get_value(DOCTYPE, org_name, ["verification_status", "representative_user"], as_dict=True)
	if not org or org.verification_status != C.ORG_STATUS_ACTIVE:
		frappe.throw(
			_("This organization is not verified, so it cannot file grievances."),
			frappe.PermissionError,
			title=_("Organization Not Verified"),
		)
	if org.representative_user != user and not permissions.is_staff(user):
		frappe.throw(
			_("You are not the authorized representative of this organization."),
			frappe.PermissionError,
			title=_("Not Authorized"),
		)


def assert_can_unblock(profile):
	"""Raise if lifting `profile`'s block would skip organization verification."""
	if not profile.get("organization"):
		return
	status = frappe.db.get_value(DOCTYPE, profile.organization, "verification_status")
	if status != C.ORG_STATUS_ACTIVE:
		frappe.throw(
			_(
				"This profile belongs to an organization that is {0}. Activate the organization instead."
			).format(status or _("missing")),
			frappe.PermissionError,
			title=_("Organization Not Verified"),
		)


def profile_details(org_name: str | None) -> dict:
	"""What `GET /auth/me` adds to a representative's grievance profile.

	The profile holds a filing-time copy of the organization's name and contact details,
	so everything else about the organization (what it is, its registration number, who
	represents it, whether it may file) comes from here. Empty for a profile that is not
	an organization's.
	"""
	if not org_name:
		return {}
	row = frappe.db.get_value(
		DOCTYPE,
		org_name,
		[
			"name",
			"legal_name",
			"brand_name",
			"org_type",
			"entity_type",
			"registration_number",
			"representative_name",
			"verification_status",
			"status_reason",
		],
		as_dict=True,
	)
	if not row:
		return {}
	return {
		"registration_number": row.registration_number,
		"representative_name": row.representative_name,
		"organization": {
			"id": row.name,
			"legal_name": row.legal_name,
			"brand_name": row.brand_name,
			"org_type": row.org_type,
			"entity_type": row.entity_type,
			"registration_number": row.registration_number,
			"representative_name": row.representative_name,
			"status": row.verification_status,
			"status_reason": row.status_reason,
			"can_file": row.verification_status == C.ORG_STATUS_ACTIVE,
		},
	}


def record(row) -> dict:
	"""An API record for an organization row or document."""
	get = row.get
	data = {field: get(field) for field in RECORD_FIELDS}
	data["status"] = data.pop("verification_status")
	for field in ("verified_on", "creation", "modified"):
		value = data[field]
		data[field] = value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)
	return data


def list_orgs(
	status: str | None = None,
	org_type: str | None = None,
	search: str | None = None,
	sort: str = "newest",
	start: int = 0,
	page_length: int = 20,
):
	"""One page of organizations and the total, filtered and ordered in SQL."""
	filters = {}
	if status:
		filters["verification_status"] = status
	if org_type:
		filters["org_type"] = org_type
	or_filters = None
	if search and search.strip():
		like = f"%{search.strip()}%"
		or_filters = [
			["legal_name", "like", like],
			["brand_name", "like", like],
			["registration_number", "like", like],
		]

	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=RECORD_FIELDS,
		order_by="creation asc, name asc" if sort == "oldest" else "creation desc, name desc",
		start=start,
		page_length=page_length,
	)
	total = frappe.get_all(
		DOCTYPE, filters=filters, or_filters=or_filters, fields=[{"COUNT": "name", "as": "total"}]
	)[0].total
	return [record(row) for row in rows], total


def stamp_decision(org):
	"""Record who decided and when. Called by the controller when the status changes."""
	org.verified_by = frappe.session.user
	org.verified_on = now_datetime()
