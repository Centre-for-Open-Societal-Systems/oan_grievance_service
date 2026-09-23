# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Save and resume a partially completed submission directly on Grievance doctype.

A draft is persisted directly as a Grievance with status 'Draft' and workflow_state 'Draft'.
Full submission validation runs when the case is formally submitted.
"""

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	SafeEmail,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, model_validator

DRAFT_LIFETIME_DAYS = 30

route = prefixed("/api/v1/drafts")

ALLOWED_DRAFT_ROLES = [
	"Grievance Submitter",
	"Grievance Officer",
	"Grievance Admin",
	"System Manager",
	"Administrator",
]


class SaveDraftRequest(BaseModel):
	"""Partial grievance state for saving a draft."""

	model_config = {"extra": "forbid"}

	client_submission_uuid: str | None = Field(
		None, min_length=1, description="Stable client-generated draft key"
	)
	client_uuid: str | None = Field(None, min_length=1, description="Alias for client_submission_uuid")
	submission_channel: str | None = None
	submitter_type: str | None = None
	submitter_name: str | None = None
	contact_mobile: str | None = None
	contact_email: SafeEmail | None = None
	administrative_area: str | None = None
	administrative_unit: str | None = None
	service_category: str | None = None
	grievance_type: str | None = None
	associated_service_provider: str | None = None
	description: str | None = None
	desired_outcome: str | None = None
	is_anonymous: int | None = Field(None, ge=0, le=1)
	validate: bool | int | None = Field(False, description="If true, execute validation on the draft payload")

	@model_validator(mode="after")
	def validate_uuids(self):
		if not self.client_submission_uuid and not self.client_uuid:
			raise ValueError(_("client_submission_uuid or client_uuid is required"))
		if not self.client_submission_uuid:
			self.client_submission_uuid = self.client_uuid
		return self


@route("", methods=("POST",), summary="Save a grievance draft")
@frappe.whitelist()
@validate_request(SaveDraftRequest)
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def save(
	client_submission_uuid: str | None = None,
	client_uuid: str | None = None,
	submission_channel: str | None = None,
	submitter_type: str | None = None,
	submitter_name: str | None = None,
	contact_mobile: str | None = None,
	contact_email: str | None = None,
	administrative_area: str | None = None,
	administrative_unit: str | None = None,
	service_category: str | None = None,
	grievance_type: str | None = None,
	associated_service_provider: str | None = None,
	description: str | None = None,
	desired_outcome: str | None = None,
	is_anonymous: int | None = None,
	validate: bool | int = False,
	**kwargs,
):
	"""Create or overwrite a draft directly on Grievance doctype with status='Draft'."""
	client_submission_uuid = client_submission_uuid or client_uuid
	if not client_submission_uuid:
		frappe.throw(_("client_submission_uuid or client_uuid is required."), frappe.ValidationError)

	session_user = _session_user()
	if not session_user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	name = frappe.db.get_value("Grievance", {"client_submission_uuid": client_submission_uuid}, "name")

	if name:
		doc = frappe.get_doc("Grievance", name)
		_assert_owner(doc, session_user)
		if doc.workflow_state != "Draft" and doc.docstatus != 0:
			frappe.throw(
				_("This draft has already been submitted as {0}.").format(doc.ticket_number or doc.name),
				title=_("Already Submitted"),
			)
		if not doc.owner:
			doc.owner = session_user
	else:
		doc = frappe.new_doc("Grievance")
		doc.client_submission_uuid = client_submission_uuid
		doc.owner = session_user
		doc.workflow_state = "Draft"
		doc.status = "Draft"
		doc.docstatus = 0

	# Associate submitter profile if available
	if not doc.submitter:
		try:
			from oan_grievance_service.api.v1.grievance import _resolve_submitter_identity

			ident = _resolve_submitter_identity({})
			if ident.get("submitter"):
				doc.submitter = ident["submitter"]
				doc.submitter_type = ident.get("submitter_type") or doc.submitter_type
				doc.submitter_name = ident.get("submitter_name") or doc.submitter_name
				doc.contact_mobile = ident.get("contact_mobile") or doc.contact_mobile
				doc.contact_email = ident.get("contact_email") or doc.contact_email
		except Exception:
			pass

	# If no profile was resolved, allow client-supplied contact details
	if not doc.submitter:
		if submitter_name is not None:
			doc.submitter_name = submitter_name
		if contact_mobile is not None:
			doc.contact_mobile = contact_mobile
		if contact_email is not None:
			doc.contact_email = contact_email
		if submitter_type is not None:
			doc.submitter_type = submitter_type

	if submission_channel is not None:
		doc.submission_channel = submission_channel
	elif not doc.submission_channel:
		doc.submission_channel = "Web Portal"

	if not doc.submitter_type:
		doc.submitter_type = "Individual Farmer"

	if administrative_area is not None:
		from oan_grievance_service.api.v1.grievance import resolve_administrative_area

		resolved_area = resolve_administrative_area(administrative_area) if administrative_area else None
		doc.administrative_area = resolved_area or administrative_area
	if administrative_unit is not None:
		doc.administrative_unit = administrative_unit
	if service_category is not None:
		doc.service_category = service_category
	if grievance_type is not None:
		doc.grievance_type = grievance_type
	if associated_service_provider is not None:
		doc.associated_service_provider = associated_service_provider
	if description is not None:
		doc.description = description
	if desired_outcome is not None:
		doc.desired_outcome = desired_outcome
	if is_anonymous is not None:
		doc.is_anonymous = 1 if is_anonymous else 0

	if validate:
		if doc.contact_mobile:
			from oan_grievance_service.services import identity

			doc.contact_mobile = identity.validate_mobile(doc.contact_mobile)

		from oan_grievance_service.services import identity

		identity.validate_submission_payload(doc.as_dict())

	doc.flags.ignore_mandatory = True
	doc.flags.is_draft_wizard = True
	doc.save(ignore_permissions=True)

	return success_response(data=_draft_state(doc), message=_("Draft saved"))


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def save_draft(**kwargs):
	return save(**kwargs)


@route("", methods=("GET",), summary="Get the authenticated user's latest grievance draft")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def load():
	"""Return the caller's latest unsubmitted draft."""
	user = _session_user()
	if not user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	name = _latest_own_draft_name(user)
	if not name:
		frappe.throw(_("No saved draft found."), frappe.DoesNotExistError, title=_("Not Found"))

	doc = frappe.get_doc("Grievance", name)
	_assert_owner(doc, user)

	return success_response(data=_draft_state(doc), message=_("Draft loaded"))


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def get_draft():
	return load()


