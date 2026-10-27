# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""A grievance and its timeline, laid out as the SOW Annexure complaint form.

The Annexure to the SOW (EAON_PLAN_SOW_Services_Grievance_D2, "Sample Manual Complaint
Process Form") is the paper form this module digitises: a reference block, then twelve
numbered sections that follow the workflow, from the complainant to closure. `build_form`
reads the case once and returns that layout as plain data; `render_csv` and `render_pdf`
turn the same data into a file, so the two formats cannot disagree about what the form says.

The data model has no record of its own for an investigation, a decision or an appeal. They
are read from what the workflow does leave behind:

- assignment: the Assigned moves in Grievance Status History, and approved reassignments;
- investigation: work started, information requested and answered, and internal notes; the
  findings are the Resolution Summary of the latest response (FSD Appendix D-1);
- decision: the Resolve, Partially Resolve and Reject moves, with their two-part reason;
- response: the public messages and department responses on the timeline;
- appeal: reopens, escalations and satisfaction feedback;
- closure: the moves into Closed or Rejected.

A form field the system never captures (priority, occupation, signature, stamp)
is left blank, as it is on paper, and never filled with a stand-in.

Who may export, and what the file holds, follows the same rules as reading the case: staff
get internal notes and officer names, a submitter gets the public record, and an anonymous
submitter's identity stays sealed unless the caller may see it.
"""

import csv
import html
import io
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import frappe
from frappe import _
from frappe.translate import print_language
from frappe.utils import get_datetime, now_datetime

from oan_grievance_service import permissions
from oan_grievance_service.services import audit, identity, response_body
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import ticket_number as tn

FORMAT_CSV = "csv"
FORMAT_PDF = "pdf"
FORMATS = (FORMAT_CSV, FORMAT_PDF)

_MIMETYPES = {FORMAT_CSV: "text/csv", FORMAT_PDF: "application/pdf"}

# The Annexure exists in these two languages (SOW). The labels go through Frappe's translation,
# so a language is supported once its wording is in translations/<code>.csv.
LANGUAGE_EN = "en"
LANGUAGE_AM = "am"
LANGUAGES = (LANGUAGE_EN, LANGUAGE_AM)

# Workflow actions that end in a decision, a start of work, an appeal. Names, not labels:
# they are what Grievance Status History stores.
DECISION_ACTIONS = ("Resolve", "Partially Resolve", "Reject")
ACTION_REOPEN = "Reopen"
ACTION_START_WORK = "In Progress"
ACTION_ASK_INFO = "Request More Info"
ACTION_REPLY = "Submitter Reply"
CLOSING_STATES = (C.STATE_CLOSED, C.STATE_REJECTED)

# Timeline entries that are part of the conversation with the complainant.
CONVERSATION_ENTRY_TYPES = ("response", "message")

# The longest ticket text kept in an audit row for a number that matched no case.
AUDIT_TICKET_CHARS = 40


@dataclass
class ExportedForm:
	content: bytes
	filename: str
	mimetype: str


# ---------------------------------------------------------------------------
# Entry point: authorise, build, render, audit
# ---------------------------------------------------------------------------


def export_ticket(ticket_number: str, file_format: str, lang: str = LANGUAGE_EN) -> ExportedForm:
	"""The form for one case, after the role check, the case check and the audit write.

	`lang` is the language the form's own wording is printed in; what the complainant and the
	officers wrote stays as they wrote it. The error messages below follow the request's language.

	Every attempt leaves one Grievance Access Audit Event: `export` when the file was
	produced, `export` marked Denied when the caller was refused (no export role, no access
	to the case, no such case), `export_failed` when the caller was cleared and the file
	could not be made. Each is committed before this returns or raises, because the API
	error handler rolls the request back. The row names the format and the reason, never
	anything from the complaint.
	"""
	scope = f"format={file_format}; lang={lang}"
	name = _find_case(ticket_number)

	# The role comes first and answers the same whether or not the case exists.
	if not frappe.has_permission("Grievance", "export"):
		_refuse(name, scope, "role", _("You do not have permission to export grievances."))

	if not name:
		shown = (ticket_number or "").strip()[:AUDIT_TICKET_CHARS]
		_refuse(
			None,
			f"{scope}; ticket={shown}",
			"not_found",
			_("No grievance found with that ticket number."),
			frappe.DoesNotExistError,
			_("Not Found"),
		)

	doc = frappe.get_doc("Grievance", name)
	if not permissions.has_grievance_permission(doc, "read"):
		_refuse(name, scope, "case_access", _("Not permitted to access this grievance."))

	try:
		with _in_language(lang):
			form = build_form(doc)
			content = render_csv(form) if file_format == FORMAT_CSV else render_pdf(form)
	except Exception as exc:
		audit.record_access(
			audit.ACTION_EXPORT_FAILED,
			grievance=doc.name,
			scope=f"{scope}; reason=generation_error ({type(exc).__name__})",
			durable=True,
		)
		raise

	audit.record_access(
		audit.ACTION_EXPORT, grievance=doc.name, scope=f"{scope}; outcome=success", durable=True
	)
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
	audit.log_denied(audit.ACTION_EXPORT, grievance=name, scope=f"{scope}; reason={reason}", durable=True)
	frappe.throw(message, exception, title=title or _("Forbidden"))


# ---------------------------------------------------------------------------
# The form
# ---------------------------------------------------------------------------


def build_form(doc) -> dict:
	"""The Annexure form for `doc`, as the caller is allowed to see it.

	Returns `title`, `letterhead` (the lines above it), `header` (label/value pairs), `sections`
	(numbered 1 to 12), `appendix` (the whole timeline, oldest first) and `footer`. A section holds
	`items` in the order the form prints them (labelled values, rows of boxes, printed wording),
	`rows` and `checks` (those items as pairs), `tables` and an optional `note`. Every value is a
	string.
	"""
	source = _Source(doc)
	return {
		"lang": frappe.local.lang,
		"title": _("Grievance / Complaint Submission Form"),
		"letterhead": _letterhead(source),
		"header": _header(source),
		"sections": [
			_complainant(source),
			_subject(source),
			_description(source),
			_key_details(source),
			_documents(source),
			_requested_action(source),
			_assignment(source),
			_investigation(source),
			_decision(source),
			_response(source),
			_appeal(source),
			_closure(source),
		],
		"appendix": _timeline_appendix(source),
		"footer": [
			(_("Exported By"), source.person(frappe.session.user)),
			(_("Exported On"), _when(now_datetime())),
		],
	}


class _Source:
	"""What the form draws on for one case, read once and cut to what the caller may see."""

	def __init__(self, doc):
		self.doc = doc
		self.staff = permissions.is_staff()
		self.show_identity = permissions.can_see_identity(doc)
		self._people = {}

		self.history = frappe.get_all(
			"Grievance Status History",
			filters={"grievance": doc.name},
			fields=[
				"name",
				"from_status",
				"to_status",
				"action",
				"is_automated",
				"changed_by",
				"timestamp",
				"reason",
				"notes",
			],
			order_by="timestamp asc, name asc",
		)

		timeline_filters = {"grievance": doc.name}
		if not self.staff:
			timeline_filters["is_internal"] = 0
		self.timeline = frappe.get_all(
			"Grievance Timeline",
			filters=timeline_filters,
			fields=["name", "entry_type", "is_internal", "author_user", "author_type", "created_on", "body"],
			order_by="created_on asc, name asc",
		)

		self.attachments = frappe.get_all(
			"Grievance Attachment",
			filters={"grievance": doc.name},
			fields=[
				"name",
				"file_name",
				"document_type",
				"mime_type",
				"size_bytes",
				"scan_status",
				"creation",
			],
			order_by="creation asc, name asc",
		)

		self.feedback = frappe.get_all(
			"Grievance Feedback",
			filters={"grievance": doc.name},
			fields=["submitted_at", "feedback_type", "rating", "comments"],
			order_by="submitted_at asc, name asc",
		)

		# Staff-only: how the complainant was reached and who moved the case between desks.
		self.channels = []
		self.reassignments = []
		if self.staff:
			self.channels = frappe.get_all(
				"Grievance Notification Log",
				filters={"grievance": doc.name, "status": "Sent"},
				pluck="channel",
				order_by="sent_at asc, name asc",
			)
			self.reassignments = _approved_reassignments(doc.name)

	def person(self, user, automated=False):
		"""How a user is named on the form: staff by name for staff, otherwise by role.

		A submitter is always "Complainant": the name is in section 1 when the caller may
		see it, and an anonymous case must not gain it from a timeline row.
		"""
		if automated or not user:
			return _("System")
		if user not in self._people:
			if not permissions.is_staff(user):
				self._people[user] = _("Complainant")
			elif self.staff:
				self._people[user] = frappe.db.get_value("User", user, "full_name") or user
			else:
				self._people[user] = _("Officer")
		return self._people[user]

	def author(self, entry):
		"""The author of a timeline entry, by the same rule as `person`."""
		if entry.author_user:
			return self.person(entry.author_user)
		return _("Complainant") if entry.author_type == "submitter" else _("System")

	def history_where(self, **match):
		"""History rows whose columns equal `match` (a tuple matches any of its values)."""
		rows = []
		for row in self.history:
			if all(
				row[key] in value if isinstance(value, tuple) else row[key] == value
				for key, value in match.items()
			):
				rows.append(row)
		return rows


def _approved_reassignments(grievance):
	"""Approved change requests that moved the case between desks, each with its changes."""
	from oan_grievance_service.services.reassignment import TRIGGER_FIELDS

	requests = []
	for name in frappe.get_all(
		"Grievance Change Request",
		filters={"grievance": grievance, "status": "Approved"},
		pluck="name",
		order_by="decided_at asc, name asc",
	):
		request = frappe.get_doc("Grievance Change Request", name)
		changes = [row for row in request.changes if row.fieldname in TRIGGER_FIELDS]
		if changes:
			requests.append((request, changes))
	return requests


# --- header ----------------------------------------------------------------


def _letterhead(s):
	"""The lines the Annexure prints above the title: the federation, the region, the filing office.

	The region and office come from the case's administrative area (the office is its woreda, or
	its zone where there is none), so the form carries the right names wherever it was filed.
	"""
	from oan_grievance_service.api.v1.administrative_area import get_administrative_hierarchy

	hierarchy = get_administrative_hierarchy(s.doc.administrative_area) or {}
	names = [
		name
		for name in dict.fromkeys((hierarchy.get("region"), hierarchy.get("woreda") or hierarchy.get("zone")))
		if name
	]
	return [
		_("Federal Democratic Republic of Ethiopia"),
		*[_("{0} Administration").format(name) for name in names],
	]


def _header(s):
	doc = s.doc
	return [
		(_("Grievance Reference No."), tn.display(doc.ticket_number)),
		(_("Date Submitted"), _when(doc.creation)),
		(_("Submission Office"), _location(doc)),
		(_("Grievance Category"), doc.service_category),
		(_("Priority"), ""),
		(_("Grievance Status"), doc.status),
		(_("Receiving Office"), doc.assigned_dept),
		(_("Document Type"), _("Grievance Record")),
	]


def _location(doc):
	from oan_grievance_service.api.v1.administrative_area import format_administrative_location

	return format_administrative_location(doc.administrative_area) or doc.administrative_area


# --- 1 to 6: what the complainant submitted --------------------------------


def _complainant(s):
	from oan_grievance_service.api.v1.administrative_area import get_administrative_hierarchy

	doc = s.doc
	contact = identity.mask_contact(doc, show_identity=s.show_identity)
	hierarchy = get_administrative_hierarchy(doc.administrative_area) or {}
	return _section(
		1,
		_("Complainant Information"),
		[
			_field(_("Full Name"), doc.submitter_name if s.show_identity else _("Anonymous Submitter")),
			_field(_("Submitter Type"), doc.submitter_type),
			_field(_("Address"), _location(doc)),
			_field(_("Kebele"), hierarchy.get("kebele")),
			_field(_("Administrative Unit"), doc.administrative_unit),
			_field(_("Telephone"), contact["contact_mobile"]),
			_field(_("Email"), contact["contact_email"]),
			_field(_("Occupation"), ""),
		],
	)


def _subject(s):
	return _section(
		2,
		_("Subject of the Grievance"),
		[_field(_("Subject"), _grievance_type_name(s.doc), show_label=False, block=True)],
	)


def _grievance_type_name(doc):
	if not doc.grievance_type:
		return ""
	return frappe.db.get_value("Grievance Type", doc.grievance_type, "type_name") or doc.grievance_type


def _description(s):
	return _section(
		3,
		_("Description of the Grievance"),
		[_field(_("Description"), s.doc.description, show_label=False, block=True)],
	)


def _key_details(s):
	doc = s.doc
	return _section(
		4,
		_("Key Details of the Grievance"),
		[
			_field(_("Service Category"), doc.service_category, numbered=True),
			_field(_("Service Provider"), doc.associated_service_provider, numbered=True),
			_field(_("Submission Channel"), doc.submission_channel, numbered=True),
			_field(_("Submitted Anonymously"), bool(doc.is_anonymous), numbered=True),
		],
	)


def _slug(text):
	return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _document_checklist():
	"""The Annexure's section 5 checklist, as (the words an attachment's document type must match, label).

	`Grievance Attachment.document_type` is free text with no list behind it, so an item is
	ticked only by an attachment that names it, ignoring case, spacing and punctuation. An
	attachment that names none of them, or nothing, ticks "Other Supporting Documents".
	"""
	return [
		("Copy of Identification Document", _("Copy of Identification Document")),
		("Previous Landholding Certificate", _("Previous Landholding Certificate")),
		("Land Registration Document", _("Land Registration Document")),
		("Site / Location Sketch", _("Site / Location Sketch")),
		("Previous Correspondence", _("Previous Correspondence")),
	]


def _documents(s):
	checklist = _document_checklist()
	ticked = set()
	other = False
	for attachment in s.attachments:
		match = next(
			(key for key, _label in checklist if _slug(key) == _slug(attachment.document_type)), None
		)
		if match:
			ticked.add(match)
		else:
			other = True
	options = [(label, key in ticked) for key, label in checklist]
	options.append((_("Other Supporting Documents"), other))

	rows = [
		[
			str(index),
			a.file_name,
			a.document_type,
			a.mime_type,
			_size(a.size_bytes),
			_when(a.creation),
			a.scan_status,
		]
		for index, a in enumerate(s.attachments, start=1)
	]
	return _section(
		5,
		_("Supporting Documents"),
		[
			_checks(_("Supporting Documents"), options, stacked=True, show_label=False),
			_field(_("Documents Attached"), len(s.attachments), pdf=False),
		],
		tables=[
			_table(
				_("Attached Files"),
				[
					_("No."),
					_("File Name"),
					_("Document Type"),
					_("File Type"),
					_("Size"),
					_("Uploaded"),
					_("Scan Status"),
				],
				rows,
			)
		],
		note=None if rows else _("No supporting documents were attached."),
	)


def _requested_action(s):
	return _section(
		6,
		_("Requested Action"),
		[_field(_("Requested Action"), s.doc.desired_outcome, show_label=False, block=True)],
	)


# --- 7: assignment ---------------------------------------------------------


def _assignment(s):
	doc = s.doc
	assigned = s.history_where(to_status=C.STATE_ASSIGNED)
	items = [
		_field(_("Date"), _when(assigned[0].timestamp) if assigned else ""),
		_field(_("Assigned Department"), doc.assigned_dept),
		_field(_("Assigned Officer"), s.person(doc.assigned_to) if s.staff and doc.assigned_to else ""),
		_field(_("Target Response Date"), _when(doc.sla_due_date)),
	]
	if s.staff:
		items.append(_field(_("Routed Automatically"), bool(doc.routed_automatically)))

	events = []
	for row in assigned:
		label = _("Referred onward") if row.action == "Refer Onward" else _("Assigned")
		events.append(
			(row.timestamp, label, _history_detail(s, row), s.person(row.changed_by, row.is_automated))
		)
	field_labels = {
		"assigned_dept": _("Department"),
		"assigned_to": _("Officer"),
		"service_category": _("Service Category"),
	}
	for request, changes in s.reassignments:
		detail = "; ".join(_reassignment_change(s, field_labels, c) for c in changes)
		events.append((request.decided_at, _("Reassignment approved"), detail, s.person(request.decided_by)))

	return _section(
		7,
		_("Initial Action by the Administration"),
		items,
		tables=[
			_table(_("Assignment History"), [_("Date"), _("Event"), _("Detail"), _("By")], _in_order(events))
		],
		note=None if events else _("The grievance has not been assigned."),
	)


def _value_for(s, fieldname, value):
	"""A reassignment's old or new value, with officers named the way the rest of the form names them."""
	if fieldname == "assigned_to" and value:
		return s.person(value)
	return value or ""


