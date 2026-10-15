# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Organization (NGO / FPO / Cooperative) registration, verification and filing rights.

The status is the state of the Organization Workflow (setup/install.py): the engine
decides which moves are legal and who may take them. An organization's profile is its
representative's Submitter Profile, which carries a filing-time copy of its type, name
and contacts. Whether it may file is read from the organization's status alone.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions

from oan_grievance_service.services import constants as C

DOCTYPE = "Grievance Organization"
PROFILE = "Grievance Submitter Profile"

# Changing any of these voids a verification, because it is what the administrator checked.
IDENTITY_FIELDS = ("legal_name", "org_type", "registration_number")

# The block /auth/register puts on an organization's sign-up until it registers the organization.
SIGNUP_BLOCK = "Pending admin verification"


def get_org(org_id: str):
	return frappe.get_doc(DOCTYPE, org_id)


def get_for_user(user: str):
	return frappe.get_doc(DOCTYPE, {"representative_user": user})


def create(user: str, values: dict):
	"""Register an organization In Review, with `user` as its authorized representative."""
	representative_name = values.get("representative_name") or frappe.db.get_value("User", user, "full_name")
	org = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			**values,
			"representative_user": user,
			"representative_name": representative_name or user,
		}
	)
	org.insert(ignore_permissions=True)
	return org


def update(org, values: dict):
	"""Apply a representative's edit.

	A suspended organization is read-only to its representative. Changing what the
	administrator verified sends an Active one back to In Review.
	"""
	if org.workflow_state == C.ORG_STATUS_SUSPENDED:
		frappe.throw(
			_("This organization is suspended and cannot be edited. Contact an administrator."),
			frappe.PermissionError,
		)

	changed = [
		field
		for field in IDENTITY_FIELDS
		if field in values and str(values[field]).upper() != str(org.get(field)).upper()
	]
	reverify = bool(changed) and org.workflow_state == C.ORG_STATUS_ACTIVE
	org.update(values)
	if reverify:
		org.status_reason = _("{0} changed; the organization must be verified again.").format(
			", ".join(frappe.unscrub(field) for field in changed)
		)
	org.save(ignore_permissions=True)
	if not reverify:
		return org

	# Not the representative's move to make, so it runs with the system's authority.
	user = frappe.session.user
	frappe.set_user("Administrator")  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
	try:
		return apply_workflow(org, "Request Re-verification")
	finally:
		frappe.set_user(user)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser


def set_status(org, status: str, reason: str | None = None):
	"""An administrator's decision, taken through the workflow action that leads to `status`."""
	if org.workflow_state == status:
		return org

	action = next((row.action for row in get_transitions(org) if row.next_state == status), None)
	if not action:
		frappe.throw(
			_("An organization cannot move from {0} to {1}.").format(org.workflow_state, status),
			frappe.ValidationError,
		)

	# The engine reloads the document, so the reason has to be saved before it acts.
	org.status_reason = reason
	org.save(ignore_permissions=True)
	return apply_workflow(org, action)


def sync_profile(org):
	"""Keep the representative's Submitter Profile in step with `org`.

	The profile links to the organization and takes its type, name and contact details,
	which a grievance snapshots when it is filed. An organization's sign-up block is
	lifted when the organization is registered. Any other block, an officer's for
	abuse, is left alone: nothing here decides whether the profile may file.

	A profile keyed by a phone number or an `org:` key is re-keyed by the registration
	number. One keyed by the representative's Fayda ID keeps that key.
	"""
	from oan_grievance_service.grievance_management.doctype.grievance_submitter_profile.grievance_submitter_profile import (
		split_dedupe_key,
	)
	from oan_grievance_service.services.identity import SCHEME_ORG, SCHEME_PHONE

	name = frappe.db.get_value(PROFILE, {"user": org.representative_user}, "name")
	profile = frappe.get_doc(PROFILE, name) if name else frappe.new_doc(PROFILE)

	scheme, _value = split_dedupe_key(profile.dedupe_key)
	if not profile.dedupe_key or scheme in (SCHEME_PHONE, SCHEME_ORG):
		profile.dedupe_key = f"{SCHEME_ORG}:{org.registration_number}"
		profile.flags.org_identity_sync = True

	profile.update(
		{
			"user": org.representative_user,
			"organization": org.name,
			"submitter_type": org.org_type,
			"submitter_name": org.legal_name,
			"contact_mobile": org.contact_phone,
			"contact_email": org.contact_email,
		}
	)
	if org.flags.in_insert and (profile.blocked_reason or "").startswith(SIGNUP_BLOCK):
		profile.is_blocked = 0
		profile.blocked_reason = None
	profile.save(ignore_permissions=True)


def assert_may_file(profile_name: str | None, user: str):
	"""Raise unless `user` may file a grievance as `profile_name`.

	A profile with no organization is not this module's business. One that has an
	organization may file only while that organization is Active, and only for its
	authorized representative or for an assisted filer acting on their behalf.
	"""
	if not profile_name:
		return
	org_name = frappe.db.get_value(PROFILE, profile_name, "organization")
	if not org_name:
		return

	org = frappe.db.get_value(DOCTYPE, org_name, ["workflow_state", "representative_user"], as_dict=True)
	if org.workflow_state != C.ORG_STATUS_ACTIVE:
		frappe.throw(
			_("This organization is not verified, so it cannot file grievances."),
			frappe.PermissionError,
			title=_("Organization Not Verified"),
		)
	if org.representative_user != user and not set(frappe.get_roles(user)) & C.ASSISTED_FILER_ROLES:
		frappe.throw(
			_("You are not the authorized representative of this organization."),
			frappe.PermissionError,
			title=_("Not Authorized"),
		)


def list_orgs(
	fields: list[str],
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
		filters["workflow_state"] = status
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
		fields=fields,
		order_by="creation asc, name asc" if sort == "oldest" else "creation desc, name desc",
		start=start,
		page_length=page_length,
	)
	total = frappe.get_all(
		DOCTYPE, filters=filters, or_filters=or_filters, fields=[{"COUNT": "name", "as": "total"}]
	)[0].total
	return rows, total
