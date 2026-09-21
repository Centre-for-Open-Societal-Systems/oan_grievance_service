# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Save and resume a partially completed submission directly on Grievance doctype.

A draft is working state, persisted directly as a Grievance with status 'Draft'
and workflow_state 'Draft' (docstatus = 0). Full submission validation runs when
the case is formally submitted.
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
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1.grievance import CLIENT_IMMUTABLE_FIELDS

DRAFT_LIFETIME_DAYS = 30

route = prefixed("/api/v1/drafts")

ALLOWED_DRAFT_ROLES = [
	"Grievance Submitter",
	"Grievance Officer",
	"Grievance Admin",
	"System Manager",
	"Administrator",
]

DRAFT_FIELDS = (
	"submission_channel",
	"submitter_type",
	"submitter_name",
	"contact_mobile",
	"contact_email",
	"administrative_area",
	"administrative_unit",
	"service_category",
	"grievance_type",
	"associated_service_provider",
	"description",
	"desired_outcome",
	"is_anonymous",
)


class SaveDraftRequest(BaseModel):
	"""Partial grievance state for saving a draft.

	Fields are optional so a user can save at any stage of input.
	"""

	model_config = {"extra": "allow"}

	client_submission_uuid: str | None = Field(None, description="Stable client-generated draft key")
	client_uuid: str | None = Field(None, description="Alias for client_submission_uuid")

	submission_channel: str | None = None
	submitter_type: str | None = None
	submitter_name: str | None = None
	contact_mobile: str | None = None
	contact_email: SafeEmail | str | None = None
	administrative_area: str | None = None
	administrative_unit: str | None = None
	service_category: str | None = None
	grievance_type: str | None = None
	associated_service_provider: str | None = None
	description: str | None = None
	desired_outcome: str | None = None
	is_anonymous: int | bool | None = None


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
	is_anonymous: int | bool | None = None,
	**kwargs,
):
	"""Create or overwrite the draft directly on the Grievance doctype with status='Draft'.

	Parameters are accepted as standard grievance fields and validated via Pydantic.
	"""
	uuid_key = (
		client_submission_uuid
		or client_uuid
		or kwargs.get("client_submission_uuid")
		or kwargs.get("client_uuid")
	)
	# Also inspect payload dict if provided for backwards compatibility
	payload_arg = kwargs.get("payload")
	if not uuid_key and isinstance(payload_arg, dict):
		uuid_key = payload_arg.get("client_submission_uuid") or payload_arg.get("client_uuid")

	if not uuid_key:
		frappe.throw(_("A draft key (client_submission_uuid) is required."), title=_("Missing Draft Key"))

	session_user = _session_user()
	if not session_user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	data = {
		"submission_channel": submission_channel,
		"submitter_type": submitter_type,
		"submitter_name": submitter_name,
		"contact_mobile": contact_mobile,
		"contact_email": contact_email,
		"administrative_area": administrative_area,
		"administrative_unit": administrative_unit,
		"service_category": service_category,
		"grievance_type": grievance_type,
		"associated_service_provider": associated_service_provider,
		"description": description,
		"desired_outcome": desired_outcome,
		"is_anonymous": is_anonymous,
	}

	# Merge payload dict or extra kwargs if passed
	if isinstance(payload_arg, dict):
		for k, v in payload_arg.items():
			if data.get(k) is None and v is not None:
				data[k] = v

	for k, v in kwargs.items():
		if k not in ("payload", "step_reached", "expires_on") and data.get(k) is None and v is not None:
			data[k] = v

	name = frappe.db.get_value("Grievance", {"client_submission_uuid": uuid_key}, "name")

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
		doc.client_submission_uuid = uuid_key
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
		except Exception:
			pass

	# Apply provided grievance fields directly
	for field in DRAFT_FIELDS:
		if field in data and data[field] is not None:
			val = data[field]
			field_def = doc.meta.get_field(field)
			if field_def and field_def.fieldtype == "Link":
				if not frappe.db.exists(field_def.options, val):
					continue
			doc.set(field, val)
		elif name and field in data and data[field] is None:
			doc.set(field, None)

	# Set any other valid Grievance doc fields passed
	for field, value in data.items():
		if field in DRAFT_FIELDS or field in CLIENT_IMMUTABLE_FIELDS or not doc.meta.has_field(field):
			continue
		if value is None or value == "":
			doc.set(field, None)
			continue
		field_def = doc.meta.get_field(field)
		if field_def and field_def.fieldtype == "Link":
			if not frappe.db.exists(field_def.options, value):
				continue
		doc.set(field, value)

	doc.flags.ignore_mandatory = True
	doc.flags.is_draft_wizard = True
	doc.save(ignore_permissions=True)

	return success_response(
		data=_draft_state(doc),
		message=_("Draft saved"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def save_draft(*args, **kwargs):
	return save(*args, **kwargs)


@route("", methods=("GET",), summary="Get the authenticated user's latest grievance draft")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def load():
	"""Return the caller's latest unsubmitted draft.

	A user has at most one active draft. Lookup is by session owner.
	"""
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
	model_config = {"extra": "allow"}

	client_submission_uuid: str | None = Field(
		None, description="Draft key to submit; defaults to latest active draft"
	)
	client_uuid: str | None = Field(None, description="Alias for client_submission_uuid")
	consent_given: int | bool = 1
	is_anonymous: int | bool = 0
	anonymity_justification: str | None = None
	submission_channel: str | None = None
	submitter_type: str | None = None
	submitter_name: str | None = None
	contact_mobile: str | None = None
	contact_email: str | None = None
	administrative_area: str | None = None
	administrative_unit: str | None = None
	service_category: str | None = None
	grievance_type: str | None = None
	associated_service_provider: str | None = None
	description: str | None = None
	desired_outcome: str | None = None


@route("/submit", methods=("POST",), summary="Submit a draft grievance into an active case")
@route("/<client_uuid>/submit", methods=("POST",), summary="Submit a draft grievance by UUID")
@frappe.whitelist()
@validate_request(SubmitDraftRequest)
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def submit_draft(
	client_submission_uuid: str | None = None,
	client_uuid: str | None = None,
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
	**kwargs,
):
	"""Submit an existing draft grievance, transitioning its status to Submitted."""
	session_user = _session_user()
	if not session_user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	uuid_key = (
		client_submission_uuid
		or client_uuid
		or kwargs.get("client_submission_uuid")
		or kwargs.get("client_uuid")
	)
	name = None
	if uuid_key:
		name = frappe.db.get_value("Grievance", {"client_submission_uuid": uuid_key}, "name")
	if not name:
		name = _latest_own_draft_name(session_user)

	if not name:
		frappe.throw(_("No saved draft found to submit."), frappe.DoesNotExistError, title=_("Not Found"))

	doc = frappe.get_doc("Grievance", name)
	_assert_owner(doc, session_user)
	if doc.workflow_state != "Draft" and doc.docstatus != 0:
		frappe.throw(
			_("This draft has already been submitted as {0}.").format(doc.ticket_number or doc.name),
			title=_("Already Submitted"),
		)

	# Apply any overrides supplied in call
	overrides = {
		"submission_channel": submission_channel,
		"submitter_type": submitter_type,
		"submitter_name": submitter_name,
		"contact_mobile": contact_mobile,
		"contact_email": contact_email,
		"administrative_area": administrative_area,
		"administrative_unit": administrative_unit,
		"service_category": service_category,
		"grievance_type": grievance_type,
		"associated_service_provider": associated_service_provider,
		"description": description,
		"desired_outcome": desired_outcome,
	}
	# Also unpack payload if provided
	payload_arg = kwargs.get("payload")
	if isinstance(payload_arg, dict):
		for k, v in payload_arg.items():
			if overrides.get(k) is None and v is not None:
				overrides[k] = v

	for k, v in kwargs.items():
		if k not in ("payload", "step_reached", "expires_on") and overrides.get(k) is None and v is not None:
			overrides[k] = v

	for field in DRAFT_FIELDS:
		if overrides.get(field) is not None:
			doc.set(field, overrides[field])

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

		ident = _resolve_submitter_identity(doc.as_dict())
		if ident.get("submitter"):
			doc.submitter = ident["submitter"]
			doc.submitter_type = ident.get("submitter_type") or doc.submitter_type
			doc.submitter_name = ident.get("submitter_name") or doc.submitter_name
		else:
			doc.submitter = find_or_create_submitter(doc.as_dict())

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
			"client_uuid": doc.client_submission_uuid,
			"routing_rule": rule.name if hasattr(rule, "name") else str(rule) if rule else None,
		},
		message=_("Grievance submitted successfully"),
	)


