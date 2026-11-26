"""Opening a sensitive anonymous case with its link and PIN, without signing in (STG-518).

Every route here is public to the gateway (`auth="public"`): the credential is the link's token
and the PIN, then the session `verify` returns, sent back in the `X-Grievance-Guest-Session`
header. Guest routes take no ticket number: the case is the one the session is bound to.

The guest routes wrap the submitter's own endpoints (`timeline`, `message`, the attachment
routes), run as the guest service user, and pass on a whitelist of what they return: no
submitter name or contact, no officer, department or internal entry, no staff controls.
"""

from datetime import UTC, datetime

import frappe
from frappe import N_, _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import check_rate_limit, handle_api_errors, success_response, validate_request
from pydantic import BaseModel, ConfigDict, Field

from oan_grievance_service.services import guest_access
from oan_grievance_service.services import ticket_number as tn

route = prefixed("/api/v1")

PUBLIC = {"auth": "public", "tier": "public-reference"}


def _iso(epoch: float | None) -> str | None:
	return datetime.fromtimestamp(epoch, tz=UTC).isoformat() if epoch else None


class VerifyGuestAccessRequest(BaseModel):
	model_config = ConfigDict(extra="ignore")

	ticket_number: str = Field(..., min_length=1, max_length=32, title=N_("Ticket number"))
	token: str = Field(..., min_length=1, max_length=64, description="The `k` value after the # in the link")
	pin: str = Field(..., min_length=1, max_length=12, title=N_("PIN"))


class GuestMessageRequest(BaseModel):
	# Ignored, not refused: a client field such as `is_internal` or `grievance` never reaches
	# the handler, so a guest cannot post a note or name another case.
	model_config = ConfigDict(extra="ignore")

	body: str = Field(..., min_length=1)
	client_message_id: str | None = Field(
		None,
		max_length=64,
		title=N_("Client message ID"),
		description="Client-generated idempotency key (UUIDv4) to prevent duplicate submissions.",
	)


@route(
	"/guest-access/verify",
	methods=("POST",),
	summary="Check a grievance link and PIN, and open a guest session",
	**PUBLIC,
)
@validate_request(VerifyGuestAccessRequest)
@handle_api_errors
def verify(ticket_number: str, token: str, pin: str):
	"""Check the ticket and token from the link and the PIN the submitter typed.

	A wrong ticket, token or PIN all answer 401 `GUEST_ACCESS_INVALID`. With the right link,
	too many wrong PINs answer 429 `GUEST_ACCESS_LOCKED` (with `retry_after_seconds`) until the
	lock ends, and a closed case answers 410 `GUEST_ACCESS_CLOSED` once the PIN is right.
	On success the session token goes back in the `X-Grievance-Guest-Session` header of every
	guest call; it ends after the idle limit without a call, or at `expires_at` regardless.
	"""
	result = guest_access.verify(ticket_number, token, pin, getattr(frappe.local, "request_ip", None))
	if not result.ok:
		details = {"retry_after_seconds": result.retry_after_seconds} if result.retry_after_seconds else None
		return guest_access.guest_error(result.code, result.status, details)

	conf = guest_access.settings()
	ticket = frappe.db.get_value("Grievance", result.grievance, "ticket_number")
	return success_response(
		data={
			"session_token": result.session_token,
			"ticket_number": tn.display(ticket),
			"idle_timeout_seconds": conf.idle_minutes * 60,
			"expires_at": _iso(result.expires_at),
		},
		message=_("PIN verified"),
	)


@route("/guest-access/session", methods=("GET",), summary="Keep a guest session alive", **PUBLIC)
@handle_api_errors
def session():
	"""Slide the session's idle limit forward ("Stay signed in") and report what is left."""
	check = guest_access.touch_session(guest_access.session_token_from_request())
	if not check.ok:
		return guest_access.guest_error(check.code, check.status)
	return success_response(
		data={"idle_timeout_seconds": check.idle_seconds, "expires_at": _iso(check.expires_at)},
		message=_("Session active"),
	)


@route("/guest-access/logout", methods=("POST",), summary="End a guest session", **PUBLIC)
@handle_api_errors
def logout():
	"""End the session now. Answers the same whether or not it was still open."""
	guest_access.end_session(guest_access.session_token_from_request())
	return success_response(data={"ended": True}, message=_("Session ended"))


# What a guest is shown of the case, its timeline entries and its files. Everything else the
# submitter's endpoints return is left out.
CASE_FIELDS = (
	"ticket_number",
	"status",
	"description",
	"desired_outcome",
	"service_category",
	"grievance_type",
	"grievance_type_name",
	"location",
	"has_more",
	"next_cursor",
)
SLA_FIELDS = ("sla_due_date", "is_paused", "confirmation_deadline")
ENTRY_FIELDS = (
	"id",
	"name",
	"entry_type",
	"body",
	"author_type",
	"created_on",
	"from_status",
	"to_status",
	"action",
	"action_taken",
	"resolution_summary",
	"response_number",
)
ATTACHMENT_FIELDS = (
	"name",
	"attachment",
	"file_name",
	"mime_type",
	"file_size",
	"size_bytes",
	"document_type",
	"timeline_entry",
	"scan_status",
	"creation",
)