class SubmitDraftRequest(BaseModel):
	model_config = {"extra": "forbid"}

	client_submission_uuid: str = Field(
		..., min_length=1, description="Stable client-generated draft key to submit"
	)
	consent_given: int | bool = 1
	is_anonymous: int | bool = 0
	anonymity_justification: str | None = None
	submission_channel: str | None = None
	submitter_type: str | None = None
	submitter_name: str | None = None
	contact_mobile: str | None = None
	contact_email: SafeEmail | None = None
	administrative_area: str | None = None
	administrative_unit: str | None = None
	service_category: str | None = None
	grievance_type: str | None = None
	associated_service_provider: str | None = None
	description: str | None = None
	desired_outcome: str | None = None


@route("/submit", methods=("POST",), summary="Submit a draft grievance into an active case")
@frappe.whitelist()
@validate_request(SubmitDraftRequest)
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def submit_draft(
	client_submission_uuid: str,
	consent_given: int | bool = 1,
	is_anonymous: int | bool = 0,
	anonymity_justification: str | None = None,
	submission_channel: str | None = None,
	submitter_type: str | None = None,
	submitter_name: str | None = None,
	contact_mobile: str | None = None,
	contact_email: str | None = None,
	administrative_area: str | None = None,
	administrative_unit: str | None = None,
	service_category: str | None = None,
	grievance_type: str | None = None,
	associated_service_provider: str | None = None,
	description: str | None = None,
	desired_outcome: str | None = None,
):
	"""Submit an existing draft grievance, transitioning its status to Submitted."""
	session_user = _session_user()
	if not session_user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	name = frappe.db.get_value("Grievance", {"client_submission_uuid": client_submission_uuid}, "name")
	if not name:
		frappe.throw(_("No saved draft found to submit."), frappe.DoesNotExistError, title=_("Not Found"))

	doc = frappe.get_doc("Grievance", name)
	_assert_owner(doc, session_user)
	if doc.workflow_state != "Draft" and doc.docstatus != 0:
		frappe.throw(
			_("This draft has already been submitted as {0}.").format(doc.ticket_number or doc.name),
			title=_("Already Submitted"),
		)

	if submission_channel:
		doc.submission_channel = submission_channel
	if submitter_type:
		doc.submitter_type = submitter_type
	if submitter_name:
		doc.submitter_name = submitter_name
	if contact_mobile:
		doc.contact_mobile = contact_mobile
	if contact_email:
		doc.contact_email = contact_email
	if administrative_area:
		from oan_grievance_service.api.v1.grievance import resolve_administrative_area

		resolved_area = resolve_administrative_area(administrative_area)
		doc.administrative_area = resolved_area or administrative_area
	if administrative_unit:
		doc.administrative_unit = administrative_unit
	if service_category:
		doc.service_category = service_category
	if grievance_type:
		doc.grievance_type = grievance_type
	if associated_service_provider:
		doc.associated_service_provider = associated_service_provider
	if description:
		doc.description = description
	if desired_outcome:
		doc.desired_outcome = desired_outcome

	doc.consent_given = 1 if consent_given else 0
	if not doc.consent_given:
		frappe.throw(
			_("The submitter must consent to the processing of their personal data."),
			title=_("Consent Required"),
		)
	if not doc.consent_recorded_at:
		doc.consent_recorded_at = now_datetime()

	if not doc.submission_channel:
		doc.submission_channel = "Web Portal"

	if not doc.submitter:
		from oan_grievance_service.api.v1.grievance import _resolve_submitter_identity
		from oan_grievance_service.services.identity import find_or_create_submitter

		try:
			ident = _resolve_submitter_identity(doc.as_dict())
		except Exception:
			ident = {}

		if ident.get("submitter"):
			doc.submitter = ident["submitter"]
			doc.submitter_type = ident.get("submitter_type") or doc.submitter_type
			doc.submitter_name = ident.get("submitter_name") or doc.submitter_name
			doc.contact_mobile = ident.get("contact_mobile") or doc.contact_mobile
			doc.contact_email = ident.get("contact_email") or doc.contact_email
			if ident.get("assisted_by_officer"):
				doc.assisted_by_officer = ident.get("assisted_by_officer")
		else:
			doc.submitter = find_or_create_submitter(doc.as_dict())

	if doc.submitter:
		profile = frappe.db.get_value(
			"Grievance Submitter Profile",
			doc.submitter,
			["submitter_type", "submitter_name", "contact_mobile", "contact_email"],
			as_dict=True,
		)
		if profile:
			doc.submitter_type = doc.submitter_type or profile.submitter_type
			doc.submitter_name = doc.submitter_name or profile.submitter_name
			doc.contact_mobile = doc.contact_mobile or profile.contact_mobile
			doc.contact_email = doc.contact_email or profile.contact_email

	if doc.contact_mobile:
		from oan_grievance_service.services import identity

		doc.contact_mobile = identity.validate_mobile(doc.contact_mobile)

	from oan_grievance_service.services import identity

	identity.validate_submission_payload(doc.as_dict())

	doc.flags.in_submit = True
	doc.save(ignore_permissions=True)

	from oan_grievance_service.services import ticket_number as tn

	if doc.name.startswith("DRAFT-") or not doc.ticket_number:
		t_num = tn.generate(doc.administrative_area, doc.service_category)
		frappe.rename_doc("Grievance", doc.name, t_num, force=True)
		doc = frappe.get_doc("Grievance", t_num)
		doc.flags.in_submit = True
	doc.ticket_number = doc.name
	doc.save(ignore_permissions=True)

	from oan_grievance_service.api.v1.grievance import _request_anonymity, detect_duplicates
	from oan_grievance_service.services import constants as C
	from oan_grievance_service.services import lifecycle, notifications, routing

	lifecycle.transition(doc, "Submit")

	if is_anonymous or doc.is_anonymous:
		doc.is_anonymous = 1
		_request_anonymity(doc, anonymity_justification)

	duplicates = detect_duplicates(doc)
	notifications.queue(doc, C.EVENT_SUBMISSION_RECEIVED)
	if duplicates:
		notifications.queue(doc, C.EVENT_DUPLICATE_DETECTED)
	rule = routing.apply_routing(doc)
	doc.reload()

	return success_response(
		data={
			"ticket_number": doc.ticket_number,
			"status": doc.status,
			"workflow_state": doc.workflow_state,
			"client_submission_uuid": doc.client_submission_uuid,
			"routing_rule": rule.name if hasattr(rule, "name") else str(rule) if rule else None,
		},
		message=_("Grievance submitted successfully"),
	)


