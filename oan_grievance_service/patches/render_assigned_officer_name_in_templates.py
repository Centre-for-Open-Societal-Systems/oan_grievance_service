# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Make saved templates render the assigned officer's name instead of their email.

A `{assigned_to}` placeholder used to compile to `doc.assigned_to`, the officer's User id,
which is their email. It now compiles to the officer's full name
(`notifications.PLACEHOLDER_EXPRESSIONS`). This rewrites the arguments of templates
already saved, so they match what a fresh save would produce.
"""

import re

import frappe

from oan_grievance_service.services.notifications import PLACEHOLDER_EXPRESSIONS

FORMAT_ARGS = re.compile(r"\.format\(([^()]*)\)")
FIELD = re.compile(r"(?<![\w.])doc\.assigned_to(?!\w)")


def _rewrite(text):
	return FORMAT_ARGS.sub(
		lambda m: ".format(" + FIELD.sub(PLACEHOLDER_EXPRESSIONS["assigned_to"], m.group(1)) + ")", text
	)


def execute():
	for row in frappe.get_all(
		"Notification", filters={"document_type": "Grievance"}, fields=["name", "subject", "message"]
	):
		subject, message = _rewrite(row.subject or ""), _rewrite(row.message or "")
		if subject != (row.subject or "") or message != (row.message or ""):
			frappe.db.set_value(
				"Notification", row.name, {"subject": subject, "message": message}, update_modified=False
			)
