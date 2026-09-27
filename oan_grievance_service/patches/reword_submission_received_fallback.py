# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Give the Submission Received acknowledgement a fallback for unrouted cases.

The seeded template printed `doc.sla_due_date` directly. The SLA clock starts at
assignment, so a case no routing rule matched has no due date when the
acknowledgement goes out, and the farmer received "Expected response by None".

`seed_notifications` skips a Notification that already exists, on purpose: the
wording is desk-editable (FSD 3.11.7) and a reinstall must not undo an admin's
edits. So the two existing records are rewritten here, and only when their message
is still the original seeded text. A record an admin has already reworded is left
alone.
"""

import frappe


def execute():
	from oan_grievance_service.services import constants as C
	from oan_grievance_service.setup.install import NOTIFICATION_EVENTS, _translatable, _translatable_expr

	context_key = f"grievance.{C.EVENT_SUBMISSION_RECEIVED}"
	original = _translatable_expr(
		"Your grievance {0} has been received under {1}. Expected response by {2}.",
		("doc.ticket_number", "doc.service_category", "doc.sla_due_date"),
		context_key,
	)

	current = None
	for code, _title, _role, _channels, _trigger, source, args in NOTIFICATION_EVENTS:
		if code == C.EVENT_SUBMISSION_RECEIVED:
			current = _translatable(source, args, context_key)
			break
	if not current:
		return

	for name in frappe.get_all(
		"Notification",
		filters={"document_type": "Grievance", "method": C.EVENT_SUBMISSION_RECEIVED},
		pluck="name",
	):
		if frappe.db.get_value("Notification", name, "message") == original:
			frappe.db.set_value("Notification", name, "message", current, update_modified=False)