def _reassignment_change(s, field_labels, change):
	"""One changed field of an approved reassignment, as `Label: old -> new`."""
	label = field_labels.get(change.fieldname, change.fieldname)
	old = _value_for(s, change.fieldname, change.old_value)
	new = _value_for(s, change.fieldname, change.new_value)
	return f"{label}: {old} -> {new}"


def _history_detail(s, row):
	"""The reason on a history row; its internal notes only for staff."""
	if s.staff and row.notes:
		return f"{row.reason}\n{row.notes}" if row.reason else row.notes
	return row.reason


# --- 8: investigation ------------------------------------------------------


def _findings(s):
	"""What the investigation found, as the department reported it.

	FSD Appendix D-1 defines the response's Resolution Summary as "the investigation outcome,
	findings, and the resolution reached", so the findings are the summary of the latest
	Resolve or Partially Resolve. A rejection's reason is not a finding, and a response that was
	not written in two parts has no summary to take.
	"""
	for row in reversed(s.history_where(action=("Resolve", "Partially Resolve"))):
		parts = response_body.split(row.reason)
		if parts and parts["resolution_summary"]:
			return parts["resolution_summary"]
	return ""


def _investigation(s):
	started = s.history_where(action=ACTION_START_WORK)
	labels = {
		ACTION_START_WORK: _("Work started"),
		ACTION_ASK_INFO: _("More information requested"),
		ACTION_REPLY: _("Complainant replied"),
	}
	events = [
		(
			row.timestamp,
			labels[row.action],
			_history_detail(s, row),
			s.person(row.changed_by, row.is_automated),
		)
		for row in s.history_where(action=(ACTION_START_WORK, ACTION_ASK_INFO, ACTION_REPLY))
	]
	if s.staff:
		events += [
			(e.created_on, _("Internal note"), e.body, s.author(e))
			for e in s.timeline
			if e.entry_type == "note"
		]

	return _section(
		8,
		_("Investigation / Assessment"),
		[
			_field(_("Investigation Date"), _when(started[0].timestamp) if started else ""),
			_field(_("Findings"), _findings(s), block=True),
		],
		tables=[
			_table(
				_("Investigation Events"), [_("Date"), _("Event"), _("Detail"), _("By")], _in_order(events)
			)
		],
		note=None if events else _("No investigation activity has been recorded."),
	)


