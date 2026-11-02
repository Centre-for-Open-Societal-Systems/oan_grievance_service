# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Sections 7 to 12 of the Annexure: assignment, investigation, decision, response, appeal, closure.

The data model has no record of its own for an investigation, a decision or an appeal. They
are read from what the workflow does leave behind:

- assignment: the Assigned moves in Grievance Status History, and approved reassignments;
- investigation: work started, information requested and answered, and internal notes; the
  findings are the Resolution Summary of the latest response (FSD Appendix D-1);
- decision: the Resolve, Partially Resolve and Reject moves, with their two-part reason;
- response: the public messages and department responses on the timeline;
- appeal: reopens, escalations and satisfaction feedback;
- closure: the moves into Closed or Rejected.
"""

from frappe import _

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import response_body
from oan_grievance_service.services.ticket_form_blocks import (
	_checks,
	_field,
	_in_order,
	_section,
	_static,
	_table,
	_when,
)

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
			entry.author_user and s.is_staff_user(entry.author_user)
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
			# Signing and stamping stay on paper (docs/ticket-export.md §6): nothing is captured
			# for them, so the PDF leaves a line to be filled in by hand.
			_field(_("Signature"), "", blank_line=True),
			_field(_("Official Stamp"), "", blank_line=True),
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
