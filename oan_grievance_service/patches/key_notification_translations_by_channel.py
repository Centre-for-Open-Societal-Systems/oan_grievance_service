# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Give each notification channel its own translation context.

Email, SMS and System Notification templates of one event shared the context
`grievance.{event}`. A Translation row is keyed on source text plus context, so
editing one channel's English rewrote the source text of the row every channel
used, and the other channels stopped matching their Amharic.

This rewrites each template's `_(..., context=...)` calls to
`grievance.{event}.{channel}` and copies each existing Translation row to every
channel of its event before removing the shared row.
"""

import re
from collections import defaultdict

import frappe

SUFFIXES = ("", ".subject", ".then", ".else")


def execute():
	from frappe.translate import clear_cache

	from oan_grievance_service.services.notifications import template_context

	channels_by_event = defaultdict(set)
	for row in frappe.get_all(
		"Notification",
		filters={"document_type": "Grievance"},
		fields=["name", "method", "channel", "subject", "message"],
	):
		if not row.method or not row.channel:
			continue
		new_key = template_context(row.method, row.channel)
		channels_by_event[row.method].add(new_key)
		pattern = re.compile(
			r"context=(['\"])grievance\." + re.escape(row.method) + r"((?:\.subject|\.then|\.else)?)\1"
		)
		updates = {}
		for field in ("subject", "message"):
			value = row.get(field) or ""
			rewritten = pattern.sub(lambda m, key=new_key: f"context={m[1]}{key}{m[2]}{m[1]}", value)
			if rewritten != value:
				updates[field] = rewritten
		if updates:
			frappe.db.set_value("Notification", row.name, updates, update_modified=False)

	for event, new_keys in channels_by_event.items():
		old_key = f"grievance.{event}"
		for suffix in SUFFIXES:
			for row in frappe.get_all(
				"Translation",
				filters={"context": old_key + suffix},
				fields=["name", "language", "source_text", "translated_text"],
			):
				for new_key in new_keys:
					context = new_key + suffix
					if frappe.db.exists("Translation", {"language": row.language, "context": context}):
						continue
					frappe.get_doc(
						{
							"doctype": "Translation",
							"language": row.language,
							"source_text": row.source_text,
							"translated_text": row.translated_text,
							"context": context,
						}
					).insert(ignore_permissions=True)
				frappe.delete_doc("Translation", row.name, ignore_permissions=True, force=True)

	clear_cache()
