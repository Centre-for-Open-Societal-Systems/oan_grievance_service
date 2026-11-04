# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Name the assigned officer in the assignment messages, and print the deadline readably.

The Auto and Manual assignment messages said "assigned to {assigned_dept}", which reads
"None" when the case went to a desk with no department. They now name the officer, and the
deadline prints as `YYYY-MM-DD HH:mm` instead of the raw datetime. A message an admin has
reworded is left as it is: only one that still reads exactly as it was seeded is replaced.
Any other saved template that prints `doc.sla_due_date` gets the readable deadline too.
"""

import re

import frappe

from oan_grievance_service.services import constants as C
from oan_grievance_service.services.notifications import PLACEHOLDER_EXPRESSIONS, template_context
from oan_grievance_service.setup.install import NOTIFICATION_EVENTS, _translatable

OLD_MESSAGES = {
	C.EVENT_ASSIGNED_AUTO: (
		"Grievance {0} ({1} / {2}) has been assigned to {3}. SLA deadline {4}.",
		(
			"doc.ticket_number",
			"doc.service_category",
			"doc.grievance_type",
			"doc.assigned_dept",
			"doc.sla_due_date",
		),
	),
	C.EVENT_ASSIGNED_MANUAL: (
		"Grievance {0} has been assigned to {1} by the nodal officer. SLA deadline {2}.",
		("doc.ticket_number", "doc.assigned_dept", "doc.sla_due_date"),
	),
}

FORMAT_ARGS = re.compile(r"\.format\(([^()]*)\)")
FIELD = re.compile(r"(?<![\w.])doc\.sla_due_date(?!\w)")


def _rewrite(text):
	return FORMAT_ARGS.sub(
		lambda m: ".format(" + FIELD.sub(PLACEHOLDER_EXPRESSIONS["sla_due_date"], m.group(1)) + ")", text
	)


def execute():
	for event, (source, args) in OLD_MESSAGES.items():
		spec = next(e for e in NOTIFICATION_EVENTS if e[0] == event)
		for notification in frappe.get_all(
			"Notification",
			filters={"document_type": "Grievance", "event": "Method", "method": event},
			fields=["name", "channel", "message"],
		):
			context_key = template_context(event, notification.channel)
			if notification.message == _translatable(source, args, context_key):
				frappe.db.set_value(
					"Notification",
					notification.name,
					"message",
					_translatable(spec[5], spec[6], context_key),
					update_modified=False,
				)

	for row in frappe.get_all(
		"Notification", filters={"document_type": "Grievance"}, fields=["name", "subject", "message"]
	):
		subject, message = _rewrite(row.subject or ""), _rewrite(row.message or "")
		if subject != (row.subject or "") or message != (row.message or ""):
			frappe.db.set_value(
				"Notification", row.name, {"subject": subject, "message": message}, update_modified=False
			)