# --- 9: decision -----------------------------------------------------------


def _decision(s):
	decisions = s.history_where(action=DECISION_ACTIONS)
	rows = []
	for row in decisions:
		# Each reopen starts another round of review; a decision belongs to the round it ends.
		round_no = 1 + len([r for r in s.history_where(action=ACTION_REOPEN) if r.timestamp <= row.timestamp])
		parts = response_body.split(row.reason)
		rows.append(
			[
				str(round_no),
				_when(row.timestamp),
				row.action,
				row.to_status,
				s.person(row.changed_by, row.is_automated),
				parts["action_taken"] if parts else "",
				parts["resolution_summary"] if parts else row.reason,
			]
		)

	# The form has room for one decision; it shows the latest, and the table below has them all.
	latest = decisions[-1] if decisions else None
	parts = response_body.split(latest.reason) if latest else None
	determination = (parts["resolution_summary"] if parts else latest.reason) if latest else ""
	referred = bool(s.history_where(action="Refer Onward"))

	return _section(
		9,
		_("Decision / Resolution"),
		[
			_field(_("Decisions Recorded"), len(decisions), pdf=False),
			_field(_("Decision Date"), _when(latest.timestamp) if latest else ""),
			_field(_("Outcome"), latest.action if latest else ""),
			_static(_("Following review and verification, the responsible office determined that:")),
			_field(_("Determination"), determination, show_label=False, block=True),
			_field(_("Action Taken"), parts["action_taken"] if parts else ""),
			# Only a referral is a fact the workflow records; the other boxes are the department's
			# own words, which the form carries as the action taken.
			_checks(
				_("Corrective Action Required"),
				[
					(_("Correct official record"), False),
					(_("Conduct additional site verification"), False),
					(_("Refer to another responsible department"), referred),
					(_("Provide written clarification"), False),
					(_("No action required"), False),
					(_("Other"), False, ""),
				],
				stacked=True,
			),
		],
		tables=[
			_table(
				_("Decisions"),
				[
					_("Review Round"),
					_("Decision Date"),
					_("Outcome"),
					_("Resulting Status"),
					_("Decided By"),
					_("Corrective Action Taken"),
					_("Resolution Summary / Reason"),
				],
				rows,
			)
		],
		note=None if rows else _("No decision has been recorded."),
	)


