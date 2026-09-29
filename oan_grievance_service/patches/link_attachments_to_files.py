# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Give every attachment a Link to its File, and put legacy Files through the scan gate.

Two repairs:

1. Rows created before the `file` Link find their File by URL string. Core reuses
   one URL for identical uploads, so that lookup could name another row's File.
   The File that core attached to the row is linked. Failing that, the URL match
   is used only when it is unambiguous: exactly one File has that URL and no
   other attachment links to it. Anything else is left unlinked and reported,
   since a wrong link would let one case's delete remove another case's File.

2. A File attached straight to a Grievance never met the scanner, yet the draft
   and detail endpoints listed it as Clean. Each such File becomes a Pending
   Grievance Attachment, re-attached to that row, and is queued for a scan.
   One that is not an accepted type, or cannot be read, is recorded as Failed
   with the reason, so it is withheld rather than trusted. A photo carrying
   location metadata is stripped the way an upload is, into a fresh File, so
   the citizen's coordinates do not stay on disk behind the case.
"""

import frappe


def execute():
	from oan_grievance_service.services import scanning

	linked, unlinked = 0, []
	for row in frappe.get_all(
		"Grievance Attachment", filters={"file": ["is", "not set"]}, fields=["name", "file_url"]
	):
		file_name = _file_for(row)
		if file_name:
			frappe.db.set_value("Grievance Attachment", row.name, "file", file_name, update_modified=False)
			linked += 1
		else:
			unlinked.append(row.name)
	if unlinked:
		frappe.log_error(
			title="Attachments left without a File link",
			message="No File is attached to these rows and their file_url is shared or missing, "
			"so no link could be made safely:\n" + "\n".join(unlinked),
		)

	created = []
	legacy = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Grievance", "is_folder": 0},
		fields=["name", "file_name", "file_url", "file_size", "attached_to_name", "owner"],
	)
	for stored in legacy:
		if not frappe.db.exists("Grievance", stored.attached_to_name):
			continue
		content = scanning.read_object(stored.name)
		status, detail, mime, checksum = scanning.SCAN_PENDING, None, None, None
		file_name, file_url = stored.name, stored.file_url
		if content is None:
			status, detail = scanning.SCAN_FAILED, "File object could not be read."
		else:
			try:
				mime = scanning.validate_upload(stored.file_name, content).mime_type
			except Exception as exc:
				status, detail = scanning.SCAN_FAILED, str(exc)[:500]
			if mime and scanning.has_location_metadata(content):
				content = scanning.strip_location_metadata(content, mime)
				replacement = frappe.get_doc(
					{"doctype": "File", "file_name": stored.file_name, "content": content, "is_private": 1}
				).insert(ignore_permissions=True)
				file_name, file_url = replacement.name, replacement.file_url
			checksum = scanning.sha256_of(content)

		# A row the first pass linked by URL may point at this very File while it
		# is still attached to the Grievance. That row is the evidence; a second
		# one would put two rows on one File, and deleting either would take it.
		existing = frappe.db.get_value("Grievance Attachment", {"file": stored.name}, "name")
		if existing:
			updates = {}
			if file_name != stored.name:
				updates = {"file": file_name, "file_url": file_url, "checksum_sha256": checksum}
				if content:
					updates["size_bytes"] = len(content)
			if updates:
				frappe.db.set_value("Grievance Attachment", existing, updates, update_modified=False)
			attachment_name = existing
		else:
			attachment = frappe.get_doc(
				{
					"doctype": "Grievance Attachment",
					"grievance": stored.attached_to_name,
					"file": file_name,
					"file_name": stored.file_name,
					"file_url": file_url,
					"mime_type": mime,
					"size_bytes": len(content) if content else (stored.file_size or 0),
					"checksum_sha256": checksum,
					"uploaded_by_user": stored.owner or "Administrator",
					"scan_status": status,
					"scan_detail": detail,
				}
			)
			attachment.flags.ignore_validate = True
			attachment.insert(ignore_permissions=True)
			attachment_name = attachment.name
			if status == scanning.SCAN_PENDING:
				created.append(attachment_name)

		frappe.db.set_value(
			"File",
			file_name,
			{"attached_to_doctype": "Grievance Attachment", "attached_to_name": attachment_name},
			update_modified=False,
		)
		if file_name != stored.name:
			frappe.delete_doc("File", stored.name, force=True, ignore_permissions=True)

	if created:
		scanning.enqueue_scan_attachments(created)
	print(f"link_attachments_to_files: linked {linked} rows, folded {len(legacy)} legacy files")


def _file_for(row):
	"""The File this row may safely link to, or None.

	First the File core attached to the row. Otherwise the row's URL, but only
	when exactly one File carries it and no other attachment already links that
	File: core keeps a File row per upload and one URL per content, so a shared
	URL cannot say which row is whose.
	"""
	attached = frappe.db.get_value(
		"File", {"attached_to_doctype": "Grievance Attachment", "attached_to_name": row.name}, "name"
	)
	if attached:
		return attached
	if not row.file_url:
		return None
	candidates = frappe.get_all("File", filters={"file_url": row.file_url}, pluck="name")
	if len(candidates) != 1:
		return None
	if frappe.db.exists("Grievance Attachment", {"file": candidates[0]}):
		return None
	return candidates[0]
