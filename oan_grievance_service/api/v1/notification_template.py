# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Notification template and configuration endpoints."""

from typing import Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import constants, notifications
from oan_grievance_service.services.constants import ADMIN_ROLES, STAFF_ROLES
from oan_grievance_service.services.notifications import RECIPIENT_ROLE_LEVEL, RECIPIENT_TYPES

route = prefixed("/api/v1/notification-templates")


class NotificationTemplateRecord(BaseModel):
	name: str
	id: str | None = None
	event: str
	channel: str
	recipient_type: str
	role_level: str | None = None
	role_level_name: str | None = None
	recipient_id: str | None = None
	recipient_label: str | None = None
	subject: str
	body: str
	placeholders: list[str] = []
	raw_subject: str | None = None
	raw_body: str | None = None
	translations: dict[str, dict[str, str]] | None = None
	enabled: bool
	condition: str | None = None


class NotificationTemplateListData(BaseModel):
	templates: list[NotificationTemplateRecord]


class PlaceholderItem(BaseModel):
	name: str
	label: str
	description: str
	example: str


class PlaceholdersResponseData(BaseModel):
	placeholders: list[PlaceholderItem]


class FlatRecipientOption(BaseModel):
	id: str
	label: str
	recipient_type: str
	role_level: str | None = None
	role_level_name: str | None = None


class RoleLevelOption(BaseModel):
	level_code: str
	level_name: str
	level_order: int
	escalation_hours: int | None = None
	description: str | None = None


class NotificationTemplateOptionsData(BaseModel):
	channels: list[str]
	recipients: list[FlatRecipientOption]
	placeholders: list[PlaceholderItem]
	role_levels: list[RoleLevelOption]


class TemplateRef(Body):
	template: NonBlank


class UpdateNotificationTemplate(TemplateRef):
	"""Payload for updating a notification template. Unknown parameters are rejected."""

	subject: str | None = None
	body: str | None = None
	enabled: bool | None = None
	recipient_type: str | None = None
	role_level: str | None = None
	recipient_id: str | None = None
	translations: dict[str, dict[str, str]] | None = None

	_blank = field_validator(
		"subject", "body", "recipient_type", "role_level", "recipient_id", mode="before"
	)(blank_to_none)


class ListNotificationTemplates(PageParams, Body):
	"""Filters for listing notification templates. Unknown parameters are rejected."""

	event: str | None = None
	channel: str | None = None
	recipient_type: str | None = None
	role_level: str | None = None
	enabled: bool | str | None = None

	_blank = field_validator("event", "channel", "recipient_type", "role_level", "enabled", mode="before")(
		blank_to_none
	)


def _resolve_recipient_id(recipient_id: str) -> tuple[str, str | None]:
	"""Flat recipient_id -> (recipient_type, role_level), the shape stored on the doc."""
	if recipient_id in notifications.PARTICIPANTS_BY_ID:
		return notifications.PARTICIPANTS_BY_ID[recipient_id], None
	if frappe.db.exists("Grievance Role Level", recipient_id):
		return RECIPIENT_ROLE_LEVEL, recipient_id
	frappe.throw(_("Invalid recipient '{0}'.").format(recipient_id), frappe.ValidationError)


def _to_record(row: dict, role_level_names: dict[str, str]) -> NotificationTemplateRecord:
	recipient_type = row.get("grievance_recipient_type") or row.get("grievance_recipient") or "Submitter"
	role_level = row.get("grievance_role_level")
	role_level_name = role_level_names.get(role_level) if role_level else None

	level = role_level or notifications.ROLE_LEVEL_RECIPIENTS.get(recipient_type)
	recipient_id = level or notifications.PARTICIPANT_IDS.get(recipient_type) or frappe.scrub(recipient_type)
	recipient_label = role_level_names.get(recipient_id) or level or recipient_type

	raw_subject = row.get("subject") or ""
	raw_body = row.get("message") or ""
	event_code = row.get("method") or ""

	simplified_subject = notifications.unpack_template(raw_subject)
	simplified_body = notifications.unpack_template(raw_body)

	placeholders = notifications.extract_placeholders(raw_body)
	for sp in notifications.extract_placeholders(raw_subject):
		if sp not in placeholders:
			placeholders.append(sp)

	event_translations = (
		notifications.get_translations_for_event(event_code, row.get("channel") or "") or None
	)

	return NotificationTemplateRecord(
		name=row["name"],
		id=constants.EVENT_EC_ID.get(event_code),
		event=event_code,
		channel=row.get("channel") or "",
		recipient_type=recipient_type,
		role_level=role_level,
		role_level_name=role_level_name,
		recipient_id=recipient_id,
		recipient_label=recipient_label,
		subject=simplified_subject,
		body=simplified_body,
		placeholders=placeholders,
		raw_subject=raw_subject,
		raw_body=raw_body,
		translations=event_translations,
		enabled=bool(row.get("enabled")),
		condition=row.get("condition") or None,
	)