# --- 10: communication with the complainant --------------------------------


def _response(s):
	rows = []
	officer_replies = []
	for entry in s.timeline:
		if entry.entry_type not in CONVERSATION_ENTRY_TYPES or entry.is_internal:
			continue
		from_officer = entry.author_type == "officer" or (
			entry.author_user and permissions.is_staff(entry.author_user)
		)
		if from_officer:
			officer_replies.append(entry)
		kind = _("Department response") if entry.entry_type == "response" else _("Message")
		rows.append(
			(
				entry.created_on,
				# FSD D-3: each department response carries its sequence number (Response #1, #2).
				len(officer_replies) if from_officer else "",
				kind,
				_("Department") if from_officer else _("Complainant"),
				s.author(entry),
				entry.body,
			)
		)

	# Staff see which channels sent: SMS is a box of its own, any other channel goes under Other.
	others = sorted({channel for channel in s.channels if channel != "SMS"})
	items = [
		_field(_("Response Date"), _when(officer_replies[0].created_on) if officer_replies else ""),
		_static(
			_("The complainant was informed of the decision and actions taken by the responsible office.")
		),
		_checks(
			_("Communication Method"),
			[
				(_("Written Letter"), False),
				# The same English word is the complainant's phone number in section 1; the
				# context keeps the two Amharic wordings apart.
				(_("Telephone", context="Communication Method"), False),
				(_("SMS"), "SMS" in s.channels),
				(_("In Person"), False),
				(_("Other"), bool(others), ", ".join(others)),
			],
		),
		_field(_("Responses Provided"), len(officer_replies), pdf=False),
	]
	if s.staff:
		items.append(_field(_("Notified By"), ", ".join(sorted(set(s.channels))), pdf=False))

	return _section(
		10,
		_("Communication to the Complainant"),
		items,
		tables=[
			_table(
				_("Communication"),
				[_("Date"), _("Response No."), _("Type"), _("From"), _("By"), _("Text")],
				_in_order(rows),
			)
		],
		note=None if rows else _("No response has been recorded."),
	)


