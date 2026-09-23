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

Files may be attached directly to open grievances or in-progress drafts
persisted on the Grievance DocType with status 'Draft'.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, Field, model_validator
from werkzeug.wrappers import Response


class UploadedFile:
	def __init__(self, file_name: str, content: bytes):
		self.file_name = file_name
		self.content = content

	@property
	def size_bytes(self) -> int:
		return len(self.content)


def get_uploaded_files(
	key: str | None = None,
	allow_empty: bool = False,
	max_count: int | None = None,
	max_size_bytes: int | None = None,
) -> list[UploadedFile]:
	"""Extract multipart files from frappe.request.files.

	Supports single or multiple files under a specific key or any keys.
	Validates presence and non-empty content.
	"""
	req = getattr(frappe, "request", None)
	files = getattr(req, "files", None) if req else None
	if not files:
		frappe.throw(
			_("No file was uploaded. Send it as multipart form data under the key 'file' or 'files'."),
			title=_("No File"),
		)

	file_objects = []
	if key:
		if hasattr(files, "getlist"):
			file_objects = [item for item in files.getlist(key) if item]
		elif isinstance(files, dict) and key in files:
			val = files[key]
			file_objects = list(val) if isinstance(val, list | tuple) else ([val] if val else [])
	else:
		if hasattr(files, "getlist"):
			for k in files.keys():
				for item in files.getlist(k):
					if item:
						file_objects.append(item)
		elif isinstance(files, dict):
			for val in files.values():
				if isinstance(val, list | tuple):
					file_objects.extend(item for item in val if item)
				elif val:
					file_objects.append(val)
		elif isinstance(files, list | tuple):
			file_objects.extend(item for item in files if item)

	if not file_objects:
		frappe.throw(
			_("No file was uploaded. Send it as multipart form data under the key 'file' or 'files'."),
			title=_("No File"),
		)

	if max_count and len(file_objects) > max_count:
		frappe.throw(
			_("Too many files. Maximum {0} files allowed.").format(max_count),
			title=_("Too Many Files"),
		)

	results = []
	for item in file_objects:
		file_name = (
			getattr(item, "filename", None)
			or getattr(item, "file_name", None)
			or getattr(item, "name", "unnamed")
		)
		stream = getattr(item, "stream", None)
		if stream:
			try:
				stream.seek(0)
			except Exception:
				pass
			content = stream.read()
			try:
				stream.seek(0)
			except Exception:
				pass
		elif hasattr(item, "read"):
			content = item.read()
		elif hasattr(item, "content"):
			content = item.content
		elif isinstance(item, bytes):
			content = item
		else:
			content = b""

		if not content and not allow_empty:
			if len(file_objects) == 1:
				frappe.throw(_("The uploaded file is empty."), title=_("Empty File"))
			else:
				frappe.throw(
					_("The uploaded file {0} is empty.").format(frappe.bold(file_name)),
					title=_("Empty File"),
				)

		if max_size_bytes and len(content) > max_size_bytes:
			frappe.throw(
				_("File '{0}' exceeds maximum size of {1} bytes.").format(file_name, max_size_bytes),
				title=_("File Too Large"),
			)

		results.append(UploadedFile(file_name=file_name, content=content))

	return results


from oan_grievance_service.grievance_management.doctype.grievance_attachment.grievance_attachment import (
	SCAN_CLEAN,
	SCAN_PENDING,
)
from oan_grievance_service.services import audit, scanning

from .grievance import ALLOWED_GRIEVANCE_ROLES

route = prefixed("/api/v1/attachments")

# A grievance is evidence, not a file share. The prototype shows a small panel, and
# an unbounded one is a denial-of-service surface on a public intake form.
MAX_ATTACHMENTS_PER_CASE = 10

grievance_route = prefixed("/api/v1/grievances")


class SubmitDocumentsRequest(BaseModel):
	model_config = {"extra": "allow"}

	grievance: str = Field(..., min_length=1, description="Unique Grievance document identifier")
	document_type: str | list[str] | None = None
	response: str | None = None

	@model_validator(mode="after")
	def validate_upload_limits(self):
		try:
			uploads = get_uploaded_files()
		except Exception as exc:
			msg = str(exc.args[0]) if getattr(exc, "args", None) else str(exc)
			raise ValueError(msg) from exc
		if not uploads:
			raise ValueError(_("At least one document file must be attached."))
		if len(uploads) > MAX_ATTACHMENTS_PER_CASE:
			raise ValueError(
				_("A grievance may carry at most {0} attachments.").format(MAX_ATTACHMENTS_PER_CASE)
			)
		existing_count = frappe.db.count("Grievance Attachment", {"grievance": self.grievance})
		if existing_count + len(uploads) > MAX_ATTACHMENTS_PER_CASE:
			raise ValueError(
				_("A grievance may carry at most {0} attachments.").format(MAX_ATTACHMENTS_PER_CASE)
			)
		return self


