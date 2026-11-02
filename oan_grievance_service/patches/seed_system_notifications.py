# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt
"""Seed System Notification records for grievance events and copy existing translations."""

import frappe


def execute():
	from frappe.translate import clear_cache

	from oan_grievance_service.services.notifications import template_context
	from oan_grievance_service.setup.install import seed_notifications

	seed_notifications()

	for notification in frappe.get_all(
		"Notification",
		filters={"document_type": "Grievance", "channel": "System Notification"},
		fields=["name", "method"],
	):
		event_code = notification.method
		if not event_code:
			continue

		system_ctx = template_context(event_code, "System Notification")
		for source_channel in ("Email", "SMS"):
			source_ctx = template_context(event_code, source_channel)
			for ctx_suffix in ("", ".subject", ".then", ".else"):
				src = f"{source_ctx}{ctx_suffix}"
				dst = f"{system_ctx}{ctx_suffix}"
				for row in frappe.get_all(
					"Translation",
					filters={"context": src},
					fields=["language", "source_text", "translated_text"],
				):
					if not frappe.db.exists("Translation", {"context": dst, "language": row["language"]}):
						frappe.get_doc(
							{
								"doctype": "Translation",
								"language": row["language"],
								"source_text": row["source_text"],
								"translated_text": row["translated_text"],
								"context": dst,
							}
						).insert(ignore_permissions=True)

	clear_cache()