# --- 11: appeal, reopen, escalation ----------------------------------------


def _appeal(s):
	doc = s.doc
	reopens = s.history_where(action=ACTION_REOPEN)
	escalations = [e for e in s.timeline if e.entry_type == "escalation"]
	events = [(r.timestamp, _("Reopened"), r.reason, s.person(r.changed_by, r.is_automated)) for r in reopens]
	events += [(e.created_on, _("Escalated"), e.body, s.author(e)) for e in escalations]

	ratings = [[_when(f.submitted_at), f.feedback_type, f"{f.rating}/5", f.comments] for f in s.feedback]
	latest = s.feedback[-1] if s.feedback else None

	appeal_note = _(
		"If the complainant is not satisfied with the decision, the complainant may submit an appeal "
		"or request further review through the appropriate administrative channel."
	)

	return _section(
		11,
		_("Appeal / Further Review"),
		[
			_static(appeal_note),
			# A reopen is the complainant asking for further action. The 1 to 5 rating has no
			# documented line between satisfied and not, so it is shown as given, not as a box.
			_checks(
				_("Complainant Satisfaction"),
				[
					(_("Satisfied"), False),
					(_("Not Satisfied"), False),
					(_("Further Action Requested"), bool(reopens)),
				],
			),
			_field(_("Satisfaction Rating"), f"{latest.rating}/5" if latest else ""),
			_field(_("Reason for Further Action"), reopens[-1].reason if reopens else "", block=True),
			_field(_("Reopen Count"), doc.reopen_count or 0),
			_field(_("Escalated"), bool(doc.escalated)),
			_field(_("Escalated At"), _when(doc.escalated_at)),
		],
		tables=[
			_table(_("Appeal Events"), [_("Date"), _("Event"), _("Detail"), _("By")], _in_order(events)),
			_table(_("Satisfaction Feedback"), [_("Date"), _("Type"), _("Rating"), _("Comments")], ratings),
		],
		note=None if events else _("No appeal, reopen or escalation has been recorded."),
	)


