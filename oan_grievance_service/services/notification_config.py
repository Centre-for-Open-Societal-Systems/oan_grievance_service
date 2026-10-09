# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Read side of the Administration Notification Configuration tab (FSD Appendix C).

The matrix is not stored as its own table. Each event is one core Notification record per
channel (see setup/install.py), and those records are what `notifications.queue` reads when
it sends, so they stay the single source of truth: what this module reports is what goes
out. Appendix C numbers its events EC-001 to EC-019; the records are keyed by event code
(`Notification.method`). `EVENTS` is the bridge, and also holds what the records do not:
the display title and the trigger description.

Two translations happen on the way out, so the payload matches the frontend's
`NotificationConfig` type:

- Subject and body are stored as translatable Jinja (`{{ _('...{0}', context=...).format(
  doc.ticket_number) }}`). They are returned as the English source with `{{id}}`-style
  tokens, which is what an administrator reads and edits.
- A channel is a separate record, so an event's `channel` list and `active` flag are
  folded together from its records.
"""

import ast
import re
from datetime import UTC
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe.utils import get_datetime, get_system_timezone

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import notifications

CHANNEL_ORDER = (notifications.CHANNEL_SMS, notifications.CHANNEL_EMAIL)

# The recipient roles the records carry, in the names the Administration UI shows.
RECIPIENT_LABELS = {
	notifications.RECIPIENT_SUBMITTER: "Submitter",
	notifications.RECIPIENT_ASSIGNED_OFFICER: "L1 Officer",
	notifications.RECIPIENT_DEPARTMENT_OFFICER: "L1 Officer",
	notifications.RECIPIENT_NODAL_OFFICER: "Nodal Officer",
	notifications.RECIPIENT_DEPARTMENT_HEAD: "Dept Head",
	notifications.RECIPIENT_TOP_LEVEL: "L2 Officer",
}


class EventDefinition(NamedTuple):
	"""One row of Appendix C.

	`code` is the event code on the Notification records, None where the backend sends no
	event of its own. `channels` and `recipients` are the FSD's, used only when there is no
	record to read them from.
	"""

	id: str
	code: str | None
	title: str
	trigger: str
	channels: tuple[str, ...]
	recipients: tuple[str, ...]


SMS_EMAIL = ("SMS", "Email")

EVENTS = (
	EventDefinition(
		"EC-001",
		C.EVENT_SUBMISSION_RECEIVED,
		"Submission Received",
		"Immediately on save",
		SMS_EMAIL,
		("Submitter",),
	),
	EventDefinition(
		"EC-002",
		C.EVENT_DUPLICATE_DETECTED,
		"Duplicate Detected",
		"On validation",
		SMS_EMAIL,
		("Submitter",),
	),
	EventDefinition(
		"EC-003",
		C.EVENT_ASSIGNED_AUTO,
		"Grievance Assigned (Auto-routing)",
		"On auto-routing match",
		("Email",),
		("L1 Officer",),
	),
	EventDefinition(
		"EC-004",
		C.EVENT_ASSIGNED_MANUAL,
		"Grievance Assigned (Manual Routing)",
		"On nodal officer assignment",
		("Email",),
		("L1 Officer",),
	),
	EventDefinition(
		"EC-005",
		C.EVENT_STATUS_IN_PROGRESS,
		"Status → In Progress",
		"Officer accepts ticket",
		("SMS",),
		("Submitter",),
	),
	EventDefinition(
		"EC-006",
		C.EVENT_MORE_INFO_REQUESTED,
		"More Information Requested",
		"Officer sets More Info Needed",
		SMS_EMAIL,
		("Submitter",),
	),
	EventDefinition(
		"EC-007",
		C.EVENT_SUBMITTER_RESPONDED,
		"Submitter Responds to Info Request",
		"Submitter provides requested info",
		("Email",),
		("L1 Officer",),
	),
	EventDefinition(
		"EC-008",
		C.EVENT_RESPONSE_SENT,
		"Structured Response Sent to Submitter",
		"Officer submits structured response",
		SMS_EMAIL,
		("Submitter",),
	),
	EventDefinition(
		"EC-009",
		C.EVENT_CONFIRMATION_WINDOW,
		"Confirmation Window Open",
		"Response submitted",
		("SMS",),
		("Submitter",),
	),
	EventDefinition(
		"EC-010",
		C.EVENT_CONFIRMED,
		"Grievance Confirmed / Resolved",
		"Submitter confirms satisfaction",
		SMS_EMAIL,
		("Submitter",),
	),
	EventDefinition(
		"EC-011",
		C.EVENT_REOPENED,
		"Grievance Reopened",
		"Submitter reopens grievance",
		("Email",),
		("L1 Officer",),
	),
	EventDefinition(
		"EC-012",
		C.EVENT_AUTO_CLOSED,
		"Auto-closed (No Response)",
		"Confirmation window expires",
		SMS_EMAIL,
		("Submitter",),
	),
	EventDefinition(
		"EC-013",
		C.EVENT_SLA_REMINDER_50,
		"SLA Reminder — 50%",
		"Scheduled job at 50% SLA elapsed",
		("Email",),
		("L1 Officer",),
	),
	EventDefinition(
		"EC-014",
		C.EVENT_SLA_REMINDER_80,
		"SLA Reminder — 80%",
		"Scheduled job at 80% SLA elapsed",
		("Email",),
		("L1 Officer",),
	),
	EventDefinition(
		"EC-015",
		C.EVENT_SLA_AT_RISK,
		"SLA At-Risk Report (Nodal Officer)",
		"Scheduled job at 80% SLA elapsed",
		("Email",),
		("Nodal Officer",),
	),
	EventDefinition(
		"EC-016",
		C.EVENT_SLA_BREACH,
		"SLA Breached — L1 Escalation",
		"SLA deadline passed",
		("Email",),
		("Dept Head", "Nodal Officer"),
	),
	# The escalation ladder sends one `sla_breached` event whatever rung the case lands on,
	# and that event is EC-016's. EC-017 has no record of its own, so it reports as inactive.
	EventDefinition(
		"EC-017",
		None,
		"SLA Breached — L2 Escalation",
		"2× SLA deadline passed",  # noqa: RUF001 - the frontend's wording
		("Email",),
		("L2 Officer",),
	),
	EventDefinition(
		"EC-018",
		C.EVENT_MANUAL_ESCALATION,
		"Manual Escalation by Submitter",
		"Submitter triggers escalation via portal",
		("Email",),
		("Dept Head", "Nodal Officer"),
	),
	EventDefinition(
		"EC-019",
		C.EVENT_REASSIGNMENT_REQUESTED,
		"Reassignment Requested",
		"Officer requests reassignment",
		("Email",),
		("Nodal Officer",),
	),
)

EVENTS_BY_ID = {event.id: event for event in EVENTS}

NOTIFICATION_FIELDS = [
	"method",
	"channel",
	"enabled",
	"subject",
	"message",
	"grievance_recipient",
	"modified",
]


# Template display
# ----------------

# What `Notification.message` is made of, as written by `setup.install._translatable`: a
# `_()` call on a string literal, with a context key and the document fields to fill in.
_STRING = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\""""
_TRANSLATED = re.compile(
	r"\{\{\s*_\(\s*(?P<text>" + _STRING + r")\s*(?:,\s*context\s*=\s*(?:" + _STRING + r")\s*)?\)"
	r"(?:\s*\.format\((?P<args>[^)]*)\))?\s*\}\}"
)
_CONDITIONAL = re.compile(r"\{%\s*if\s.*?%\}(?P<then>.*?)\{%\s*else\s*%\}.*?\{%\s*endif\s*%\}", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_DOC_FIELD = re.compile(r"doc\.(\w+)")

# Document fields whose token is not just the camel-cased field name.
FIELD_TOKENS = {
	"ticket_number": "id",
	"service_category": "category",
	"assigned_dept": "dept",
	"sla_due_date": "slaDeadline",
	"assigned_to": "officerName",
}


def _token(expression: str) -> str:
	expression = expression.strip()
	match = _DOC_FIELD.fullmatch(expression)
	if not match:
		return "{{" + expression + "}}"
	field = match.group(1)
	if field in FIELD_TOKENS:
		return "{{" + FIELD_TOKENS[field] + "}}"
	head, *rest = field.split("_")
	return "{{" + head + "".join(part.capitalize() for part in rest) + "}}"


def _display_text(match: re.Match) -> str:
	text = ast.literal_eval(match.group("text"))
	args = [_token(arg) for arg in (match.group("args") or "").split(",") if arg.strip()]
	return _PLACEHOLDER.sub(
		lambda hole: args[int(hole.group(1))] if int(hole.group(1)) < len(args) else hole.group(0),
		text,
	)


def display_template(stored: str | None) -> str:
	"""A stored subject or body as the administrator sees it: English, with `{{id}}` tokens.

	A conditional keeps the sentence for when the field is set, since the other is only a
	fallback. Anything that is not the seeded shape, such as wording an administrator has
	rewritten in plain Jinja, comes back as stored rather than guessed at.
	"""
	if not stored:
		return ""
	text = _TRANSLATED.sub(_display_text, stored)
	text = _CONDITIONAL.sub(lambda match: match.group("then"), text)
	return re.sub(r"[ \t]{2,}", " ", text).strip()


# Events
# ------


def _channel_rank(channel: str) -> int:
	return CHANNEL_ORDER.index(channel) if channel in CHANNEL_ORDER else len(CHANNEL_ORDER)


def _timestamp(value) -> str | None:
	"""Frappe stores local naive datetimes. The API speaks UTC, as the frontend mock does."""
	if not value:
		return None
	moment = get_datetime(value)
	try:
		moment = moment.replace(tzinfo=ZoneInfo(get_system_timezone()))
	except ZoneInfoNotFoundError:
		moment = moment.replace(tzinfo=UTC)
	return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unique(items) -> list[str]:
	return list(dict.fromkeys(items))


def _build(definition: EventDefinition, records: list) -> dict:
	"""One event, from its Notification records (empty when the backend has none)."""
	event = {
		"id": definition.id,
		"title": definition.title,
		"eventType": definition.code,
		"trigger": definition.trigger,
		"template": "",
		"lastEdited": None,
	}
	if not records:
		return {
			**event,
			"active": False,
			"channel": list(definition.channels),
			"subject": "",
			"recipients": list(definition.recipients),
		}

	records = sorted(records, key=lambda record: _channel_rank(record.channel))
	enabled = [record for record in records if record.enabled]
	# Email is the richer of the two renderings, so it supplies the wording.
	wording = next((r for r in records if r.channel == notifications.CHANNEL_EMAIL), records[0])
	# A switched-off event still lists the channels it would use, so the card has them to show.
	channels = _unique(record.channel for record in (enabled or records))
	last_edited = max((record.modified for record in records if record.modified), default=None)

	event.update(
		active=bool(enabled),
		channel=[channel for channel in channels if channel in CHANNEL_ORDER],
		subject=display_template(wording.subject),
		template=display_template(wording.message),
		lastEdited=_timestamp(last_edited),
		recipients=_unique(
			RECIPIENT_LABELS.get(
				record.grievance_recipient or notifications.RECIPIENT_SUBMITTER,
				record.grievance_recipient,
			)
			for record in records
		),
	)
	return event


def _records_by_code(codes: list[str]) -> dict[str, list]:
	by_code: dict[str, list] = {}
	if not codes:
		return by_code
	for record in frappe.get_all(
		"Notification",
		filters={"document_type": "Grievance", "event": "Method", "method": ["in", codes]},
		fields=NOTIFICATION_FIELDS,
		order_by="modified asc, name asc",
	):
		by_code.setdefault(record.method, []).append(record)
	return by_code


def list_events() -> list[dict]:
	"""Every Appendix C event, in EC order."""
	records = _records_by_code([event.code for event in EVENTS if event.code])
	return [_build(event, records.get(event.code, [])) for event in EVENTS]


def get_event(event_id: str) -> dict | None:
	"""One event by its EC id, or None when Appendix C has no such id."""
	definition = EVENTS_BY_ID.get(event_id.strip().upper())
	if not definition:
		return None
	records = _records_by_code([definition.code] if definition.code else [])
	return _build(definition, records.get(definition.code, []))