@route("", methods=("GET",), summary="List grievance notification templates")
@frappe.whitelist()
@handle_api_errors
@require_role(STAFF_ROLES)
@validate_request(ListNotificationTemplates)
@api_doc(
	summary="List grievance notification templates",
	description="Returns configured notification rules for grievances, including channel, recipient type, dynamic role level, subject, and body.",
	tags=["Administration"],
	response_model=NotificationTemplateListData,
)
def list_notification_templates(
	event: str | None = None,
	channel: str | None = None,
	recipient_type: str | None = None,
	role_level: str | None = None,
	enabled: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List Grievance Notification templates. Returns both active and inactive by default."""
	params = PageParams(page=page, page_size=page_size)

	filters = {"document_type": "Grievance"}
	if event:
		filters["method"] = event
	if channel:
		filters["channel"] = channel
	if recipient_type:
		filters["grievance_recipient_type"] = recipient_type
	if role_level:
		filters["grievance_role_level"] = role_level
	if enabled is not None:
		filters["enabled"] = 1 if str(enabled).lower() in ("true", "1") else 0

	total = frappe.db.count("Notification", filters=filters)

	rows = frappe.get_all(
		"Notification",
		filters=filters,
		fields=[
			"name",
			"method",
			"channel",
			"grievance_recipient_type",
			"grievance_recipient",
			"grievance_role_level",
			"subject",
			"message",
			"enabled",
			"condition",
		],
		order_by="method asc, channel asc, name asc",
		offset=params.start,
		limit=params.page_size,
	)

	role_levels = {
		rl["name"]: rl["level_name"]
		for rl in frappe.get_all("Grievance Role Level", fields=["name", "level_name"])
	}

	records = [_to_record(row, role_levels).model_dump() for row in rows]

	return success_response(
		data={"templates": records},
		message=_("Notification templates retrieved successfully"),
		pagination=page_meta(total, params.page, params.page_size),
	)


@route("/options", methods=("GET",), summary="Get notification template options and metadata")
@frappe.whitelist()
@handle_api_errors
@require_role(STAFF_ROLES)
@api_doc(
	summary="Get notification template options and metadata",
	description="Returns available notification channels, flat recipient targets, active role levels, and allowed template placeholders.",
	tags=["Administration"],
	response_model=NotificationTemplateOptionsData,
)
def get_notification_template_options(**kwargs):
	"""Return channels, recipients, role levels, and placeholders for notification templates."""
	from oan_grievance_service.api.v1._options import get_role_levels

	recipients = [
		FlatRecipientOption(id=recipient_id, label=_(recipient), recipient_type=recipient)
		for recipient, recipient_id in notifications.PARTICIPANT_IDS.items()
	]

	active_levels = get_role_levels()
	for rl in active_levels:
		recipients.append(
			FlatRecipientOption(
				id=rl["level_code"],
				label=rl["level_name"],
				recipient_type=notifications.RECIPIENT_ROLE_LEVEL,
				role_level=rl["level_code"],
				role_level_name=rl["level_name"],
			)
		)

	role_levels_data = [RoleLevelOption(**rl) for rl in active_levels]
	placeholders_data = [PlaceholderItem(**p) for p in notifications.get_template_placeholders_metadata()]

	data = NotificationTemplateOptionsData(
		channels=list(notifications.SUPPORTED_CHANNELS),
		recipients=recipients,
		placeholders=placeholders_data,
		role_levels=role_levels_data,
	)

	return success_response(
		data=data.model_dump(),
		message=_("Notification template options retrieved successfully"),
	)


@route("/placeholders", methods=("GET",), summary="Get available notification template placeholders")
@frappe.whitelist()
@handle_api_errors
@require_role(STAFF_ROLES)
@api_doc(
	summary="Get available notification template placeholders",
	description="Returns the curated list of variables and metadata that can be used in notification templates.",
	tags=["Administration"],
	response_model=PlaceholdersResponseData,
)
def get_notification_placeholders(**kwargs):
	"""Return the allowed placeholders and descriptions for notification templates."""
	items = notifications.get_template_placeholders_metadata()
	return success_response(
		data={"placeholders": items},
		message=_("Available notification placeholders retrieved successfully"),
	)


@route("/<template>", methods=("GET",), summary="Get a notification template")
@frappe.whitelist()
@handle_api_errors
@require_role(STAFF_ROLES)
@validate_request(TemplateRef)
@api_doc(
	summary="Get a notification template",
	description="Returns a single configured notification rule for grievances by ID/name.",
	tags=["Administration"],
	response_model=dict[Literal["template"], NotificationTemplateRecord],
)
def get_notification_template(template: str, **kwargs):
	"""Return one Grievance Notification template."""
	if not frappe.db.exists("Notification", {"name": template, "document_type": "Grievance"}):
		frappe.throw(_("Notification template '{0}' not found.").format(template), frappe.DoesNotExistError)

	doc = frappe.get_doc("Notification", template)
	role_levels = {
		rl["name"]: rl["level_name"]
		for rl in frappe.get_all("Grievance Role Level", fields=["name", "level_name"])
	}

	record = _to_record(doc.as_dict(), role_levels).model_dump()
	return success_response(
		data={"template": record},
		message=_("Notification template retrieved successfully"),
	)


@route("/<template>", methods=("PATCH",), summary="Update a notification template")
@frappe.whitelist()
@handle_api_errors
@require_role(ADMIN_ROLES)
@validate_request(UpdateNotificationTemplate, exclude_unset=True)
@api_doc(
	summary="Update a notification template",
	description="Updates a notification template's subject, body, active status, or recipient configuration.",
	tags=["Administration"],
	response_model=dict[Literal["template"], NotificationTemplateRecord],
)
def update_notification_template(
	template: str,
	subject: str | None = None,
	body: str | None = None,
	enabled: bool | None = None,
	recipient_type: str | None = None,
	role_level: str | None = None,
	recipient_id: str | None = None,
	translations: dict[str, dict[str, str]] | None = None,
	**kwargs,
):
	"""Update one Grievance Notification template."""
	if not frappe.db.exists("Notification", {"name": template, "document_type": "Grievance"}):
		frappe.throw(_("Notification template '{0}' not found.").format(template), frappe.DoesNotExistError)

	for lang in translations or {}:
		if not frappe.db.exists("Language", lang):
			frappe.throw(_("Unknown language '{0}'.").format(lang), frappe.ValidationError)

	doc = frappe.get_doc("Notification", template)
	event_code = doc.get("method") or frappe.scrub(doc.name)
	body_ctx = notifications.template_context(event_code, doc.channel)
	subj_ctx = f"{body_ctx}.subject"

	old_source_subj = notifications.get_source_text(doc.subject or "")
	old_source_body = notifications.get_source_text(doc.message or "")

	if subject is not None:
		doc.subject = notifications.compile_template(
			subject,
			context_key=subj_ctx,
		)
	if body is not None:
		doc.message = notifications.compile_template(
			body,
			context_key=body_ctx,
		)
	if enabled is not None:
		doc.enabled = 1 if enabled else 0
	if recipient_id is not None:
		if recipient_type is not None or role_level is not None:
			frappe.throw(
				_("Send either 'recipient_id', or 'recipient_type'/'role_level', not both."),
				frappe.ValidationError,
			)
		recipient_type, role_level = _resolve_recipient_id(recipient_id)

	if recipient_type is not None:
		if recipient_type not in RECIPIENT_TYPES:
			frappe.throw(_("Invalid recipient type '{0}'.").format(recipient_type), frappe.ValidationError)
		doc.grievance_recipient_type = recipient_type
		if recipient_type != RECIPIENT_ROLE_LEVEL:
			doc.grievance_role_level = None

	if role_level is not None:
		if not frappe.db.exists("Grievance Role Level", role_level):
			frappe.throw(_("Role Level '{0}' does not exist.").format(role_level), frappe.ValidationError)
		doc.grievance_role_level = role_level

	if recipient_type is not None or role_level is not None:
		if doc.grievance_recipient_type == RECIPIENT_ROLE_LEVEL and not doc.grievance_role_level:
			frappe.throw(
				_("'role_level' is required when 'recipient_type' is 'Role Level'."),
				frappe.ValidationError,
			)
		doc.grievance_recipient = None

	doc.save(ignore_permissions=True)

	new_source_subj = notifications.get_source_text(doc.subject or "")
	new_source_body = notifications.get_source_text(doc.message or "")
	for key, context, source, changed in (
		("subject", subj_ctx, new_source_subj, subject is not None and new_source_subj != old_source_subj),
		("body", body_ctx, new_source_body, body is not None and new_source_body != old_source_body),
	):
		provided = {lang: texts[key] for lang, texts in (translations or {}).items() if texts.get(key)}
		notifications.sync_translations(context, source, provided, source_changed=changed)

	from frappe.translate import clear_cache as clear_translation_cache

	clear_translation_cache()

	role_levels = {
		rl["name"]: rl["level_name"]
		for rl in frappe.get_all("Grievance Role Level", fields=["name", "level_name"])
	}
	record = _to_record(doc.as_dict(), role_levels).model_dump()
	return success_response(
		data={"template": record},
		message=_("Notification template updated successfully"),
	)