# --- 12: closure -----------------------------------------------------------

# The moves that settle where a case stands, for the Final Status boxes.
SETTLING_ACTIONS = (
	ACTION_REOPEN,
	"Resolve",
	"Partially Resolve",
	"Close Case",
	"Auto Close",
	"Reject",
	"Refer Onward",
)


def _closure(s):
	doc = s.doc
	closings = s.history_where(to_status=CLOSING_STATES)
	closing = closings[-1] if closings else None
	settling = s.history_where(action=SETTLING_ACTIONS)
	last = settling[-1].action if settling else None
	events = [
		(r.timestamp, r.action, r.to_status, _history_detail(s, r), s.person(r.changed_by, r.is_automated))
		for r in closings
	]
	return _section(
		12,
		_("Closure"),
		[
			_checks(
				_("Final Status"),
				[
					(_("Resolved"), doc.status == C.STATE_RESOLVED),
					(_("Closed"), doc.status == C.STATE_CLOSED),
					(_("Referred"), last == "Refer Onward"),
					(_("Escalated"), bool(doc.escalated)),
					(_("Reopened"), last == ACTION_REOPEN),
				],
			),
			# The boxes have none for Rejected or In Progress, so the plain status is kept for the data.
			_field(_("Current Status"), doc.status, pdf=False),
			_field(_("Closure Date"), _when(closing.timestamp) if closing else ""),
			_field(_("Closure Remarks"), (closing.reason or doc.closure_reason) if closing else ""),
			_field(_("Closed By"), s.person(closing.changed_by, closing.is_automated) if closing else ""),
			_field(_("Resolved At"), _when(doc.resolved_at), pdf=False),
			_field(
				_("Responsible Officer"), s.person(doc.assigned_to) if s.staff and doc.assigned_to else ""
			),
			_field(_("Signature"), ""),
			_field(_("Official Stamp"), ""),
		],
		tables=[
			_table(
				_("Closure History"),
				[_("Date"), _("Action"), _("Status"), _("Remarks"), _("By")],
				_in_order(events),
			)
		],
		note=None if closing else _("The grievance has not been closed."),
	)


# --- appendix: the whole timeline ------------------------------------------


def _timeline_appendix(s):
	columns = [_("No."), _("Date"), _("Type"), _("By")]
	if s.staff:
		columns.append(_("Visibility"))
	columns.append(_("Text"))
	rows = []
	for index, entry in enumerate(s.timeline, start=1):
		row = [str(index), _when(entry.created_on), entry.entry_type, s.author(entry)]
		if s.staff:
			row.append(_("Internal") if entry.is_internal else _("Public"))
		row.append(entry.body)
		rows.append(row)
	return _table(_("Timeline"), columns, rows)


# --- building blocks -------------------------------------------------------


def _field(label, value, *, pdf=True, show_label=True, block=False, numbered=False):
	"""A labelled value. `pdf=False` keeps it to the CSV; `block` puts the value on lines of its own
	(ruled blanks when empty); `numbered` prints it in a numbered list."""
	return {
		"kind": "field",
		"label": label,
		"value": _text(value),
		"pdf": pdf,
		"show_label": show_label,
		"block": block,
		"numbered": numbered,
	}


def _checks(label, options, *, stacked=False, show_label=True):
	"""A row of boxes, each ticked or not. An option is (text, checked), or (text, checked, detail)
	where the detail is the free text that follows an "Other" box."""
	return {
		"kind": "checks",
		"label": label,
		"stacked": stacked,
		"show_label": show_label,
		"options": [
			{
				"text": option[0],
				"checked": bool(option[1]),
				"detail": option[2] if len(option) > 2 else None,
			}
			for option in options
		],
	}


def _static(text):
	"""Wording printed on the paper form itself; the PDF shows it and the CSV leaves it out."""
	return {"kind": "static", "text": text}


def _ticked(option):
	return f"{option['text']}: {option['detail']}" if option["detail"] else option["text"]