@grievance_route("/<grievance>/attachments", methods=("POST",), summary="Upload supporting documents")
@route("", methods=("POST",), summary="Upload supporting documents")
@frappe.whitelist(methods=["POST"])
@validate_request(SubmitDocumentsRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def submit_documents(
	grievance: str,
	document_type: str | list[str] | None = None,
	response: str | None = None,
	**kwargs,
):
	"""Upload one or more supporting documents against a grievance.

	Requires authentication (Grievance Submitter, Officer, or Admin role).
	Accepts single or multiple files in multipart form data.
	Always returns a list of created attachment records.
	"""
	case = _case_for_write(grievance)
	owner = {"grievance": case.name}
	submitter = case.submitter

	uploads = get_uploaded_files()

	# 1. Validate each file and strip location metadata before storing
	prepared_files = []
	for upload in uploads:
		file_name = upload.file_name
		content = upload.content

		validated = scanning.validate_upload(file_name, content)
		cleaned_content = scanning.strip_location_metadata(content, validated.mime_type)

		prepared_files.append(
			{
				"file_name": validated.file_name,
				"content": cleaned_content,
				"mime_type": validated.mime_type,
				"size_bytes": len(cleaned_content),
				"checksum_sha256": scanning.sha256_of(cleaned_content),
			}
		)

	# 2. Persist File and Grievance Attachment records
	results = []
	attachment_names = []
	for idx, item in enumerate(prepared_files):
		stored = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": item["file_name"],
				"content": item["content"],
				"is_private": 1,
			}
		).insert(ignore_permissions=True)

		doc_type = (
			document_type[idx]
			if isinstance(document_type, list | tuple) and idx < len(document_type)
			else (str(document_type) if document_type else None)
		)

		attachment = frappe.get_doc(
			{
				"doctype": "Grievance Attachment",
				**owner,
				"response": response,
				"document_type": doc_type,
				"file_name": stored.file_name,
				"file_url": stored.file_url,
				"mime_type": item["mime_type"],
				"size_bytes": item["size_bytes"],
				"checksum_sha256": item["checksum_sha256"],
				"uploaded_by_submitter": submitter,
				"uploaded_by_user": None if submitter else _acting_user(),
				"scan_status": SCAN_PENDING,
			}
		).insert(ignore_permissions=True)

		frappe.db.set_value(
			"File",
			stored.name,
			{"attached_to_doctype": "Grievance Attachment", "attached_to_name": attachment.name},
			update_modified=False,
		)

		attachment_names.append(attachment.name)
		results.append(
			{
				"attachment": attachment.name,
				"file_name": stored.file_name,
				"mime_type": item["mime_type"],
				"size_bytes": item["size_bytes"],
				"checksum_sha256": attachment.checksum_sha256,
				"scan_status": attachment.scan_status,
			}
		)

	# 3. Asynchronously enqueue scanning for all created attachments
	scanning.enqueue_scan_attachments(attachment_names)

	return success_response(
		data=results,
		message=_("{0} document(s) uploaded and queued for scanning").format(len(results)),
	)


@grievance_route("/<grievance>/attachments", methods=("GET",), summary="List a grievance's attachments")
@route("", methods=("GET",), summary="List attachments for a grievance")
@frappe.whitelist(methods=["GET"])
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
		# has_permission now denies read on anything not yet Clean, which is what
		# closes the /private/files bypass. Listing has to step around it: an officer
		# needs to see that an infected file was submitted and what became of it.
		# The case-level check above is what authorises this read.
		ignore_permissions=True,
	)

	return success_response(data=rows, message=_("Attachments fetched"))


