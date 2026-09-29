# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Give every attachment a Link to its File, and put legacy Files through the scan gate.

Two repairs:

1. Rows created before the `file` Link find their File by URL string. Core reuses
   one URL for identical uploads, so that lookup could name another row's File.
   The File that core attached to the row is linked; failing that, the URL match.

2. A File attached straight to a Grievance never met the scanner, yet the draft
   and detail endpoints listed it as Clean. Each such File becomes a Pending
   Grievance Attachment, re-attached to that row, and is queued for a scan.
   One that is not an accepted type, or cannot be read, is recorded as Failed
   with the reason, so it is withheld rather than trusted.
"""

import frappe


def execute():
	from oan_grievance_service.services import scanning

	linked = 0
	for row in frappe.get_all(
		"Grievance Attachment", filters={"file": ["is", "not set"]}, fields=["name", "file_url"]
	):
		file_name = frappe.db.get_value(
			"File", {"attached_to_doctype": "Grievance Attachment", "attached_to_name": row.name}, "name"
		) or (frappe.db.get_value("File", {"file_url": row.file_url}, "name") if row.file_url else None)
		if file_name:
			frappe.db.set_value("Grievance Attachment", row.name, "file", file_name, update_modified=False)
			linked += 1

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
		if content is None:
			status, detail = scanning.SCAN_FAILED, "File object could not be read."
		else:
			checksum = scanning.sha256_of(content)
			try:
				mime = scanning.validate_upload(stored.file_name, content).mime_type
			except Exception as exc:
				status, detail = scanning.SCAN_FAILED, str(exc)[:500]

		attachment = frappe.get_doc(
			{
				"doctype": "Grievance Attachment",
				"grievance": stored.attached_to_name,
				"file": stored.name,
				"file_name": stored.file_name,
				"file_url": stored.file_url,
				"mime_type": mime,
				"size_bytes": stored.file_size or (len(content) if content else 0),
				"checksum_sha256": checksum,
				"uploaded_by_user": stored.owner or "Administrator",
				"scan_status": status,
				"scan_detail": detail,
			}
		)
		attachment.flags.ignore_validate = True
		attachment.insert(ignore_permissions=True)
		frappe.db.set_value(
			"File",
			stored.name,
			{"attached_to_doctype": "Grievance Attachment", "attached_to_name": attachment.name},
			update_modified=False,
		)
		if status == scanning.SCAN_PENDING:
			created.append(attachment.name)

	if created:
		scanning.enqueue_scan_attachments(created)
	print(f"link_attachments_to_files: linked {linked} rows, folded {len(legacy)} legacy files")