def _section(number, title, items=(), tables=(), note=None):
	items = list(items)
	return {
		"number": number,
		"title": title,
		"items": items,
		"rows": [(i["label"], i["value"]) for i in items if i["kind"] == "field"],
		"checks": [
			(i["label"], [_ticked(o) for o in i["options"] if o["checked"]])
			for i in items
			if i["kind"] == "checks"
		],
		"tables": [table for table in tables if table["rows"]],
		"note": note,
	}


def _table(title, columns, rows):
	return {"title": title, "columns": columns, "rows": [[_text(cell) for cell in row] for row in rows]}


def _in_order(events):
	"""Event tuples (when, *cells) as rows, oldest first; ties keep the order they were read in."""
	ordered = sorted(events, key=lambda event: get_datetime(event[0]) if event[0] else datetime.min)
	return [[_when(event[0]), *event[1:]] for event in ordered]


def _when(value):
	return get_datetime(value).strftime("%Y-%m-%d %H:%M") if value else ""


def _size(size_bytes):
	if not size_bytes:
		return ""
	return f"{size_bytes / 1024:.1f} KB" if size_bytes >= 1024 else f"{size_bytes} B"


def _text(value):
	if value is None:
		return ""
	if isinstance(value, bool):
		return _("Yes") if value else _("No")
	return str(value)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

# A cell that opens with one of these is run as a formula by a spreadsheet. Complaint text
# is typed by the public, so the cell is defused rather than trusted. A plain number or phone
# number is left alone: it cannot carry a formula.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_NUMBERLIKE = re.compile(r"^[+-]?[\d\s().-]+$")


def _csv_safe(text):
	if text and text.startswith(_FORMULA_LEAD) and not _NUMBERLIKE.match(text):
		return "'" + text
	return text


def render_csv(form) -> bytes:
	"""The form as UTF-8 CSV with a byte-order mark, so a spreadsheet reads Amharic correctly.

	One fact per line: Section, Entry, Field, Value. A key/value row has no Entry; a row of
	boxes is one line whose value is the boxes ticked; a table row is named by its table and
	position ("Decisions 2"), one line per filled cell.
	"""
	out = io.StringIO(newline="")
	writer = csv.writer(out, lineterminator="\r\n")

	def put(section, entry, field, value):
		writer.writerow([_csv_safe(section), _csv_safe(entry), _csv_safe(field), _csv_safe(value)])

	writer.writerow([_("Section"), _("Entry"), _("Field"), _("Value")])
	put(form["title"], "", "", "")
	for label, value in form["header"]:
		put(_("Reference"), "", label, value)
	for section in form["sections"]:
		heading = f"{section['number']}. {section['title']}"
		for item in section["items"]:
			if item["kind"] == "field":
				put(heading, "", item["label"], item["value"])
			elif item["kind"] == "checks":
				put(
					heading, "", item["label"], ", ".join(_ticked(o) for o in item["options"] if o["checked"])
				)
		for table in section["tables"]:
			_put_table(put, heading, table)
		if section["note"]:
			put(heading, "", _("Note"), section["note"])
	if form["appendix"]["rows"]:
		_put_table(put, _("Appendix: Timeline"), form["appendix"])
	for label, value in form["footer"]:
		put(_("Export"), "", label, value)
	return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def _put_table(put, heading, table):
	for index, row in enumerate(table["rows"], start=1):
		for column, cell in zip(table["columns"], row, strict=True):
			if cell:
				put(heading, f"{table['title']} {index}", column, cell)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

BOX_EMPTY = "☐"
BOX_TICKED = "☒"

# Latin first, as the paper form is set; the Ethiopic faces follow so Amharic complaint text
# renders where one is installed (the server image has to carry Noto Sans Ethiopic for that).
_PDF_STYLE = """
@page { size: A4; margin: 15mm 16mm 18mm;
	@bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #555; } }
body { font-family: "Liberation Sans", Arial, "Noto Sans Ethiopic", "Abyssinica SIL", "Nyala",
	"DejaVu Sans", sans-serif; font-size: 9.5pt; color: #000; }
p.lh { text-align: center; font-weight: bold; text-transform: uppercase; margin: 0 0 7pt; font-size: 12pt; }
p.lh.first { font-size: 13pt; }
p.lh.last { font-size: 14pt; margin-bottom: 12pt; }
h1 { text-align: center; font-size: 13pt; text-transform: uppercase; margin: 4pt 0 10pt; }
table.meta { width: 100%; border-collapse: collapse; margin-bottom: 4pt; }
table.meta td { border: 1px solid #000; padding: 2pt 4pt; vertical-align: top; width: 31%;
	overflow-wrap: break-word; }
table.meta td.hl { width: 19%; font-weight: bold; background: #e3eef7; }
h2 { font-size: 10pt; text-transform: uppercase; margin: 10pt 0 4pt; page-break-after: avoid; }
h3 { font-size: 9pt; margin: 8pt 0 3pt; color: #222; page-break-after: avoid; }
p.line, p.text, p.opt { margin: 0 0 4.5pt; overflow-wrap: break-word; }
p.text { white-space: pre-wrap; }
p.opt { margin-bottom: 3.5pt; }
span.opt { margin-right: 12pt; white-space: nowrap; }
.blank { display: inline-block; min-width: 150pt; height: 10pt; border-bottom: 1px solid #000;
	vertical-align: bottom; }
.rule { height: 15pt; border-bottom: 1px solid #000; margin: 0 0 3pt; }
table.grid { width: 100%; border-collapse: collapse; }
table.grid td, table.grid th { padding: 3pt 5pt; vertical-align: top; border: 1px solid #888; }
table.grid th { background: #eee; text-align: left; font-size: 8.5pt; }
table.grid td { white-space: pre-wrap; overflow-wrap: break-word; font-size: 8.5pt; }
table.grid tr { page-break-inside: avoid; }
.note { margin: 3pt 0; font-style: italic; color: #444; }
.appendix { page-break-before: always; }
.footer { margin-top: 14pt; font-size: 8pt; color: #444; }
"""