@route("/<attachment>/view", methods=("GET",), summary="Stream a clean attachment inline")
@frappe.whitelist(methods=["GET"])
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def view(attachment: str, download: str | int | None = None):
	"""Serve the bytes of one attachment for display in the browser.

	The private-file route that `file_url` points at sits outside the JWT
	namespaces, so a bearer token does nothing there and a Next.js client cannot
	open the URL it was handed. This route is under `/api/v1`, so the same token
	that listed the case also fetches the file, and the scan gate is applied on
	the way out. Inline by default so an image or PDF opens in the tab; pass
	`download=1` for a Save As.
	"""
	doc = _servable(attachment)
	content = scanning.read_object(doc.file_url)
	if content is None:
		frappe.throw(
			_("{0} has no stored object.").format(frappe.bold(doc.file_name)),
			frappe.DoesNotExistError,
			title=_("Attachment Missing"),
		)

	audit.record_access(audit.ACTION_VIEW_ATTACHMENT, grievance=doc.grievance)

	response = Response(content, status=200, mimetype=doc.mime_type or "application/octet-stream")
	disposition = "attachment" if str(download or "").lower() in ("1", "true", "yes") else "inline"
	response.headers.add("Content-Disposition", disposition, filename=doc.file_name)
	response.headers["Content-Length"] = str(len(content))
	response.headers["Cache-Control"] = "private, no-store"
	response.headers["X-Content-Type-Options"] = "nosniff"
	if doc.checksum_sha256:
		response.headers["ETag"] = f'"{doc.checksum_sha256}"'

	# Tells handle_api_errors to hand the Response back untouched instead of
	# wrapping it in the JSON envelope; the REST router and Frappe's RPC handler
	# both pass a Response object through as-is.
	frappe.response["type"] = "download"
	return response


@route("/<attachment>/download", methods=("GET",), summary="Get attachment download URL")
@frappe.whitelist(methods=["GET"])
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def download(attachment: str):
	"""Metadata for one attachment, but only once it has been scanned clean.

	Kept for the desk and for clients with a Frappe session cookie, which can
	fetch `file_url` directly. API clients should use `/view`, which streams the
	bytes under the same gate.
	"""
	doc = _servable(attachment)
	audit.record_access(audit.ACTION_VIEW_ATTACHMENT, grievance=doc.grievance)
	return success_response(
		data={
			"file_name": doc.file_name,
			"file_url": doc.file_url,
			"view_url": f"/api/v1/attachments/{doc.name}/view",
			"mime_type": doc.mime_type,
			"size_bytes": doc.size_bytes,
			"checksum_sha256": doc.checksum_sha256,
		},
		message=_("Attachment ready"),
	)


def _servable(attachment: str):
	"""The attachment row, after the case permission and the scan gate.

	The gate lives here rather than on the File row because this is the last
	point at which the module can refuse before bytes or a URL leave it.
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
	return doc


@route("/<attachment>", methods=("DELETE", "POST"), summary="Delete an attachment")
@frappe.whitelist(methods=["DELETE", "POST"])
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def delete(attachment: str):
	"""Remove an attachment the submitter added by mistake.

	Only while the case is still open: once it is resolved or closed, the evidence
	is part of what the decision rested on and removing it would rewrite the record
	after the fact.
	"""
	doc = frappe.get_doc("Grievance Attachment", attachment)
	case = _case_for_write(doc.grievance)

	if case.status in ("Closed", "Rejected", "Resolved"):
		frappe.throw(
			_("Evidence cannot be removed once the grievance is {0}.").format(case.status),
			title=_("Case Is Closed"),
		)

	file_name = frappe.db.get_value("File", {"file_url": doc.file_url}, "name")
	if file_name:
		frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)

	frappe.delete_doc("Grievance Attachment", doc.name, force=True, ignore_permissions=True)
	audit.record_access(audit.ACTION_DELETE_ATTACHMENT, grievance=case.name)

	return success_response(data={"deleted": True}, message=_("Attachment removed"))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _acting_user():
	"""The signed-in user, or None for a guest filling in the wizard."""
	user = frappe.session.user
	return None if user in ("Guest", None) else user


def _case_for_read(grievance):
	"""The grievance, if this user may read it. Raises otherwise."""
	from oan_grievance_service.permissions import has_grievance_permission

	name = grievance
	if not frappe.db.exists("Grievance", name):
		name = frappe.db.get_value("Grievance", {"ticket_number": grievance}, "name") or frappe.db.get_value(
			"Grievance", {"client_submission_uuid": grievance}, "name"
		)

	if not name or not frappe.db.exists("Grievance", name):
		frappe.throw(_("No such grievance."), frappe.DoesNotExistError, title=_("Not Found"))

	doc = frappe.get_doc("Grievance", name)
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
