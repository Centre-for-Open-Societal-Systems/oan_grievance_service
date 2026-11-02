# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Export one grievance as the SOW Annexure complaint form: who may, the audit row, the file.

`export_ticket` is the entry point. It checks the role and the case, builds the form
(`ticket_form`), renders it as CSV or PDF (`ticket_render`) and records the export. The size
of the case is capped (see `ticket_form`), a refusal never says whether a ticket exists, and
a file is only returned once its audit row is on record.

Who may export, and what the file holds, follows the same rules as reading the case: staff
get internal notes and officer names, a submitter gets the public record, and an anonymous
submitter's identity stays sealed unless the caller may see it. docs/ticket-export.md has the
mapping from the form to the data.
"""

import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.translate import print_language
from frappe.utils import now_datetime

from oan_grievance_service import permissions
from oan_grievance_service.services import audit
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.services.ticket_form import ExportTooLarge, build_form
from oan_grievance_service.services.ticket_render import render_csv, render_pdf

FORMAT_CSV = "csv"
FORMAT_PDF = "pdf"
FORMATS = (FORMAT_CSV, FORMAT_PDF)

_MIMETYPES = {FORMAT_CSV: "text/csv", FORMAT_PDF: "application/pdf"}

# The Annexure exists in these two languages (SOW). The labels go through Frappe's translation,
# so a language is supported once its wording is in translations/<code>.csv.
LANGUAGE_EN = "en"
LANGUAGE_AM = "am"
LANGUAGES = (LANGUAGE_EN, LANGUAGE_AM)

# What a long case can make the worker hold: a file past this is refused, not returned.
MAX_FILE_BYTES = 15 * 1024 * 1024

# The longest ticket text kept in an audit row for a number that matched no case.
AUDIT_TICKET_CHARS = 40

# Refusals are audited, and the gateway limit is per address, not per account. Past this many
# refusal rows from one account in a minute the refusal still happens but writes no more rows,
# so one account cannot fill the audit table with them.
MAX_DENIAL_ROWS_PER_MINUTE = 10


@dataclass
class ExportedForm:
	content: bytes
	filename: str
	mimetype: str


class ExportNotRecorded(Exception):
	"""The audit row could not be written, so the file was not handed over."""


# ---------------------------------------------------------------------------
# Entry point: authorise, build, render, audit
# ---------------------------------------------------------------------------


def export_ticket(ticket_number: str, file_format: str, lang: str = LANGUAGE_EN) -> ExportedForm:
	"""The form for one case, after the case check and before the audit write.

	The Grievance `export` permission is not checked here: the route's `require_permission`
	guard does, once, before the query is validated (the same guard the timeline route uses).

	`lang` is the language the form's own wording is printed in; what the complainant and the
	officers wrote stays as they wrote it. The error messages below follow the request's language.

	Every attempt that gets this far leaves one Grievance Access Audit Event: `export` when the
	file was produced, `export` marked Denied when the caller was refused (no access to the
	case, no such case), `export_failed` when the caller was cleared and the file could not
	be made. Each is committed before this returns or raises, because the API
	error handler rolls the request back. The row names the format and the reason, never
	anything from the complaint.

	The row for a file that left the system also carries its size and SHA-256, so a copy found
	later can be matched to the export that made it. It is written last, and if it cannot be
	written the file is not returned: nothing leaves without a record of it.

	A case the caller may not read is refused exactly as one that does not exist, so holding the
	export permission is not a way to find out which tickets exist. The audit row still says which.
	"""
	scope = f"format={file_format}; lang={lang}"

	name = _find_case(ticket_number)
	doc = frappe.get_doc("Grievance", name) if name else None
	if not doc:
		shown = (ticket_number or "").strip()[:AUDIT_TICKET_CHARS]
		_refuse_not_found(None, f"{scope}; ticket={shown}", "not_found")
	if not permissions.has_grievance_permission(doc, "read"):
		_refuse_not_found(name, scope, "case_access")

	try:
		with _in_language(lang):
			form = build_form(doc)
			content = render_csv(form) if file_format == FORMAT_CSV else render_pdf(form)
		if len(content) > MAX_FILE_BYTES:
			frappe.throw(_("This case is too large to export as one file."), ExportTooLarge)
	except Exception as exc:
		audit.record_access(
			audit.ACTION_EXPORT_FAILED,
			grievance=doc.name,
			scope=f"{scope}; reason=generation_error ({type(exc).__name__})",
			durable=True,
		)
		raise

	digest = hashlib.sha256(content).hexdigest()
	try:
		audit.record_access(
			audit.ACTION_EXPORT,
			grievance=doc.name,
			scope=f"{scope}; outcome=success; bytes={len(content)}; sha256={digest}",
			durable=True,
			strict=True,
		)
	except Exception as exc:
		raise ExportNotRecorded(
			"The export was not recorded in the audit trail; no file was returned."
		) from exc

	slug = re.sub(r"[^A-Za-z0-9]", "", doc.ticket_number or doc.name)
	suffix = "" if lang == LANGUAGE_EN else f"-{lang}"
	return ExportedForm(content, f"grievance-{slug}{suffix}.{file_format}", _MIMETYPES[file_format])


@contextmanager
def _in_language(lang):
	"""The form is built and rendered in `lang`, then the request's own language is restored.

	Frappe's print_language restores only when the block ends normally, so a render that fails
	would leave the rest of the request, its error message and its audit write included,
	in the form's language.
	"""
	previous = frappe.local.lang
	try:
		with print_language(lang):
			yield
	finally:
		frappe.local.lang = previous


def _find_case(ticket_number):
	"""The Grievance name for a ticket as a person typed it, or None."""
	value = (ticket_number or "").strip()
	if not value:
		return None
	normalized = tn.normalize(value)
	return (
		frappe.db.get_value("Grievance", {"ticket_number": normalized}, "name")
		or (normalized if frappe.db.exists("Grievance", normalized) else None)
		or (value if frappe.db.exists("Grievance", value) else None)
	)


def _refuse(name, scope, reason, message, exception=frappe.PermissionError, title=None):
	if _may_audit_denial():
		audit.log_denied(audit.ACTION_EXPORT, grievance=name, scope=f"{scope}; reason={reason}", durable=True)
	frappe.throw(message, exception, title=title or _("Forbidden"))


def _refuse_not_found(name, scope, reason):
	"""The one answer for a ticket that does not exist and one the caller may not read."""
	_refuse(
		name,
		scope,
		reason,
		_("No grievance found with that ticket number."),
		frappe.DoesNotExistError,
		_("Not Found"),
	)


def _may_audit_denial():
	"""Whether this account's refusal is written to the audit trail, or its minute's allowance is spent."""
	minute = int(now_datetime().timestamp() // 60)
	key = f"grievance:export-denials:{frappe.session.user}:{minute}"
	count = frappe.cache.get_value(key) or 0
	if count >= MAX_DENIAL_ROWS_PER_MINUTE:
		return False
	frappe.cache.set_value(key, count + 1, expires_in_sec=120)
	return True
