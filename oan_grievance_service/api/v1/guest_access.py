"""Viewing a sensitive anonymous case with its link and PIN, without signing in (STG-518).

One public route, read-only and stateless: `POST /api/v1/guest/grievance` with the ticket number
and token from the link and the PIN the submitter typed. Each call checks all three
(`guest_access.verify`) and answers with that one case. Nothing is kept between calls, so the
portal sends them again on every view, holding them in memory only.

What the guest is shown is built from the submitter-safe parts the signed-in timeline shares
(`_case_view.py`): its status, description and public timeline, and the names of the files on
public entries. No submitter name or contact, no officer, department or internal entry, no
actions, and no file contents.
"""

import frappe
from frappe import N_, _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, success_response, validate_request
from pydantic import BaseModel, ConfigDict, Field

from oan_grievance_service.api.v1 import _case_view as case_view
from oan_grievance_service.services import audit, guest_access

route = prefixed("/api/v1")

# More public entries than any case gathers; the view has no paging to keep.
MAX_ENTRIES = 500

# What the guest is shown, field by field. Anything the shared parts carry that is not named here
# (an author's name, a file's URL, the uploader) is never sent.
CASE_FIELDS = (
	"ticket_number",
	"status",
	"description",
	"desired_outcome",
	"service_category",
	"grievance_type_name",
	"location",
)
SLA_FIELDS = ("sla_due_date", "confirmation_deadline")
ENTRY_FIELDS = (
	"id",
	"name",
	"entry_type",
	"body",
	"author_type",
	"author_role",
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
	"file_size",
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
	"""The case as its submitter is shown it by link: public entries and their files' names only.

	Built from the submitter-safe parts `timeline` shares (`_case_view.py`), never from `timeline`
	itself, and only the fields named above leave.
	"""
	entries, _more, _cursor = case_view.timeline_entries(doc, include_internal=False, limit=MAX_ENTRIES)
	files = case_view.case_attachments(doc, include_internal=False)
	files_by_entry = case_view.attachments_by_entry(files)
	summary = case_view.case_summary(doc)

	timeline = []
	for entry in entries:
		shown = _pick(entry, ENTRY_FIELDS)
		shown["attachments"] = [
			_pick(row, ATTACHMENT_FIELDS) for row in files_by_entry.get(entry["name"], [])
		]
		timeline.append(shown)

	return {
		**_pick(summary, CASE_FIELDS),
		"sla": _pick(summary["sla"], SLA_FIELDS),
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
