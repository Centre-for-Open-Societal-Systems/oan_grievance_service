"""Save and resume a partially completed submission.

FSD 7 targets farmers on low-connectivity channels and FSD 3.2.1 specifies an
offline-first app. Both make a four-step wizard that only persists on the final
step the wrong shape: the connection is most likely to drop precisely while the
submitter is typing a long description.

A draft is working state, not a record. It is stored unvalidated -- an incomplete
submission is one the Grievance doctype would reject -- and it is cleared on a
schedule once it expires.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, require_role, success_response, validate_request
from pydantic import BaseModel, Field

from oan_grievance_service.services import submission

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
	"""Partial wizard state. Soft by design - full validation runs only at submit.

	`payload` may be empty or incomplete; required grievance fields are not enforced
	here. Only the draft key is required so the client can resume later.
	"""

	model_config = {"extra": "forbid"}

	client_uuid: str = Field(..., min_length=1, description="Stable client-generated draft key")
	payload: dict | str | None = None
	step_reached: int | str | None = 0


@route(  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method, tmp.frappe-semgrep-rules.rules.security.guest-whitelisted-method
	"", methods=("POST",), allow_guest=True, summary="Save a grievance draft"
)
@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@validate_request(SaveDraftRequest)
@handle_api_errors
def save(client_uuid: str, payload: str | dict | None = None, step_reached: int | str = 0):
	"""Create or overwrite the draft for `client_uuid`.

	Overwrites rather than merges: the client holds the whole wizard state, so a
	partial merge would let a field the submitter cleared reappear from an earlier
	save.
	"""
	if not client_uuid:
		frappe.throw(_("A draft key is required."), title=_("Missing Draft Key"))

	data = submission.parse_payload(payload)
	name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	session_user = _session_user()

	if name:
		doc = frappe.get_doc("Grievance Draft", name)
		_assert_owner(doc)
		if doc.submitted_as:
			frappe.throw(
				_("This draft has already been submitted as {0}.").format(doc.submitted_as),
				title=_("Already Submitted"),
			)
		# Claim an anonymous draft once the submitter signs in mid-wizard.
		if not doc.owner_user and session_user:
			doc.owner_user = session_user
	else:
		doc = frappe.new_doc("Grievance Draft")
		doc.client_uuid = client_uuid
		doc.owner_user = session_user

	doc.payload = json.dumps(data, ensure_ascii=False)
	doc.step_reached = max(int(step_reached or 0), doc.step_reached or 0)
	doc.contact_mobile = data.get("contact_mobile")
	doc.expires_on = add_days(now_datetime(), DRAFT_LIFETIME_DAYS)
	doc.save(ignore_permissions=True)

	return success_response(
		data={
			"client_uuid": doc.client_uuid,
			"step_reached": doc.step_reached,
			"expires_on": doc.expires_on,
			"attachment_count": _attachment_count(doc.name),
			"owner_user": doc.owner_user,
		},
		message=_("Draft saved"),
	)


@route("", methods=("GET",), summary="Get the authenticated user's latest grievance draft")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_DRAFT_ROLES)
def load():
	"""Return the caller's latest unsubmitted draft so the wizard can resume.

	A user has at most one active draft. Lookup is by session owner - never by a
	client-supplied draft id - so one authenticated GET is enough to resume.
	"""
	user = _session_user()
	if not user:
		frappe.throw(_("Authentication required."), frappe.PermissionError, title=_("Unauthorized"))

	name = _latest_own_draft_name(user)
	if not name:
		# DoesNotExistError rather than a bare throw: handle_api_errors reads
		# http_status_code off the exception, and a missing draft is a 404 the client
		# can act on -- start a fresh wizard -- not a 400 that reads like bad input.
		frappe.throw(_("No saved draft found."), frappe.DoesNotExistError, title=_("Not Found"))

	doc = frappe.get_doc("Grievance Draft", name)
	_assert_owner(doc)
	_assert_not_expired(doc)

	return success_response(data=_draft_state(doc), message=_("Draft loaded"))


@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def discard(client_uuid: str):
	"""Delete a draft the submitter abandoned.

	A draft already turned into a grievance is kept: it is what makes a retry of
	`submit` return the original ticket instead of filing a second case.
	"""
	name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	if not name:
		return success_response(data={"discarded": False}, message=_("No draft to discard"))

	doc = frappe.get_doc("Grievance Draft", name)
	_assert_owner(doc)
	if doc.submitted_as:
		frappe.throw(
			_("This draft became grievance {0} and cannot be discarded.").format(doc.submitted_as),
			title=_("Already Submitted"),
		)

	frappe.delete_doc("Grievance Draft", name, ignore_permissions=True, delete_permanently=True)
	return success_response(data={"discarded": True}, message=_("Draft discarded"))


def purge_expired_drafts():
	"""Daily: clear abandoned drafts. Drafts that became grievances are retained."""
	stale = frappe.get_all(
		"Grievance Draft",
		filters={"expires_on": ["<", now_datetime()], "submitted_as": ["is", "not set"]},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("Grievance Draft", name, ignore_permissions=True, delete_permanently=True)
	return len(stale)


def _session_user():
	user = frappe.session.user
	return None if user in ("Guest", None) else user


def _assert_owner(doc):
	"""A draft claimed by a signed-in user stays with that user.

	Anonymous drafts are protected only by the unguessability of the key, which is
	the same guarantee the ticket-number lookup already relies on. Once `owner_user`
	is set, only that user may read or mutate it -- never another session.
	"""
	if not doc.owner_user:
		return
	if _session_user() == doc.owner_user:
		return
	frappe.throw(
		_("This draft belongs to another user."),
		frappe.PermissionError,
		title=_("Forbidden"),
	)


def _assert_not_expired(doc):
	"""An expired unsubmitted draft is gone as far as resume is concerned."""
	if doc.submitted_as or not doc.expires_on:
		return
	if get_datetime(doc.expires_on) < now_datetime():
		frappe.throw(_("No saved draft found."), frappe.DoesNotExistError, title=_("Not Found"))


def _latest_own_draft_name(user):
	"""The caller's newest unsubmitted, unexpired draft, or None.

	One active draft per user: pick the most recently modified open draft that
	has not expired. Submitted drafts are ignored (they already became cases).
	"""
	rows = frappe.get_all(
		"Grievance Draft",
		filters={"owner_user": user, "submitted_as": ["is", "not set"]},
		fields=["name", "expires_on"],
		order_by="modified desc",
		limit=5,
	)
	now = now_datetime()
	for row in rows:
		if not row.expires_on or get_datetime(row.expires_on) >= now:
			return row.name
	return None


def _draft_state(doc):
	"""Everything the multi-step form needs to repopulate every step."""
	attachments = _attachments(doc.name)
	return {
		"name": doc.name,
		"client_uuid": doc.client_uuid,
		"payload": submission.parse_payload(doc.payload),
		"step_reached": doc.step_reached,
		"contact_mobile": doc.contact_mobile,
		"expires_on": doc.expires_on,
		"submitted_as": doc.submitted_as,
		"attachments": attachments,
		"attachment_count": len(attachments),
	}


def _attachments(draft_name):
	return frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Grievance Draft", "attached_to_name": draft_name},
		fields=["name", "file_name", "file_url", "file_size", "is_private"],
		order_by="creation asc",
	)


def _attachment_count(draft_name):
	return frappe.db.count("File", {"attached_to_doctype": "Grievance Draft", "attached_to_name": draft_name})