class DiscardDraftRequest(BaseModel):
	model_config = {"extra": "forbid"}

	client_submission_uuid: str = Field(
		..., min_length=1, description="Stable client-generated draft key to discard"
	)


@route("", methods=("DELETE",), summary="Discard a grievance draft")
@frappe.whitelist()
@validate_request(DiscardDraftRequest)
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def discard(client_submission_uuid: str):
	"""Delete a draft the submitter abandoned."""
	session_user = _session_user()
	if not session_user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	name = frappe.db.get_value("Grievance", {"client_submission_uuid": client_submission_uuid}, "name")
	if not name:
		return success_response(data={"discarded": False}, message=_("No draft to discard"))

	doc = frappe.get_doc("Grievance", name)
	_assert_owner(doc, session_user)
	if doc.workflow_state != "Draft" and doc.docstatus != 0:
		frappe.throw(
			_("This draft became grievance {0} and cannot be discarded.").format(
				doc.ticket_number or doc.name
			),
			title=_("Already Submitted"),
		)

	_purge_draft_uploads(doc.name)
	frappe.delete_doc("Grievance", doc.name, force=True, ignore_permissions=True)
	return success_response(data={"discarded": True}, message=_("Draft discarded"))


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def delete_draft(client_submission_uuid: str):
	return discard(client_submission_uuid=client_submission_uuid)


