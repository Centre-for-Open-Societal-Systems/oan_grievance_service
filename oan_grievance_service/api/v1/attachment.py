# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Supporting document upload and retrieval for a grievance.

The prototype's evidence panel -- "Attach photos, voice recordings, or documents,
Max 10 MB, JPG PNG PDF MP3" -- is the whole contract, and the backend holds every
part of it rather than trusting the client for any of it.

An upload is not stored and then checked. It is checked and then stored:

1. The type is read from the leading bytes, never from the filename. Frappe's own
   File doctype derives content_type from `mimetypes.guess_type(file_name)`, which
   a payload renamed from .exe to .jpg defeats outright.
2. Location metadata is stripped from images. A submitter who asked for anonymity
   under FSD 9.2 and attached a photograph of their own plot has published their
   coordinates, whatever the database says about their name.
3. The object is withheld until a scanner has looked at it. Uploads land Pending
   and `is_servable()` passes only on Clean, so an unscanned file never reaches an
   officer's browser.

Files may be attached before the Grievance exists. The wizard uploads while the
submitter is still filling it in, so an upload carries either a `grievance` or a
draft's `client_uuid`; `submission.attach_draft_files` re-parents the draft ones
when the case is finally filed.
"""

import frappe
from frappe import _
from oan_auth_service.api.utils import handle_api_errors, require_role, success_response

from oan_grievance_service.grievance_management.doctype.grievance_attachment.grievance_attachment import (
	SCAN_CLEAN,
	SCAN_PENDING,
)
from oan_grievance_service.services import audit, scanning

from .grievance import ALLOWED_GRIEVANCE_ROLES

# A grievance is evidence, not a file share. The prototype shows a small panel, and
# an unbounded one is a denial-of-service surface on a public intake form.
MAX_ATTACHMENTS_PER_CASE = 10


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def submit_document(
	grievance: str | None = None,
	client_uuid: str | None = None,
	document_type: str | None = None,
	response: str | None = None,
):
	"""Upload one supporting document against a grievance or an open draft.

	The file arrives as multipart form data, which is how a browser sends a file
	the submitter picked; nothing about it is trusted until the bytes are read.

	Either `grievance` or `client_uuid` is required. The wizard sends `client_uuid`
	because the case does not exist yet -- the file is parented to the draft and
	re-parented at submission.
	"""
	if not (grievance or client_uuid):
		frappe.throw(
			_("An attachment must name either a grievance or a draft."),
			title=_("Nothing To Attach To"),
		)

	upload = _uploaded_file()
	content = upload["content"]
	file_name = upload["file_name"]

	# Refuses on size, on an unrecognised or disallowed type, and on an extension
	# that disagrees with the bytes. Returns the sniffed type, which is what gets
	# recorded -- the client's claim never does.
	mime = scanning.validate_upload(file_name, content)

	if grievance:
		case = _case_for_write(grievance)
		_enforce_attachment_limit(case.name)
		parent_doctype, parent_name = "Grievance", case.name
		submitter = case.submitter
	else:
		draft = _draft_for_write(client_uuid)
		parent_doctype, parent_name = "Grievance Draft", draft
		submitter = None

	# Re-encoded before storage, not after: the stripped copy is the only one that
	# is ever written, so a crash between write and strip cannot leave coordinates
	# on disk.
	content = scanning.strip_location_metadata(content, mime)

	stored = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"attached_to_doctype": parent_doctype,
			"attached_to_name": parent_name,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)

	attachment = None
	if grievance:
		attachment = frappe.get_doc(
			{
				"doctype": "Grievance Attachment",
				"grievance": parent_name,
				"response": response,
				"document_type": document_type,
				"file_name": stored.file_name,
				"file_url": stored.file_url,
				"mime_type": mime,
				"size_bytes": len(content),
				"checksum_sha256": scanning.sha256_of(content),
				"uploaded_by_submitter": submitter,
				"uploaded_by_user": None if submitter else frappe.session.user,
				"scan_status": SCAN_PENDING,
			}
		).insert(ignore_permissions=True)

	return success_response(
		data={
			"attachment": attachment.name if attachment else None,
			"file_name": stored.file_name,
			"file_url": stored.file_url,
			"mime_type": mime,
			"size_bytes": len(content),
			# Pending is the honest answer and the client should show it: the file is
			# stored but not yet servable. The hourly scan decides.
			"scan_status": attachment.scan_status if attachment else SCAN_PENDING,
		},
		message=_("Document uploaded and queued for scanning"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def get_attachments(grievance: str):
	"""List the evidence on a case, with each file's scan verdict.

	Infected and pending files are listed rather than hidden. An officer needs to
	know something was submitted and what happened to it; silently omitting a row
	would make the case look like it had less evidence than it did.
	"""
	case = _case_for_read(grievance)

	rows = frappe.get_all(
		"Grievance Attachment",
		filters={"grievance": case.name},
		fields=[
			"name",
			"file_name",
			"mime_type",
			"size_bytes",
			"document_type",
			"response",
			"scan_status",
			"scanned_at",
			"uploaded_by_user",
			"uploaded_by_submitter",
			"creation",
		],
		order_by="creation asc",
	)
	for row in rows:
		row["servable"] = row["scan_status"] == SCAN_CLEAN

	audit.record_access(audit.ACTION_VIEW_ATTACHMENT, grievance=case.name)
	return success_response(data=rows, message=_("Attachments fetched"))


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def download(attachment: str):
	"""Hand back one attachment's URL, but only once it has been scanned clean.

	The gate is here rather than on the File row because the File is what an
	officer's browser fetches directly; returning the URL is the last point at
	which this module can refuse.
	"""
	doc = frappe.get_doc("Grievance Attachment", attachment)
	_case_for_read(doc.grievance)

	if not doc.is_servable():
		audit.log_denied(audit.ACTION_VIEW_ATTACHMENT, grievance=doc.grievance)
		frappe.throw(
			_("{0} is not available: its scan status is {1}.").format(
				frappe.bold(doc.file_name), doc.scan_status
			),
			title=_("Attachment Withheld"),
		)

	audit.record_access(audit.ACTION_VIEW_ATTACHMENT, grievance=doc.grievance)
	return success_response(
		data={
			"file_name": doc.file_name,
			"file_url": doc.file_url,
			"mime_type": doc.mime_type,
			"size_bytes": doc.size_bytes,
			"checksum_sha256": doc.checksum_sha256,
		},
		message=_("Attachment ready"),
	)


@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def delete(attachment: str):
	"""Remove an attachment the submitter added by mistake.

	Only while the case is still open: once it is resolved or closed, the evidence
	is part of what the decision rested on and removing it would rewrite the record
	after the fact.
	"""
	from oan_grievance_service.services import constants as C

	doc = frappe.get_doc("Grievance Attachment", attachment)
	case = _case_for_write(doc.grievance)

	if case.status in C.TERMINAL_STATUSES or case.status == C.RESOLVED:
		frappe.throw(
			_("Evidence cannot be removed once the grievance is {0}.").format(case.status),
			title=_("Case Is Closed"),
		)

	file_name = frappe.db.get_value("File", {"file_url": doc.file_url}, "name")
	if file_name:
		frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)

	frappe.delete_doc("Grievance Attachment", doc.name, force=True, ignore_permissions=True)
	audit.record_access(audit.ACTION_VIEW_ATTACHMENT, grievance=case.name)

	return success_response(data={"deleted": True}, message=_("Attachment removed"))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _uploaded_file():
	"""The multipart file on this request, as a name and its bytes.

	Frappe puts the parsed upload on `frappe.request.files`. Reading it here keeps
	every caller above working in bytes, which is what the checks need.
	"""
	files = getattr(frappe.request, "files", None) if frappe.request else None
	if not files or "file" not in files:
		frappe.throw(
			_("No file was uploaded. Send it as multipart form data under the key 'file'."),
			title=_("No File"),
		)

	upload = files["file"]
	content = upload.stream.read()
	if not content:
		frappe.throw(_("The uploaded file is empty."), title=_("Empty File"))

	return {"file_name": upload.filename, "content": content}


def _case_for_read(grievance):
	"""The grievance, if this user may read it. Raises otherwise."""
	from oan_grievance_service.permissions import has_grievance_permission

	if not frappe.db.exists("Grievance", grievance):
		frappe.throw(_("No such grievance."), frappe.DoesNotExistError, title=_("Not Found"))

	doc = frappe.get_doc("Grievance", grievance)
	if not has_grievance_permission(doc, "read"):
		audit.log_denied(audit.ACTION_VIEW_ATTACHMENT, grievance=doc.name)
		frappe.throw(_("You do not have access to this grievance."), frappe.PermissionError)
	return doc


def _case_for_write(grievance):
	from oan_grievance_service.permissions import has_grievance_permission

	doc = _case_for_read(grievance)
	if not has_grievance_permission(doc, "write"):
		audit.log_denied(audit.ACTION_VIEW_ATTACHMENT, grievance=doc.name)
		frappe.throw(_("You cannot add evidence to this grievance."), frappe.PermissionError)
	return doc


def _draft_for_write(client_uuid):
	"""The draft's name, if it exists and has not already been submitted."""
	name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	if not name:
		frappe.throw(_("No saved draft found."), frappe.DoesNotExistError, title=_("Not Found"))

	if frappe.db.get_value("Grievance Draft", name, "submitted_as"):
		frappe.throw(
			_("This draft has already been submitted."),
			title=_("Already Submitted"),
		)
	return name


def _enforce_attachment_limit(grievance):
	count = frappe.db.count("Grievance Attachment", {"grievance": grievance})
	if count >= MAX_ATTACHMENTS_PER_CASE:
		frappe.throw(
			_("A grievance may carry at most {0} attachments.").format(MAX_ATTACHMENTS_PER_CASE),
			title=_("Too Many Attachments"),
		)