def render_pdf(form) -> bytes:
	"""The form as a PDF, laid out like the paper original: letterhead, reference block, numbered sections."""
	from weasyprint import HTML

	# Every value is escaped and the page has no links or images, so there is nothing to
	# fetch; refusing fetches outright keeps it that way.
	def refuse_fetch(url, *args, **kwargs):
		raise ValueError("External resources are not loaded in exports.")

	return HTML(string=_pdf_html(form), url_fetcher=refuse_fetch).write_pdf()


def _pdf_html(form):
	def esc(value):
		return html.escape(value or "")

	blank = '<span class="blank"></span>'

	def ruled(count=3):
		return '<div class="rule"></div>' * count

	def grid(table):
		head = "".join(f"<th>{esc(column)}</th>" for column in table["columns"])
		body = "".join(
			"<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in table["rows"]
		)
		return (
			f'<h3>{esc(table["title"])}</h3><table class="grid"><thead><tr>{head}</tr></thead>'
			f"<tbody>{body}</tbody></table>"
		)

	def option(o):
		box = BOX_TICKED if o["checked"] else BOX_EMPTY
		detail = ""
		if o["detail"] is not None:
			detail = f" {esc(o['detail'])}" if o["detail"] else f" {blank}"
		return f"{box} {esc(o['text'])}{':' if o['detail'] is not None else ''}{detail}"

	def render(item, counter):
		kind = item["kind"]
		if kind == "static":
			return f'<p class="text">{esc(item["text"])}</p>'
		if kind == "checks":
			label = f'{esc(item["label"])}:' if item["show_label"] else ""
			if item["stacked"]:
				head = f'<p class="line">{label}</p>' if label else ""
				return head + "".join(f'<p class="opt">{option(o)}</p>' for o in item["options"])
			boxes = " ".join(f'<span class="opt">{option(o)}</span>' for o in item["options"])
			return f'<p class="line">{label} {boxes}</p>'
		if not item["pdf"]:
			return ""
		value = item["value"]
		if item["block"]:
			head = f'<p class="line">{esc(item["label"])}:</p>' if item["show_label"] else ""
			return head + (f'<p class="text">{esc(value)}</p>' if value else ruled())
		prefix = f"{counter}. " if item["numbered"] else ""
		return f'<p class="line">{prefix}{esc(item["label"])}: {esc(value) if value else blank}</p>'

	lines = form["letterhead"]
	letterhead = "".join(
		f'<p class="lh{" first" if index == 0 else ""}{" last" if index == len(lines) - 1 else ""}">{esc(line)}</p>'
		for index, line in enumerate(lines)
	)

	header = form["header"]
	meta = ""
	for index in range(0, len(header), 2):
		meta += (
			"<tr>"
			+ "".join(
				f'<td class="hl">{esc(label)}</td><td>{esc(value)}</td>'
				for label, value in header[index : index + 2]
			)
			+ "</tr>"
		)

	sections = ""
	for section in form["sections"]:
		sections += f"<h2>{section['number']}. {esc(section['title'])}</h2>"
		counter = 0
		for item in section["items"]:
			if item["kind"] == "field" and item["numbered"] and item["pdf"]:
				counter += 1
			sections += render(item, counter)
		sections += "".join(grid(table) for table in section["tables"])
		if section["note"]:
			sections += f'<p class="note">{esc(section["note"])}</p>'

	appendix = ""
	if form["appendix"]["rows"]:
		appendix = (
			f'<div class="appendix"><h2>{esc(_("Appendix: Timeline"))}</h2>{grid(form["appendix"])}</div>'
		)

	footer = " &middot; ".join(f"{esc(label)}: {esc(value)}" for label, value in form["footer"])
	return (
		f'<!doctype html><html lang="{esc(form.get("lang") or LANGUAGE_EN)}"><head><meta charset="utf-8">'
		f'<title>{esc(form["title"])}</title>'
		f"<style>{_PDF_STYLE}</style></head><body>"
		f"{letterhead}<h1>{esc(form['title'])}</h1><table class=\"meta\">{meta}</table>{sections}{appendix}"
		f'<p class="footer">{footer}</p></body></html>'
	)
