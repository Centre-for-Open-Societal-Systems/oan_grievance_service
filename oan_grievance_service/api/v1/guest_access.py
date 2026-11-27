"""Viewing a sensitive anonymous case with its link and PIN, without signing in (STG-518).

One public route, read-only and stateless: `POST /api/v1/guest/grievance` with the ticket number
and token from the link and the PIN the submitter typed. Each call checks all three
(`guest_access.verify`) and answers with that one case. Nothing is kept between calls, so the
portal sends them again on every view, holding them in memory only.

What the guest is shown is built here, from the case alone: its status, description and public
timeline, and the names of the files on public entries. No submitter name or contact, no
officer, department or internal entry, no actions, and no file contents.
"""

import frappe
from frappe import N_, _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, success_response, to_tz_aware_iso, validate_request
from pydantic import BaseModel, ConfigDict, Field

from oan_grievance_service.api.v1._options import public_status
from oan_grievance_service.services import audit, guest_access
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import ticket_number as tn

route = prefixed("/api/v1")

# More public entries than any case gathers; the view has no paging to keep.
MAX_ENTRIES = 500

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
	"file_name",
	"mime_type",
	"size_bytes",
	"document_type",
	"scan_status",
	"creation",
)


class GuestGrievanceRequest(BaseModel):
	model_config = ConfigDict(extra="ignore")

	ticket_number: str = Field(..., min_length=1, max_length=32, title=N_("Ticket number"))
	token: str = Field(..., min_length=1, max_length=64, description="The `k` value after the # in the link")
	pin: str = Field(..., min_length=1, max_length=12, title=N_("PIN"))


def _pick(row: dict, fields) -> dict:
	return {field: row[field] for field in fields if field in row}


def _case_view(doc) -> dict:
	"""The case as its submitter is shown it by link: public entries and their files' names only."""
	from oan_grievance_service.api.v1 import grievance as grievance_api
	from oan_grievance_service.services.administrative_area import (
		format_administrative_location,
		get_administrative_hierarchy,
	)

	entries = frappe.get_all(
		"Grievance Timeline",
		filters={"grievance": doc.name, "is_internal": 0},
		fields=[
			"name",
			"entry_type",
			"is_internal",
			"body",
			"author_user",
			"author_type",
			"ref_doctype",
			"ref_docname",
			"created_on",
		],
		order_by="created_on asc, name asc",
		limit=MAX_ENTRIES,
		ignore_permissions=True,
	)
	public_entries = {entry.name for entry in entries}
	# Only files on a public entry, or on none: evidence an officer put on an internal note stays hidden.
	files = [
		row
		for row in frappe.get_all(
			"Grievance Attachment",
			filters={"grievance": doc.name},
			fields=[*ATTACHMENT_FIELDS, "timeline_entry"],
			order_by="creation asc",
			ignore_permissions=True,
		)
		if not row.timeline_entry or row.timeline_entry in public_entries
	]
	files_by_entry = {}
	for row in files:
		files_by_entry.setdefault(row.timeline_entry, []).append(_pick(row, ATTACHMENT_FIELDS))

	context = {
		"history_map": grievance_api._history_details_for(
			{e.ref_docname for e in entries if e.ref_doctype == "Grievance Status History"}
		),
		"user_names": {},
		"history_fetched": True,
		"response_numbers": grievance_api._get_response_numbers(doc.name),
		"change_requests": {},
	}
	timeline = []
	for entry in entries:
		event = grievance_api._format_timeline_event(entry, doc, context=context)
		if not event:
			continue
		shown = _pick(event, ENTRY_FIELDS)
		shown["attachments"] = files_by_entry.get(entry.name, [])
		timeline.append(shown)

	return {
		"ticket_number": tn.display(doc.ticket_number),
		"status": public_status(doc.status),
		"description": doc.description,
		"desired_outcome": doc.desired_outcome,
		"service_category": doc.service_category,
		"grievance_type_name": (
			frappe.db.get_value("Grievance Type", doc.grievance_type, "type_name")
			if doc.grievance_type
			else None
		),
		"location": format_administrative_location(get_administrative_hierarchy(doc.administrative_area)),
		"sla": {
			"sla_due_date": to_tz_aware_iso(doc.sla_due_date),
			"confirmation_deadline": (
				to_tz_aware_iso(doc.state_deadline) if doc.workflow_state == C.STATE_RESOLVED else None
			),
		},
		"attachments": [_pick(row, ATTACHMENT_FIELDS) for row in files],
		"timeline": timeline,
	}


@route(
	"/guest/grievance",
	methods=("POST",),
	summary="View a sensitive anonymous grievance with its link and PIN",
	auth="public",
	tier="public-reference",
)
@validate_request(GuestGrievanceRequest)
@handle_api_errors
def view_grievance(ticket_number: str, token: str, pin: str):
	"""Check the ticket and token from the link and the PIN typed, and return that one case.

	Read-only and stateless: send all three on every view. A wrong ticket, token or PIN all answer
	401 `GUEST_ACCESS_INVALID`. With the right link, too many wrong PINs answer 429
	`GUEST_ACCESS_LOCKED` (with `retry_after_seconds`) until the lock ends, and a closed case
	answers 410 `GUEST_ACCESS_CLOSED` once the PIN is right. 503 `GUEST_ACCESS_UNAVAILABLE` when
	the site is not configured for it.
	"""
	result = guest_access.verify(ticket_number, token, pin, getattr(frappe.local, "request_ip", None))
	if not result.ok:
		details = {"retry_after_seconds": result.retry_after_seconds} if result.retry_after_seconds else None
		return guest_access.guest_error(result.code, result.status, details)

	doc = frappe.get_doc("Grievance", result.grievance)
	audit.record_access(audit.ACTION_VIEW_DETAIL, grievance=doc.name, scope="guest link")
	return success_response(data=_case_view(doc), message=_("Grievance retrieved"))