def purge_expired_drafts():
	"""Daily: clear abandoned drafts that were never submitted (older than 30 days)."""
	cutoff = add_days(now_datetime(), -DRAFT_LIFETIME_DAYS)
	stale = frappe.get_all(
		"Grievance",
		filters={
			"workflow_state": "Draft",
			"docstatus": 0,
			"creation": ["<", cutoff],
		},
		pluck="name",
	)
	for name in stale:
		_purge_draft_uploads(name)
		frappe.delete_doc("Grievance", name, force=True, ignore_permissions=True)
	return len(stale)


def _purge_draft_uploads(grievance_name):
	"""Delete uploads linked to an abandoned draft."""
	rows = frappe.get_all(
		"Grievance Attachment",
		filters={"grievance": grievance_name},
		fields=["name", "file_url"],
	)
	for row in rows:
		file_name = frappe.db.get_value("File", {"file_url": row.file_url}, "name")
		if file_name:
			frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
		frappe.delete_doc("Grievance Attachment", row.name, force=True, ignore_permissions=True)

	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Grievance", "attached_to_name": grievance_name},
		fields=["name"],
	)
	for f in files:
		frappe.delete_doc("File", f.name, force=True, ignore_permissions=True)


def _session_user():
	user = frappe.session.user
	return None if user in ("Guest", None) else user


def _assert_owner(doc, session_user):
	"""A draft claimed by a signed-in user stays with that user."""
	if not doc.owner:
		return
	if not session_user or session_user == doc.owner:
		return
	frappe.throw(
		_("This draft belongs to another user."),
		frappe.PermissionError,
		title=_("Forbidden"),
	)


def _latest_own_draft_name(user):
	"""The caller's newest unsubmitted draft, or None."""
	return frappe.db.get_value(
		"Grievance",
		filters={"owner": user, "workflow_state": "Draft", "docstatus": 0},
		fieldname="name",
		order_by="modified desc",
	)


def _draft_state(doc):
	"""Draft document attributes returned directly."""
	from oan_grievance_service.api.v1.administrative_area import (
		format_administrative_location,
		get_administrative_hierarchy,
	)

	attachments = _attachments(doc.name)
	hierarchy = get_administrative_hierarchy(doc.administrative_area)
	location_str = format_administrative_location(hierarchy)

	return {
		"name": doc.name,
		"ticket_number": doc.ticket_number,
		"client_submission_uuid": doc.client_submission_uuid,
		"status": doc.status,
		"workflow_state": doc.workflow_state,
		"submission_channel": doc.submission_channel,
		"submitter_type": doc.submitter_type,
		"submitter_name": doc.submitter_name,
		"contact_mobile": doc.contact_mobile,
		"contact_email": doc.contact_email,
		"administrative_area": doc.administrative_area,
		"administrative_hierarchy": hierarchy,
		"location": location_str,
		"administrative_unit": doc.administrative_unit,
		"service_category": doc.service_category,
		"grievance_type": doc.grievance_type,
		"associated_service_provider": doc.associated_service_provider,
		"description": doc.description,
		"desired_outcome": doc.desired_outcome,
		"is_anonymous": doc.is_anonymous,
		"attachments": attachments,
		"attachment_count": len(attachments),
		"owner": doc.owner,
	}


def _attachments(grievance_name):
	att_rows = frappe.get_all(
		"Grievance Attachment",
		filters={"grievance": grievance_name},
		fields=["name", "file_name", "file_url", "size_bytes", "mime_type", "scan_status", "creation"],
		order_by="creation asc",
	)
	if att_rows:
		return att_rows
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Grievance", "attached_to_name": grievance_name},
		fields=["name", "file_name", "file_url", "file_size", "is_private", "creation"],
		order_by="creation asc",
	)
	return [
		{
			"name": f["name"],
			"file_name": f["file_name"],
			"file_url": f["file_url"],
			"size_bytes": f.get("file_size"),
			"mime_type": None,
			"scan_status": "Clean",
			"creation": f["creation"],
		}
		for f in files
	]