def _pick(row: dict, fields) -> dict:
	return {field: row[field] for field in fields if field in row}


def _entry(entry: dict) -> dict:
	shown = _pick(entry, ENTRY_FIELDS)
	shown["attachments"] = [_pick(att, ATTACHMENT_FIELDS) for att in entry.get("attachments") or []]
	return shown


def _case_view(data: dict) -> dict:
	view = _pick(data, CASE_FIELDS)
	view["sla"] = _pick(data.get("sla") or {}, SLA_FIELDS)
	view["attachments"] = [_pick(att, ATTACHMENT_FIELDS) for att in data.get("attachments") or []]
	view["timeline"] = [_entry(entry) for entry in data.get("timeline") or [] if not entry.get("is_internal")]
	return view


def _ticket_of(grievance: str) -> str:
	return frappe.db.get_value("Grievance", grievance, "ticket_number") or grievance


@route(
	"/guest/grievance", methods=("GET",), summary="View the grievance a guest session is bound to", **PUBLIC
)
@handle_api_errors
@guest_access.as_guest
def view_grievance(grievance: str, limit: int | str = 50, cursor: str | None = None):
	"""The case, its public timeline and its files, as the submitter is shown them, less identity."""
	from oan_grievance_service.api.v1 import grievance as grievance_api

	result = grievance_api.timeline(ticket_number=_ticket_of(grievance), limit=limit, cursor=cursor)
	if result.get("status") == "error":
		return result
	return success_response(data=_case_view(result.get("data") or {}), message=result.get("message"))


@route(
	"/guest/grievance/message",
	methods=("POST",),
	summary="Reply on the grievance a guest session is bound to, with optional files",
	request_types=("application/json", "multipart/form-data"),
	**PUBLIC,
)
@validate_request(GuestMessageRequest)
@handle_api_errors
@guest_access.as_guest
def post_message(grievance: str, body: str, client_message_id: str | None = None):
	"""A public reply from the submitter. Files sent as multipart attach to it and are scanned."""
	from oan_grievance_service.api.v1 import grievance as grievance_api

	conf = guest_access.settings()
	check_rate_limit(f"grievance_guest:message:{grievance}", conf.message_limit, conf.message_window_seconds)
	result = grievance_api.message(
		ticket_number=_ticket_of(grievance), body=body, client_message_id=client_message_id
	)
	if result.get("status") == "error":
		return result
	data = result.get("data") or {}
	return success_response(
		data={
			"name": data.get("name"),
			"created_on": data.get("created_on"),
			"attachments": [_pick(att, ATTACHMENT_FIELDS) for att in data.get("attachments") or []],
			"timeline_event": _entry(data.get("timeline_event") or {}),
		},
		message=result.get("message"),
	)


@route("/guest/grievance/attachments", methods=("GET",), summary="List the grievance's files", **PUBLIC)
@handle_api_errors
@guest_access.as_guest
def list_attachments(grievance: str):
	"""Every file on the case with its scan verdict; only Clean ones can be opened."""
	from oan_grievance_service.api.v1 import attachment as attachment_api

	result = attachment_api.get_attachments(grievance=grievance)
	if result.get("status") == "error":
		return result
	return success_response(
		data=[_pick(row, ATTACHMENT_FIELDS) for row in result.get("data") or []],
		message=result.get("message"),
	)


def _stream(grievance: str, attachment_id: str, download):
	from oan_grievance_service.api.v1 import attachment as attachment_api

	# Answered as not found, like an id that does not exist, so the id of another case's file
	# tells a guest nothing.
	if frappe.db.get_value("Grievance Attachment", attachment_id, "grievance") != grievance:
		frappe.throw(_("No such attachment."), frappe.DoesNotExistError, title=_("Not Found"))
	return attachment_api.view(attachment_id=attachment_id, download=download)


@route(
	"/guest/grievance/attachments/<attachment_id>/view",
	methods=("GET",),
	summary="Open one of the grievance's files",
	response_types=("*/*",),
	**PUBLIC,
)
@handle_api_errors
@guest_access.as_guest
def view_attachment(grievance: str, attachment_id: str, download: str | int | None = None):
	"""The file's bytes, inline (`download=1` for a Save As), once it has been scanned clean."""
	return _stream(grievance, attachment_id, download)


@route(
	"/guest/grievance/attachments/<attachment_id>/download",
	methods=("GET",),
	summary="Download one of the grievance's files",
	response_types=("*/*",),
	**PUBLIC,
)
@handle_api_errors
@guest_access.as_guest
def download_attachment(grievance: str, attachment_id: str):
	"""The file's bytes as a download, once it has been scanned clean."""
	return _stream(grievance, attachment_id, 1)