@route("/<client_uuid>", methods=("DELETE",), allow_guest=True, summary="Discard a grievance draft by UUID")
@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"", methods=("DELETE",), allow_guest=True, summary="Discard a grievance draft"
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def discard(client_uuid: str | None = None, client_submission_uuid: str | None = None):
	"""Delete a draft the submitter abandoned."""
	uuid_key = client_submission_uuid or client_uuid
	if not uuid_key:
		session_user = _session_user()
		if session_user:
			name = _latest_own_draft_name(session_user)
		else:
			return success_response(data={"discarded": False}, message=_("No draft to discard"))
	else:
		name = frappe.db.get_value("Grievance", {"client_submission_uuid": uuid_key}, "name")

	if not name:
		return success_response(data={"discarded": False}, message=_("No draft to discard"))

	doc = frappe.get_doc("Grievance", name)
	session_user = _session_user()
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


@frappe.whitelist(allow_guest=True)
@handle_api_errors
def delete_draft(client_uuid: str | None = None, client_submission_uuid: str | None = None):
	return discard(client_uuid=client_uuid, client_submission_uuid=client_submission_uuid)


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
	attachments = _attachments(doc.name)
	return {
		"name": doc.name,
		"ticket_number": doc.ticket_number,
		"client_submission_uuid": doc.client_submission_uuid,
		"client_uuid": doc.client_submission_uuid,
		"status": doc.status,
		"workflow_state": doc.workflow_state,
		"submission_channel": doc.submission_channel,
		"submitter_type": doc.submitter_type,
		"submitter_name": doc.submitter_name,
		"contact_mobile": doc.contact_mobile,
		"contact_email": doc.contact_email,
		"administrative_area": doc.administrative_area,
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
		"owner_user": doc.owner,
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
