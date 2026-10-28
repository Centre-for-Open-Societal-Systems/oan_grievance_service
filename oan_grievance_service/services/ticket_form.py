# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The Annexure form for one case, as plain data.

`build_form` reads the case once and returns the layout of the paper form: a reference block,
twelve numbered sections that follow the workflow, the whole timeline, and a footer. The
sections from the assignment onward are in `ticket_form_stages`; `ticket_render` turns the
result into a file, so the two formats cannot disagree about what the form says.

A form field the system never captures (priority, occupation, signature, stamp) is left
blank, as it is on paper, and never filled with a stand-in.
"""

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service import permissions
from oan_grievance_service.services import identity
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.services.administrative_area import (
	format_administrative_location,
	get_administrative_hierarchy,
)
from oan_grievance_service.services.ticket_form_blocks import (
	_checks,
	_field,
	_section,
	_size,
	_table,
	_time_zone,
	_when,
)
from oan_grievance_service.services.ticket_form_stages import (
	_appeal,
	_assignment,
	_closure,
	_decision,
	_investigation,
	_response,
)

# The export reads a whole case and renders it in the request worker, so each source is capped.
# A case past a cap is refused, not cut short: a record that looks complete and is not is
# worse than no record.
MAX_ROWS_PER_SOURCE = 1000


class ExportTooLarge(frappe.ValidationError):
	"""The case, or the file made from it, is past the size the export will build."""


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
			(_("Time Zone"), _time_zone()),
		],
	}


class _Source:
	"""What the form draws on for one case, read once and cut to what the caller may see."""

	def __init__(self, doc):
		self.doc = doc
		self.staff = permissions.is_staff()
		self.show_identity = permissions.can_see_identity(doc)
		self._people = {}
		self._staff_users = {}

		self.history = _read(
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
		self.timeline = _read(
			"Grievance Timeline",
			filters=timeline_filters,
			fields=["name", "entry_type", "is_internal", "author_user", "author_type", "created_on", "body"],
			order_by="created_on asc, name asc",
		)

		self.attachments = _read(
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

		self.feedback = _read(
			"Grievance Feedback",
			filters={"grievance": doc.name},
			fields=["submitted_at", "feedback_type", "rating", "comments"],
			order_by="submitted_at asc, name asc",
		)

		# Staff-only: how the complainant was reached and who moved the case between desks.
		self.channels = []
		self.reassignments = []
		if self.staff:
			self.channels = _read(
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
			if not self.is_staff_user(user):
				self._people[user] = _("Complainant")
			elif self.staff:
				self._people[user] = frappe.db.get_value("User", user, "full_name") or user
			else:
				self._people[user] = _("Officer")
		return self._people[user]

	def is_staff_user(self, user):
		"""Whether `user` is staff, looked up once per user however many entries they wrote."""
		if user not in self._staff_users:
			self._staff_users[user] = permissions.is_staff(user)
		return self._staff_users[user]

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


def _read(doctype, **query):
	"""All rows of `query`, or ExportTooLarge when there are more than the export will build."""
	rows = frappe.get_all(doctype, limit=MAX_ROWS_PER_SOURCE + 1, **query)
	if len(rows) > MAX_ROWS_PER_SOURCE:
		frappe.throw(_("This case has too many entries to export as one file."), ExportTooLarge)
	return rows


def _approved_reassignments(grievance):
	"""Approved change requests that moved the case between desks, each with its changes.

	Two queries, however many requests there are: the requests, then all their changes together.
	"""
	from oan_grievance_service.services.reassignment import TRIGGER_FIELDS

	requests = _read(
		"Grievance Change Request",
		filters={"grievance": grievance, "status": "Approved"},
		fields=["name", "decided_at", "decided_by"],
		order_by="decided_at asc, name asc",
	)
	if not requests:
		return []
	rows = _read(
		"Grievance Change Request Item",
		filters={
			"parenttype": "Grievance Change Request",
			"parent": ("in", [request.name for request in requests]),
			"fieldname": ("in", list(TRIGGER_FIELDS)),
		},
		fields=["parent", "fieldname", "old_value", "new_value"],
		order_by="parent asc, idx asc",
	)
	changes = {}
	for row in rows:
		changes.setdefault(row.parent, []).append(row)
	return [(request, changes[request.name]) for request in requests if request.name in changes]


# --- header ----------------------------------------------------------------


def _letterhead(s):
	"""The lines the Annexure prints above the title: the federation, the region, the filing office.

	The region and office come from the case's administrative area (the office is its woreda, or
	its zone where there is none), so the form carries the right names wherever it was filed.
	"""
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
	return format_administrative_location(doc.administrative_area) or doc.administrative_area


# --- 1 to 6: what the complainant submitted --------------------------------


def _complainant(s):
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
