# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Notification configuration for the Administration Notification tab (FSD Appendix C).

Read-only: the 19 notification events, and one event by its EC id. The matrix lives in the
core Notification records the sender already uses; see services/notification_config.py.
Editing an event is a separate endpoint.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	api_doc,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel

from oan_grievance_service.api.v1._schemas import Body, NonBlank
from oan_grievance_service.services import notification_config as service
from oan_grievance_service.services.constants import ADMIN_READ_ROLES

route = prefixed("/api/v1/notification-configs")


class NotificationConfigRecord(BaseModel):
	"""Matches `NotificationConfig` in the frontend's notification-config/components/types.ts."""

	id: str
	title: str
	eventType: str | None = None
	active: bool
	channel: list[str]
	subject: str
	trigger: str
	recipients: list[str]
	template: str
	lastEdited: str | None = None


class NotificationConfigListData(BaseModel):
	notification_configs: list[NotificationConfigRecord]


class NotificationConfigData(BaseModel):
	notification_config: NotificationConfigRecord


class ListNotificationConfigs(Body):
	"""No filters: the matrix is the fixed set of Appendix C events."""


class NotificationConfigRef(Body):
	event_id: NonBlank


@route("", methods=("GET",), summary="List notification configurations")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListNotificationConfigs)
@api_doc(
	summary="List notification configurations",
	description="The 19 notification events of FSD Appendix C (EC-001 to EC-019), in order, each "
	+ "with its title, trigger, recipients, channels (SMS, Email), subject, message template and "
	+ "last-edited time (UTC). Subject and template use {{token}} placeholders such as {{id}} and "
	+ "{{officerName}}. An event is active when at least one of its channels is switched on. "
	+ "EC-017 has no event of its own in the backend: it reports inactive, with eventType null, "
	+ "an empty subject and template, and a null lastEdited.",
	tags=["Administration"],
	response_model=NotificationConfigListData,
)
def list_notification_configs(**kwargs):
	"""Every Appendix C notification event."""
	return success_response(
		data={"notification_configs": service.list_events()},
		message=_("Notification configurations retrieved"),
	)


@route("/<event_id>", methods=("GET",), summary="Get a notification configuration")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(NotificationConfigRef)
@api_doc(
	summary="Get a notification configuration",
	description="One Appendix C notification event by its id (for example EC-001), in the shape "
	+ "the list returns.",
	tags=["Administration"],
	response_model=NotificationConfigData,
)
def get_notification_config(event_id: str, **kwargs):
	"""One notification event, by EC id."""
	event = service.get_event(event_id)
	if not event:
		frappe.throw(
			_("Notification configuration '{0}' does not exist.").format(event_id),
			frappe.DoesNotExistError,
		)
	return success_response(
		data={"notification_config": event},
		message=_("Notification configuration retrieved"),
	)
