# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Tell the farmer how many days they have to answer a request for information.

The seeded More Information Requested message now ends with the days left before the
case closes by itself. A message an admin has reworded is left as it is: only one that
still reads exactly as it was seeded is replaced.
"""

import frappe

from oan_grievance_service.services import constants as C
from oan_grievance_service.services.notifications import template_context
from oan_grievance_service.setup.install import NOTIFICATION_EVENTS, _translatable

OLD_SOURCE = "Additional information is needed for grievance {0}. Please respond via the portal."


def execute():
	spec = next(event[5] for event in NOTIFICATION_EVENTS if event[0] == C.EVENT_MORE_INFO_REQUESTED)

	for notification in frappe.get_all(
		"Notification",
		filters={"document_type": "Grievance", "event": "Method", "method": C.EVENT_MORE_INFO_REQUESTED},
		fields=["name", "channel", "message"],
	):
		context_key = template_context(C.EVENT_MORE_INFO_REQUESTED, notification.channel)
		if notification.message != _translatable(OLD_SOURCE, ("doc.ticket_number",), context_key):
			continue
		frappe.db.set_value(
			"Notification",
			notification.name,
			"message",
			_translatable(spec, (), context_key),
			update_modified=False,
		)
