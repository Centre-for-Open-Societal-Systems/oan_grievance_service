#!/usr/bin/env python3
"""
generate_openapi_spec.py

Dynamically discovers all declared REST routes across oan_grievance_service,
introspects request/response models, query parameters, security requirements,
and builds openapi_v1.yaml and openapi_v1.public.yaml.

Outputs:
  - openapi_v1.yaml: Engineering/Internal specification with vendor extensions
    (x-legacy-rpc-method, x-schema-confidence, x-gateway-tier).
  - openapi_v1.public.yaml: Public/Gateway contract with vendor extensions stripped.

Usage:
  python3 openapi/generate_openapi_spec.py
"""

from pathlib import Path
from typing import Any

import yaml
from oan_auth_service import gateway_apisix as gw
from oan_auth_service.openapi_generator import generate_spec_and_gateway, import_route_modules
from oan_auth_service.openapi_spec import (
	ARR,
	OBJ,
	REF,
	B,
	I,
	N,
	S,
	make_envelope,
	oas30,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
INTERNAL_SPEC_OUTPUT = SCRIPT_DIR / "openapi_v1.yaml"
PUBLIC_SPEC_OUTPUT = SCRIPT_DIR / "openapi_v1.public.yaml"
REGISTRATION_OVERLAY_OUTPUT = SCRIPT_DIR / "registration_overlay.yaml"

# The public spec is the client contract: `/v1/...` on the gateway host, no Frappe `/api`
# prefix (oan_auth_service docs/API_CONVENTIONS.md). The internal spec keeps the Frappe
# paths and the bench server, for Postman and local calls straight to Frappe.
PUBLIC_SERVERS = [
	{"url": "http://localhost:9080", "description": "Local APISIX gateway"},
	{"url": "https://grievance.openagrinet.org", "description": "Production Grievance Gateway"},
]


# ---------------------------------------------------------------------------
# Components: Data Schemas
# ---------------------------------------------------------------------------
DATA_SCHEMAS: dict[str, Any] = {}


def data(name: str, schema: dict[str, Any]) -> str:
	DATA_SCHEMAS[name] = schema
	return name


data(
	"PaginationMeta",
	OBJ(
		{
			"page": I(example=1, description="Current page number"),
			"page_size": I(example=20, description="Items per page"),
			"total_count": I(example=142, description="Total matching records"),
			"total_pages": I(example=8, description="Total pages available"),
			"has_next": B(description="True when a later page exists"),
			"has_prev": B(description="True when an earlier page exists"),
		},
		required=["page", "page_size", "total_count", "total_pages", "has_next", "has_prev"],
		description="Pagination metadata block",
	),
)

# Health & Ping
data(
	"HealthData",
	OBJ(
		{
			"status": S(example="healthy"),
			"service": S(example="oan_grievance_service"),
			"api_version": S(example="v1"),
		},
		required=["status", "service", "api_version"],
		description="Service health status payload",
	),
)

data(
	"PingData",
	OBJ(
		{
			"ping": S(example="pong"),
			"service": S(example="oan_grievance_service"),
			"api_version": S(example="v1"),
		},
		required=["ping", "service", "api_version"],
		description="Service ping payload",
	),
)

# Submitter Options & Profile
data(
	"SubmitterTypeItem",
	OBJ(
		{
			"type_name": S(example="Individual Farmer"),
			"code": S(example="IND_FARMER"),
			"description": S(nullable=True),
		},
		required=["type_name"],
	),
)

data(
	"SubmissionTypeItem",
	OBJ(
		{
			"type_name": S(example="Mobile App"),
			"code": S(example="MOB_APP"),
			"description": S(nullable=True),
		},
		required=["type_name"],
	),
)

data(
	"OptionKeyValue",
	OBJ(
		{
			"value": S(example="Inputs"),
			"label": S(example="Agricultural Inputs"),
			"code": S(nullable=True),
		},
		required=["value", "label"],
	),
)

data(
	"PhoneExtensionItem",
	OBJ(
		{
			"country": S(example="Ethiopia"),
			"code": S(example="ET"),
			"isd": S(example="+251"),
		},
		required=["country", "code", "isd"],
	),
)

data(
	"SubmitterOptionsData",
	OBJ(
		{
			"submitter_types": ARR(REF("SubmitterTypeItem")),
			"submission_types": ARR(REF("SubmissionTypeItem")),
			"preferred_languages": ARR(OBJ({"code": S(), "label": S()}, required=["code", "label"])),
			"service_categories": ARR(REF("OptionKeyValue")),
			"grievance_types": ARR(REF("OptionKeyValue")),
			"phone_extensions": ARR(REF("PhoneExtensionItem"), nullable=True),
		},
		required=[
			"submitter_types",
			"submission_types",
			"preferred_languages",
			"service_categories",
			"grievance_types",
		],
		description="Public dropdown options and intake reference data",
	),
)

data(
	"SubmitterProfileData",
	OBJ(
		{
			"profile_id": S(description="Unique Grievance Submitter Profile document name"),
			"full_name": S(description="Full name of submitter or representative"),
			"type": S(description="Submitter Type e.g. Individual Farmer or Cooperative"),
			"role": S(example="Grievance Submitter"),
			"identity_scheme": S(nullable=True, enum=["fayda", "org", "phone", None]),
			"identity_value": S(nullable=True),
			"fayda_id": S(nullable=True),
			"registration_number": S(nullable=True),
			"contact_mobile": S(nullable=True),
			"country_code": S(example="+251", nullable=True),
			"phone_number": S(example="911887766", nullable=True),
			"contact_email": S(format="email", nullable=True),
			"preferred_language": S(example="en", nullable=True),
			"administrative_area": S(nullable=True),
			"administrative_unit": S(nullable=True),
			"active": I(enum=[0, 1]),
			"is_blocked": I(enum=[0, 1]),
		},
		required=["profile_id", "full_name", "type", "role"],
		description="Grievance submitter profile details",
	),
)

data(
	"SubmitterIdentityItem",
	OBJ(
		{"scheme": S(example="phone"), "value": S(example="+251911887766")},
		required=["scheme", "value"],
		description="Submitter deduplication identity key",
	),
)

data(
	"SubmitterRegisterResultData",
	OBJ(
		{
			"profile_id": S(description="Unique Grievance Submitter Profile document name"),
			"submitter_type": S(example="Individual Farmer"),
			"submitter_name": S(example="Abebe Bikila", nullable=True),
			"contact_mobile": S(example="+251911887766", nullable=True),
			"country_code": S(example="+251", nullable=True),
			"phone_number": S(example="911887766", nullable=True),
			"contact_email": S(format="email", nullable=True),
			"dedupe_key": S(nullable=True),
			"identities": ARR(REF("SubmitterIdentityItem")),
			"administrative_area": S(nullable=True),
			"administrative_unit": S(nullable=True),
			"active": B(),
			"is_blocked": B(),
			"blocked_reason": S(nullable=True),
		},
		required=["profile_id", "submitter_type", "active", "is_blocked"],
		description="Outcome of submitter profile registration",
	),
)

data(
	"SubmitterBlockResultData",
	OBJ(
		{
			"profile_id": S(description="Submitter profile ID"),
			"is_blocked": B(description="Whether the submitter profile is blocked"),
			"blocked_reason": S(nullable=True, description="Reason for blocking"),
			"active": B(description="Whether the submitter profile is active"),
		},
		required=["profile_id", "is_blocked", "active"],
		description="Outcome of submitter block or unblock operation",
	),
)

data(
	"FarmerSearchData",
	OBJ(
		{
			"farmer": OBJ(
				{
					"registry_id": S(description="Farmer Registry internal record ID"),
					"fayda_id": S(description="16-digit Fayda ID searched for"),
					"submitter_name": S(nullable=True, description="Farmer name in the registry"),
				},
				required=["registry_id", "fayda_id"],
				description="Farmer found in the registry",
			),
		},
		required=["farmer"],
		description="Farmer registry search result",
	),
)

data(
	"FarmerDetailsData",
	OBJ(
		{
			"farmer": OBJ(
				{
					"registry_id": S(description="Farmer Registry internal record ID"),
					"contact_mobile": S(nullable=True, description="Primary phone number"),
					"contact_email": S(nullable=True, description="Email address"),
					"region": S(nullable=True, description="Region name"),
					"zone": S(nullable=True, description="Zone name"),
					"woreda": S(nullable=True, description="Woreda name"),
					"kebele": S(nullable=True, description="Kebele name"),
				},
				required=["registry_id"],
				description="Registry contact details to pre-fill a submitter profile",
			),
		},
		required=["farmer"],
		description="Farmer registry contact details",
	),
)

# Administrative Areas
data(
	"AdministrativeAreaItem",
	OBJ(
		{
			"area_id": S(example="region-ET14", description="Canonical area ID"),
			"area_name": S(example="Oromia"),
			"code": S(example="ET14", nullable=True),
			"path_code": S(example="ET.ET14", nullable=True),
			"level_name": S(
				example="Region", description="Administrative tier (e.g. Region, Zone, Woreda, Kebele)"
			),
			"parent_administrative_area": S(nullable=True),
			"is_group": I(enum=[0, 1]),
			"depth": I(example=1),
		},
		required=["area_id", "area_name", "level_name"],
		description="Administrative area hierarchy node",
	),
)

data(
	"AdministrativeAreasListData",
	OBJ(
		{
			"areas": ARR(REF("AdministrativeAreaItem")),
			"count": I(example=12),
			"parent": S(nullable=True),
			"level_name": S(nullable=True),
		},
		required=["areas", "count"],
		description="List of administrative area nodes for cascading dropdowns or search",
	),
)

data(
	"BreadcrumbItem",
	OBJ(
		{
			"area_id": S(),
			"area_name": S(),
			"code": S(nullable=True),
			"path_code": S(nullable=True),
			"level_name": S(),
			"depth": I(),
		},
		required=["area_id", "area_name", "level_name"],
	),
)

data(
	"AreaAncestorsData",
	OBJ(
		{
			"current": REF("AdministrativeAreaItem"),
			"breadcrumbs": ARR(REF("BreadcrumbItem")),
		},
		required=["breadcrumbs"],
		description="Ancestor hierarchy breadcrumbs from country root to the node",
	),
)

# Grievance Core & Lifecycle
data(
	"GrievanceSubmitResultData",
	OBJ(
		{
			"ticket_number": S(example="ET14IN000012026", description="Formatted ticket number"),
			"status": S(example="Submitted"),
			"workflow_state": S(nullable=True),
			"client_submission_uuid": S(nullable=True),
			"routing_rule": S(nullable=True),
			"can_request_more_info": B(description="Whether the submitter can be contacted"),
			"attachments": ARR(REF("AttachmentItem"), description="Files stored with the submission"),
			"current_state": REF("GrievanceCurrentState"),
			"timeline_event": {**REF("TimelineEventItem"), "nullable": True},
		},
		required=["ticket_number", "status"],
		description="Acknowledgement outcome and ticket identifier returned on submission",
	),
)

data(
	"IvrGrievanceResultData",
	OBJ(
		{
			"ticket_number": S(example="Z-0AB-0001-0", description="Formatted ticket number"),
			"status": S(example="Submitted"),
			"submitter": S(description="The caller's Submitter Profile"),
			"created": B(description="False when the call id was already filed and nothing new was made"),
			"attachments": ARR(REF("AttachmentItem"), description="The call's audio, stored with the case"),
			"current_state": REF("GrievanceCurrentState"),
			"timeline_event": {**REF("TimelineEventItem"), "nullable": True},
		},
		required=["ticket_number", "status", "submitter", "created"],
		description="Ticket of the grievance filed for an IVR call",
	),
)

data(
	"GrievanceListItem",
	OBJ(
		{
			"name": S(description="Internal document ID"),
			"ticket_number": S(example="3001002A0"),
			"ticket_number_display": S(
				example="3-001-002A-0", nullable=True, description="Grouped ticket number for human reading"
			),
			"status": S(
				example="Submitted",
				description="Public workflow status (Submitted, Under Investigation, More Info Needed, Resolved, Closed, Reopened, Rejected)",
			),
			"service_category": S(example="Inputs"),
			"grievance_type": S(example="Fertilizer Shortage"),
			"grievance_type_name": S(
				example="Fertilizer Shortage",
				nullable=True,
				description="Display name for the grievance type",
			),
			"administrative_area": S(example="kebele-ET140108101008"),
			"administrative_unit": S(nullable=True),
			"submitter_name": S(example="Abebe Bikila"),
			"contact_mobile": S(example="+251911887766", nullable=True),
			"contact_email": S(nullable=True),
			"assigned_officer": S(nullable=True),
			"assigned_to": S(nullable=True, description="Assigned officer email; staff only"),
			"assigned_to_name": S(nullable=True, description="Assigned officer's full name; staff only"),
			"sla_target_date": S(format="date-time", nullable=True),
			"is_escalated": I(enum=[0, 1]),
			"escalated": B(nullable=True),
			"is_anonymous": B(nullable=True),
			"confirmation_deadline": S(format="date-time", nullable=True),
			"can_act": B(
				description="Whether the caller may act on the case now: the submitter while a reply or confirmation is owed, the assigned officer (or their supervisor) otherwise"
			),
			"due_date": S(
				format="date-time",
				nullable=True,
				description="The one deadline that matters now: the auto-close date while awaiting the submitter, the SLA due date while awaiting an officer",
			),
			"due_kind": S(
				nullable=True,
				enum=["auto_close", "sla_breach"],
				description="What happens at due_date: the case closes by itself, or the SLA is breached",
			),
			"overdue_seconds": I(
				nullable=True,
				description="How long past due_date the case already is; null until it is",
			),
			"is_paused": B(
				nullable=True, description="Whether the SLA clock is held (waiting on the submitter)"
			),
			"paused_since": S(format="date-time", nullable=True, description="When the hold started"),
			"auto_close_at": S(
				format="date-time",
				nullable=True,
				description="When the case closes by itself if nobody acts, for a state whose timer closes it",
			),
			"creation": S(format="date-time"),
			"modified": S(format="date-time"),
		},
		required=["ticket_number", "status", "service_category", "grievance_type", "creation"],
		description="Summary record of a grievance in list view",
	),
)

data(
	"GrievanceListData",
	OBJ(
		{
			"grievances": ARR(REF("GrievanceListItem")),
			"pagination": REF("PaginationMeta"),
		},
		required=["grievances", "pagination"],
		description="Filtered and paginated list of grievances",
	),
)

data(
	"GrievanceDetailData",
	OBJ(
		{
			"ticket_number": S(example="3001002A0"),
			"ticket_number_display": S(
				example="3-001-002A-0", nullable=True, description="Grouped ticket number for human reading"
			),
			"name": S(),
			"status": S(),
			"service_category": S(),
			"grievance_type": S(),
			"description": S(),
			"submission_channel": S(),
			"preferred_language": S(nullable=True),
			"administrative_area": S(),
			"administrative_unit": S(nullable=True),
			"submitter": S(nullable=True),
			"submitter_name": S(),
			"contact_mobile": S(nullable=True),
			"contact_email": S(nullable=True),
			"is_anonymous": I(enum=[0, 1]),
			"assigned_officer": S(nullable=True),
			"assisted_by_officer": S(nullable=True),
			"sla_target_date": S(format="date-time", nullable=True),
			"sla_status": S(nullable=True),
			"resolution_details": S(nullable=True),
			"satisfaction_rating": I(nullable=True),
			"reopen_count": I(example=0),
			"is_escalated": I(enum=[0, 1]),
			"confirmation_deadline": S(format="date-time", nullable=True),
			"creation": S(format="date-time"),
			"modified": S(format="date-time"),
		},
		required=["ticket_number", "status", "description", "creation"],
		description="Full grievance case details",
	),
)

data(
	"ResponseParts",
	OBJ(
		{
			"action_taken": S(description="What the department did"),
			"resolution_summary": S(description="The outcome for the submitter"),
		},
		required=["action_taken", "resolution_summary"],
		description="A department response's two parts, split from the stored text. Null on any text "
		"that was not written in two parts (a rejection, a reopen, an older template).",
	),
)

data(
	"TimelineChangeItem",
	OBJ(
		{
			"fieldname": S(example="assigned_to", description="The grievance field the request changed"),
			"label": S(example="Assigned Officer", description="Display label of the field"),
			"old_value": S(nullable=True, description="Stored value before the change (an id or email)"),
			"new_value": S(nullable=True, description="Stored value after the change (an id or email)"),
			"old_display": S(
				example="Selam Bekele", description="Name to show for the old value; '-' if empty"
			),
			"new_display": S(
				example="Hana Girma", description="Name to show for the new value; '-' if empty"
			),
		},
		required=["fieldname", "label", "old_display", "new_display"],
		description="One field a change request moved, with the names to show for its values",
	),
)

data(
	"TimelineEventItem",
	OBJ(
		{
			"id": S(nullable=True, description="Timeline entry identifier (same as name)"),
			"name": S(nullable=True, description="Timeline entry identifier"),
			"entry_type": S(
				example="dept_response",
				description="Event classification (dept_response, note, message, submission, status_change)",
			),
			"response_number": I(
				nullable=True,
				example=1,
				description="Sequential count of department response on the grievance e.g. 1, 2",
			),
			"action": S(
				nullable=True, description="Workflow action behind a status change or response entry"
			),
			"action_taken": S(nullable=True, description="Specific operational action taken by department"),
			"resolution_summary": S(
				nullable=True, description="Summary of resolution and next steps for citizen"
			),
			"from_status": S(nullable=True),
			"to_status": S(nullable=True),
			"author_role": S(nullable=True, description="Role of the actor e.g. Woreda Officer or Submitter"),
			"author_type": S(nullable=True, enum=["submitter", "officer", "system"]),
			"author_name": S(
				nullable=True, description="Full name of actor when visible (e.g. for staff oversight)"
			),
			"body": S(
				nullable=True,
				description="Timeline message or description text. A change request's internal note "
				"reads with field labels and officer and grievance type names, one line per change.",
			),
			"changes": ARR(
				REF("TimelineChangeItem"),
				description="Only on the internal note of a change request (reassignment, SLA deferral): "
				"the same changes as `body`, as data",
			),
			"is_internal": B(description="Whether visible only to staff"),
			"attachments": ARR(REF("AttachmentItem"), description="Files attached to this entry"),
			"created_on": S(format="date-time", nullable=True),
			"creation": S(format="date-time", nullable=True),
		},
		required=["entry_type"],
		description="Audit and communication event on the grievance timeline",
	),
)

data(
	"TimelineSubmitterDetail",
	OBJ(
		{
			"name": S(nullable=True),
			"contact_mobile": S(nullable=True),
			"contact_email": S(nullable=True),
			"submitter_type": S(nullable=True),
			"is_anonymous": B(),
			"assisted_by_officer": S(nullable=True),
		},
		required=["is_anonymous"],
		description="Submitter contact and identity details on the timeline",
	),
)

data(
	"GrievanceTimelineData",
	OBJ(
		{
			"ticket_number": S(example="ET14IN000012026"),
			"status": S(example="Under Investigation"),
			"escalated": B(),
			"description": S(nullable=True),
			"desired_outcome": S(nullable=True),
			"service_category": S(nullable=True),
			"grievance_type": S(nullable=True),
			"grievance_type_name": S(nullable=True),
			"administrative_area": S(nullable=True),
			"administrative_hierarchy": OBJ({}, additionalProperties=True, nullable=True),
			"location": S(nullable=True),
			"administrative_unit": S(nullable=True),
			"submission_channel": S(nullable=True),
			"sla": OBJ(
				{
					"sla_due_date": S(
						format="date-time",
						nullable=True,
						description="Due date. While the clock is held (is_paused) it is the latest date it can reach, for staff",
					),
					"is_paused": B(
						nullable=True, description="Whether the SLA clock is held (waiting on the submitter)"
					),
					"paused_since": S(format="date-time", nullable=True, description="When the hold started"),
					"auto_close_at": S(
						format="date-time",
						nullable=True,
						description="When the case closes by itself if nobody acts, for a state whose timer closes it",
					),
					"active_deferral_request": {
						"oneOf": [REF("ChangeRequestData"), REF("PublicChangeRequestData")],
						"nullable": True,
					},
				}
			),
			"assignment": OBJ(
				{
					"department": S(nullable=True),
					"assigned_to": S(nullable=True),
					"assigned_to_name": S(nullable=True),
					"routed_automatically": B(),
					"active_reassignment_request": {**REF("ChangeRequestData"), "nullable": True},
				}
			),
			"submitter": REF("TimelineSubmitterDetail"),
			"timeline": ARR(REF("TimelineEventItem")),
		},
		required=["ticket_number", "status", "timeline"],
		description="Chronological event log and message history",
	),
)

data(
	"GrievanceActionResultData",
	OBJ(
		{
			"ticket_number": S(example="3-001-002A-0"),
			"status": S(example="Resolved"),
			"action": S(description="Workflow action taken"),
			"attachments": ARR(REF("AttachmentItem"), description="Files stored with the action"),
			"current_state": REF("GrievanceCurrentState"),
			"timeline_event": {**REF("TimelineEventItem"), "nullable": True},
			"available_actions": ARR(REF("AvailableActionItem")),
		},
		required=["ticket_number", "status", "action", "current_state", "available_actions"],
		description="Outcome of a workflow action on a grievance",
	),
)

data(
	"GrievanceFeedbackData",
	OBJ(
		{
			"ticket_number": S(),
			"feedback_id": S(),
			"rating": I(minimum=1, maximum=5),
			"comments": S(nullable=True),
			"submitted_at": S(format="date-time", nullable=True),
			"current_state": REF("GrievanceCurrentState"),
		},
		required=["ticket_number", "feedback_id", "rating", "current_state"],
		description="The feedback recorded, and the case as it now stands",
	),
)

data(
	"GrievanceMessageData",
	OBJ(
		{
			"name": S(description="Timeline entry ID"),
			"ticket_number": S(example="3-001-002A-0"),
			"entry_type": S(enum=["message", "note"]),
			"is_internal": B(),
			"author_type": S(enum=["officer", "submitter"]),
			"created_on": S(format="date-time", nullable=True),
			"status": S(description="Grievance status, unchanged by a message"),
			"attachments": ARR(REF("AttachmentItem"), description="Files stored with the message"),
			"current_state": REF("GrievanceCurrentState"),
			"timeline_event": {**REF("TimelineEventItem"), "nullable": True},
		},
		required=["name", "ticket_number", "entry_type", "is_internal", "status", "current_state"],
		description="The timeline entry a message or internal note created",
	),
)

data(
	"GrievanceOptionsData",
	OBJ(
		{
			"statuses": ARR(
				OBJ(
					{
						"status": S(),
						"label": S(),
						"order": I(description="Display order of the queue status"),
						"is_open": I(),
						"is_terminal": I(
							description="1 when every mapped Frappe workflow state is terminal; 0 when absent"
						),
					}
				)
			),
			"departments": ARR(
				OBJ({"department_id": S(), "department_name": S()}, additionalProperties=True)
			),
			"service_categories": ARR(REF("OptionKeyValue")),
			"grievance_types": ARR(REF("OptionKeyValue")),
			"submission_channels": ARR(S()),
			"officers": ARR(
				OBJ(
					{
						"user_id": S(description="Officer user ID"),
						"full_name": S(description="Officer display name"),
						"role_level": S(description="Grievance role level code"),
						"is_primary": B(description="Whether officer is designated primary"),
					}
				),
				description="Active officers under the specified department. Staff only; present only when department is supplied.",
			),
		},
		required=["statuses", "departments", "service_categories", "grievance_types", "submission_channels"],
		description="Grievance management options and active dropdown choices for staff",
	),
)

data(
	"StatusCard",
	OBJ(
		{
			"status": S(example="In Progress"),
			"label": S(example="In Progress"),
			"order": I(description="Display order of the queue status", example=2),
			"is_open": I(enum=[0, 1], example=1),
			"is_terminal": I(
				description="1 when every mapped Frappe workflow state is terminal. Absent workflow states are non-terminal.",
				enum=[0, 1],
				example=0,
			),
			"count": I(
				description="Grievances on this card visible to the caller. Omitted on the options list.",
				example=12,
			),
		},
		required=["status", "label", "order", "is_open", "is_terminal"],
		description="One queue status for the all-grievances KPI cards",
	),
)

data(
	"GrievanceStatusSummaryData",
	OBJ(
		{"cards": ARR(REF("StatusCard"))},
		required=["cards"],
		description="Status summary for the all-grievances queue.",
	),
)

# Department responses
data(
	"ResponseTemplateItem",
	OBJ(
		{
			"template": S(description="Template ID, sent as `template` to /action"),
			"title": S(),
			"action": S(nullable=True, description="Workflow action the template is for"),
			"workflow_action": S(nullable=True, description="Workflow action the template is for"),
			"department": S(nullable=True, description="Department the template is scoped to; null for all"),
			"service_category": S(
				nullable=True, description="Service category the template is scoped to; null for all"
			),
			"reason": S(description="Template body rendered for the case, as plain text. Prefills `reason`."),
			"reason_parts": {**REF("ResponseParts"), "nullable": True},
			"note": S(
				nullable=True,
				description="Template note rendered for the case, as plain text. Prefills `note`.",
			),
		},
		required=["template", "title", "reason"],
		description="A response template filled in for the grievance",
	),
)

data(
	"ResponseTemplatesData",
	OBJ(
		{"items": ARR(REF("ResponseTemplateItem"), description="Most specific first")},
		required=["items"],
	),
)

# Attachments
data(
	"AttachmentItem",
	OBJ(
		{
			"attachment": S(description="Unique identifier of the attachment record"),
			"name": S(description="Document name (alias for attachment)", nullable=True),
			"file_name": S(description="Original filename"),
			"file_url": S(description="URL to the uploaded file", nullable=True),
			"mime_type": S(description="MIME type of the file"),
			"size_bytes": I(description="File size in bytes"),
			"checksum_sha256": S(description="SHA-256 checksum"),
			"scan_status": S(description="Antivirus scan status (Pending, Clean, Infected)"),
			"document_type": S(nullable=True, description="Classification of document"),
			"timeline_entry": S(nullable=True, description="Timeline entry the file is attached to"),
			"creation": S(format="date-time", nullable=True),
		},
		required=["file_name", "mime_type", "size_bytes"],
		description="Metadata for an uploaded evidence attachment",
	),
)

data(
	"AttachmentDownloadData",
	OBJ(
		{
			"file_name": S(description="Original filename"),
			"file_url": S(
				description="Frappe private-file URL; needs a Frappe session cookie, not a bearer token"
			),
			"view_url": S(
				description="API route that streams the bytes under the bearer token: "
				"/api/v1/attachments/{attachment_id}/view"
			),
			"mime_type": S(description="MIME type"),
			"size_bytes": I(description="Size in bytes"),
			"checksum_sha256": S(description="SHA-256 checksum"),
		},
		required=["file_name", "file_url", "view_url", "mime_type", "size_bytes"],
		description="Download metadata for a clean attachment",
	),
)

data(
	"DeleteAttachmentData",
	OBJ(
		{"deleted": B(description="True if attachment was successfully deleted")},
		required=["deleted"],
		description="Confirmation of attachment removal",
	),
)

# Change Requests
data(
	"ChangeItem",
	OBJ(
		{
			"fieldname": S(description="Grievance field to modify"),
			"old_value": S(
				nullable=True, description="Value before change; the officer's full name for assigned_to"
			),
			"old_value_id": S(nullable=True, description="Officer's user id; set only for assigned_to"),
			"new_value": S(
				nullable=True, description="Requested value; the officer's full name for assigned_to"
			),
			"new_value_id": S(nullable=True, description="Officer's user id; set only for assigned_to"),
		},
		required=["fieldname"],
		description="Specific field modification item in a change request",
	),
)

data(
	"ApprovalTrailItem",
	OBJ(
		{
			"action": S(example="Requested", description="Approval lifecycle action"),
			"user": S(description="Full name of the user performing action"),
			"user_id": S(description="User id of the user performing action"),
			"pending_with": S(nullable=True, description="Full name of the user pending decision"),
			"pending_with_id": S(nullable=True, description="User id of the user pending decision"),
			"at": S(format="date-time", nullable=True, description="Timestamp of action"),
			"note": S(nullable=True, description="Approval or rejection remarks"),
		},
		required=["action", "user"],
		description="Change request approval audit step",
	),
)

data(
	"PublicChangeRequestData",
	OBJ(
		{
			"status": S(
				example="Pending",
				enum=["Pending", "Approved", "Rejected"],
				description="Change request status",
			),
		},
		required=["status"],
		description="Public change request snapshot for non-staff citizens",
	),
)

data(
	"ChangeRequestData",
	OBJ(
		{
			"name": S(description="Unique change request document ID"),
			"ticket_number": S(description="Associated grievance ticket number"),
			"subject": S(description="Summary of requested change"),
			"reason": S(nullable=True, description="Reason or justification"),
			"status": S(example="Pending", enum=["Pending", "Approved", "Rejected"]),
			"requested_by": S(description="Full name of the user who raised the request"),
			"requested_by_id": S(description="User id of the user who raised the request"),
			"requested_at": S(format="date-time", nullable=True),
			"pending_with": S(
				nullable=True, description="Full name of the user currently required to decide"
			),
			"pending_with_id": S(
				nullable=True, description="User id of the user currently required to decide"
			),
			"pending_since": S(format="date-time", nullable=True),
			"decided_by": S(nullable=True, description="Full name of the user who finalized decision"),
			"decided_by_id": S(nullable=True, description="User id of the user who finalized decision"),
			"decided_at": S(format="date-time", nullable=True),
			"decision_note": S(nullable=True, description="Decision explanation"),
			"changes": ARR(REF("ChangeItem")),
			"trail": ARR(REF("ApprovalTrailItem")),
		},
		required=["name", "ticket_number", "subject", "status", "changes", "trail"],
		description="Change request snapshot and hierarchy approval status",
	),
)

data(
	"ChangeRequestListData",
	OBJ(
		{
			"items": ARR(REF("ChangeRequestData")),
			"count": I(example=3, description="Number of matching change requests"),
		},
		required=["items", "count"],
		description="List of change requests",
	),
)

data(
	"AvailableActionItem",
	OBJ(
		{
			"action": S(description="Action name"),
			"action_code": S(description="Stable machine-readable action identifier"),
			"label": S(description="Localized action label"),
			"requires_reason": B(description="Always true: every action carries a reason"),
			"requires_rating": B(description="True if the action accepts a rating (e.g. Close Case)"),
		},
		required=["action", "label", "requires_reason"],
	),
)

data(
	"GrievanceCurrentState",
	OBJ(
		{
			"status": S(description="Public grievance status"),
			"escalated": B(description="Whether case is currently escalated"),
			"assigned_to": S(nullable=True, description="Assigned officer email"),
			"assigned_to_name": S(nullable=True, description="Assigned officer's full name; staff only"),
			"department": S(nullable=True, description="Assigned department ID"),
			"updated_at": S(format="date-time", nullable=True),
			"available_actions": ARR(REF("AvailableActionItem")),
			"can_request_more_info": B(description="Whether the submitter can be contacted"),
		},
		required=["status", "escalated", "available_actions", "can_request_more_info"],
		description="Current case workflow status and permitted next actions",
	),
)

data(
	"GrievanceChangeResponseData",
	OBJ(
		{
			"ticket_number": S(example="ET14IN000012026", description="Grievance ticket number"),
			"status": S(example="Under Investigation"),
			"change_request": REF("ChangeRequestData"),
			"current_state": REF("GrievanceCurrentState"),
			"timeline_event": {
				**REF("TimelineEventItem"),
				"nullable": True,
				"description": "Timeline entry the change wrote, if any",
			},
			"assigned_dept": S(nullable=True),
			"assigned_to": S(nullable=True),
			"service_category": S(nullable=True),
			"grievance_type": S(nullable=True),
			"sla_due_date": S(format="date-time", nullable=True),
			"is_anonymous": B(nullable=True),
			"anonymity_status": S(nullable=True),
		},
		required=["ticket_number", "status", "change_request", "current_state"],
		description="Outcome of a change-request-backed modification on a grievance",
	),
)

data(
	"CategoryAssignment",
	OBJ(
		{
			"name": S(
				example="GR-RBAC-00001",
				description="Grievance RBAC Assignment id for this department and category",
			),
			"service_category": S(example="Inputs", description="Grievance service category"),
			"department": S(description="Owning department the category routes to"),
			"l1_officer": S(nullable=True, description="L1 nodal officer user id"),
			"l1_officer_name": S(nullable=True, description="L1 officer display name"),
			"l2_officer": S(nullable=True, description="L2 senior nodal officer user id"),
			"l2_officer_name": S(nullable=True, description="L2 officer display name"),
			"sla_days": I(
				nullable=True,
				minimum=1,
				example=14,
				description="SLA window in days. Shared by every department serving the category.",
			),
			"auto_escalate": B(
				nullable=True,
				description="Escalate automatically when the SLA is breached. Shared by every department serving the category.",
			),
			"active": B(description="False once the rule has been deactivated"),
			"l1_role_level": S(
				nullable=True,
				description="L1 role level copied from the department",
			),
			"l2_role_level": S(
				nullable=True,
				description="L2 role level copied from the department",
			),
			"routing_strategy": S(
				nullable=True,
				enum=["Primary First", "Round Robin", "Least Loaded"],
				description="Routing strategy copied from the department. Null when the department has not chosen one.",
			),
		},
		required=[
			"name",
			"service_category",
			"department",
			"active",
		],
		description="Category-to-department routing rule",
	),
)

data(
	"RoleLevelRecord",
	OBJ(
		{
			"level_code": S(example="nodal_officer", description="Unique role level identifier/code"),
			"level_name": S(example="Kebele Nodal Officer", description="Human-readable display name"),
			"level_order": I(example=10, description="Escalation order rank (lower is more junior)"),
			"escalation_hours": I(
				example=48,
				nullable=True,
				description="Hours a breached grievance rests before climbing upward; 0 or null is terminal",
			),
			"is_active": B(example=True, description="Whether this level is active in the escalation chain"),
			"description": S(example="L1. Kebele-level coordination point for grievances.", nullable=True),
		},
		required=["level_code", "level_name", "level_order", "is_active"],
		description="Escalation hierarchy role level record",
	),
)

data(
	"RoleLevelDetailData",
	OBJ(
		{"role_level": REF("RoleLevelRecord")},
		required=["role_level"],
		description="Single role level detail",
	),
)

data(
	"RoleLevelListData",
	OBJ(
		{
			"role_levels": ARR(REF("RoleLevelRecord")),
			"total": I(example=5, description="Total matching records"),
			"pagination": REF("PaginationMeta"),
		},
		required=["role_levels", "total", "pagination"],
		description="List of grievance role levels with pagination",
	),
)

data(
	"CategoryAssignmentData",
	OBJ(
		{"assignment": REF("CategoryAssignment")},
		required=["assignment"],
		description="One category assignment",
	),
)

data(
	"CategoryAssignmentListData",
	OBJ(
		{
			"assignments": ARR(REF("CategoryAssignment")),
			"pagination": REF("PaginationMeta"),
		},
		required=["assignments", "pagination"],
		description="One page of category assignments",
	),
)


# Dashboard charts. Rows differ per chart and are documented on each route; each is
# a flat object of counts, codes and labels, never case detail on the public routes.
data(
	"DashboardChartRow",
	OBJ({}, additionalProperties=True, description="One row of a chart; the keys depend on the chart"),
)
data(
	"DashboardChartsData",
	OBJ(
		{},
		additionalProperties={**ARR(REF("DashboardChartRow")), "nullable": True},
		description="Rows per requested chart id; null for a chart that failed (see meta.errors)",
	),
)

# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------
# Derived from the Pydantic model each route names in @validate_request, so the
# contract is the validation the code runs. Filled by build_openapi().
REQ: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Response Envelopes
# ---------------------------------------------------------------------------
ENVELOPES = {
	"HealthResponse": make_envelope("HealthData", description="Health check response"),
	"PingResponse": make_envelope("PingData", description="Ping response"),
	"SubmitterOptionsResponse": make_envelope(
		"SubmitterOptionsData", description="Submitter options response"
	),
	"SubmitterProfileResponse": make_envelope(
		"SubmitterProfileData", description="Submitter profile response"
	),
	"SubmitterRegisterResponse": make_envelope(
		"SubmitterRegisterResultData", description="Submitter registration response"
	),
	"SubmitterBlockResponse": make_envelope(
		"SubmitterBlockResultData", description="Submitter block/unblock response"
	),
	"FarmerSearchResponse": make_envelope("FarmerSearchData", description="Farmer registry search response"),
	"FarmerDetailsResponse": make_envelope(
		"FarmerDetailsData", description="Farmer registry details response"
	),
	"AdministrativeAreasListResponse": make_envelope(
		"AdministrativeAreasListData", description="Administrative areas list response"
	),
	"AreaAncestorsResponse": make_envelope("AreaAncestorsData", description="Area ancestors response"),
	"GrievanceSubmitResultResponse": make_envelope(
		"GrievanceSubmitResultData", description="Grievance submission outcome response"
	),
	"IvrGrievanceResultResponse": make_envelope(
		"IvrGrievanceResultData", description="IVR grievance filing outcome response"
	),
	"GrievanceListResponse": make_envelope(
		"GrievanceListData", description="Paginated grievance list response"
	),
	"GrievanceDetailResponse": make_envelope("GrievanceDetailData", description="Grievance details response"),
	"GrievanceOptionsResponse": make_envelope(
		"GrievanceOptionsData", description="Grievance options response"
	),
	"GrievanceStatusSummaryResponse": make_envelope(
		"GrievanceStatusSummaryData", description="KPI status card counts"
	),
	"ResponseTemplatesResponse": make_envelope(
		"ResponseTemplatesData", description="Rendered response templates"
	),
	"GrievanceTimelineResponse": make_envelope(
		"GrievanceTimelineData", description="Grievance timeline response"
	),
	"GrievanceActionResultResponse": make_envelope(
		"GrievanceActionResultData", description="Action result response"
	),
	"GrievanceFeedbackResponse": make_envelope(
		"GrievanceFeedbackData", description="Feedback outcome response"
	),
	"GrievanceMessageResponse": make_envelope(
		"GrievanceMessageData", description="Message or internal note posted"
	),
	"ChangeRequestResponse": make_envelope(
		"ChangeRequestData", description="Grievance change request response"
	),
	"ChangeRequestListResponse": make_envelope(
		"ChangeRequestListData", description="List of change requests response"
	),
	"GrievanceChangeResponse": make_envelope(
		"GrievanceChangeResponseData", description="Grievance change action response"
	),
	"AttachmentUploadResponse": make_envelope(
		"AttachmentItem", is_list=True, description="Attachment upload response"
	),
	"AttachmentListResponse": make_envelope(
		"AttachmentItem", is_list=True, description="Attachment list response"
	),
	"AttachmentDownloadResponse": make_envelope(
		"AttachmentDownloadData", description="Attachment download URL response"
	),
	"DeleteAttachmentResponse": make_envelope(
		"DeleteAttachmentData", description="Attachment deletion response"
	),
	"CategoryAssignmentResponse": make_envelope(
		"CategoryAssignmentData", description="Category assignment response"
	),
	"CategoryAssignmentListResponse": make_envelope(
		"CategoryAssignmentListData",
		description="Category assignment list response",
	),
	"RoleLevelResponse": make_envelope("RoleLevelDetailData", description="Role level response"),
	"RoleLevelListResponse": make_envelope("RoleLevelListData", description="Role level list response"),
	"DashboardChartResponse": make_envelope(
		"DashboardChartRow",
		is_list=True,
		description="Rows of one public dashboard chart; meta.as_of is the rollup time, meta.unavailable is set when the chart is empty because no data was kept",
	),
	"DashboardChartsResponse": make_envelope(
		"DashboardChartsData",
		description="Rows per chart; meta.as_of, meta.errors and meta.unavailable per chart",
	),
}


# ---------------------------------------------------------------------------
# Query Parameters Catalog
# ---------------------------------------------------------------------------
QP: dict[str, list[dict[str, Any]]] = {
	"SubmitterOptions": [
		{
			"name": "search_country",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter phone ISD prefixes",
		},
		{
			"name": "country",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Exact country name or 2-letter ISO code",
		},
		{
			"name": "include_phone_extensions",
			"in": "query",
			"required": False,
			"schema": B(default=True),
			"description": "Include phone dialing codes",
		},
		{
			"name": "service_category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter grievance types by category",
		},
	],
	"GrievanceOptions": [
		{
			"name": "service_category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter grievance types and officers by service category",
		},
		{
			"name": "category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Alias for service_category",
		},
		{
			"name": "department",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Department ID to cascade and return active assigned officers",
		},
		{
			"name": "administrative_area",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Administrative area ID to filter officer assignments",
		},
	],
	"AdministrativeAreas": [
		{
			"name": "parent",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Parent area ID, path_code, or comma-separated list of parents (cascading drill-down)",
		},
		{
			"name": "level_name",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter by administrative tier (e.g. Region, Zone, Woreda, Kebele)",
		},
		{
			"name": "search",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Free text search by area name or code",
		},
		{
			"name": "ancestors_of",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Return breadcrumb chain for specified node",
		},
		{
			"name": "limit",
			"in": "query",
			"required": False,
			"schema": I(default=100, maximum=500),
			"description": "Maximum records returned",
		},
	],
	"ViewAttachment": [
		{
			"name": "download",
			"in": "query",
			"required": False,
			"schema": B(default=False),
			"description": "Send Content-Disposition: attachment (Save As) instead of inline",
		}
	],
}

QP["DashboardChart"] = [
	{
		"name": "region",
		"in": "query",
		"required": False,
		"schema": S(),
		"description": "Comma-separated Region P-codes (e.g. ET04)",
	},
	{
		"name": "service_category",
		"in": "query",
		"required": False,
		"schema": S(),
		"description": "Comma-separated service category names; `category` is accepted as an alias",
	},
	{
		"name": "level",
		"in": "query",
		"required": False,
		"schema": S(),
		"description": "Comma-separated administrative levels: federal, region, woreda (grvByAdminLevel). An officer is limited to their own level and the ones below it",
	},
	{
		"name": "from",
		"in": "query",
		"required": False,
		"schema": S(format="date"),
		"description": "Period start (trend, category-resolution and average-resolution charts)",
	},
	{
		"name": "to",
		"in": "query",
		"required": False,
		"schema": S(format="date"),
		"description": "Period end, default today",
	},
	{
		"name": "month",
		"in": "query",
		"required": False,
		"schema": S(pattern=r"^\d{4}-\d{2}$"),
		"description": "YYYY-MM for grvPerformanceKpis and grvPerformanceScore, default the current month",
	},
	{
		"name": "as_of_month",
		"in": "query",
		"required": False,
		"schema": S(pattern=r"^\d{4}-\d{2}$"),
		"description": (
			"YYYY-MM for grvResolutionRateByRegion only: read it as at the end of that month "
			"instead of today. Not `month`, so a page can send `month` to every chart safely"
		),
	},
	{
		"name": "granularity",
		"in": "query",
		"required": False,
		"schema": S(enum=["month", "week"], default="month"),
		"description": "Period size for grvNetBacklogTrend",
	},
]

# The admin form takes several charts at once and two filters the public one does not.
QP["DashboardCharts"] = [
	{
		"name": "charts",
		"in": "query",
		"required": False,
		"schema": S(),
		"description": "Comma-separated chart ids (at most 20); all charts when omitted",
	},
	*QP["DashboardChart"],
	{
		"name": "assigned_dept",
		"in": "query",
		"required": False,
		"schema": S(),
		"description": "Comma-separated department names",
	},
	{
		"name": "limit",
		"in": "query",
		"required": False,
		"schema": I(minimum=1, maximum=50, default=10),
		"description": "Rows for grvRecent",
	},
]


# ---------------------------------------------------------------------------
# Dynamic Discovery & Spec Builder
# ---------------------------------------------------------------------------
def _determine_tag(path: str, func_name: str) -> str:
	if "/health" in path or "/ping" in path:
		return "Health & Monitoring"
	if path.startswith("/api/v1/submitters"):
		return "Submitter Management"
	if path.startswith("/api/v1/administrative-areas"):
		return "Administrative Areas"
	if path.startswith("/api/v1/service-provider"):
		return "Service Providers"
	if path.startswith(
		(
			"/api/v1/category-assignments",
			"/api/v1/officers",
			"/api/v1/response-templates",
			"/api/v1/notification-templates",
			"/api/v1/sla-configurations",
			"/api/v1/sla-policy",
			"/api/v1/service-categories",
			"/api/v1/grievance-types",
		)
	):
		return "Administration"
	if path.startswith("/api/v1/charts"):
		return "Dashboard Charts"
	if path.startswith("/api/v1/webhooks"):
		return "Webhooks"
	if "/change-requests" in path or path.startswith("/api/v1/change-requests"):
		return "Change Requests"
	if "/attachments" in path or path.startswith("/api/v1/attachments"):
		return "Attachments"
	if any(
		kw in path
		for kw in (
			"action",
			"message",
			"timeline",
			"reassign",
			"defer-sla",
			"anonymity-decision",
			"response-templates",
		)
	):
		return "Grievance Lifecycle & Actions"
	return "Grievances Core"


def _determine_response(func_name: str, path: str, method: str) -> str | None:
	mapping = {
		"get_health": "HealthResponse",
		"get_ping": "PingResponse",
		"get_areas": "AdministrativeAreasListResponse",
		"get_area_ancestors": "AreaAncestorsResponse",
		"list_grievances": "GrievanceListResponse",
		"submit": "GrievanceSubmitResultResponse",
		"ivr_grievance": "IvrGrievanceResultResponse",
		"action": "GrievanceActionResultResponse",
		"timeline": "GrievanceTimelineResponse",
		"response_templates": "ResponseTemplatesResponse",
		"message": "GrievanceMessageResponse",
		"reassign": "GrievanceChangeResponse",
		"defer_sla": "GrievanceChangeResponse",
		"decide_deferral": "GrievanceChangeResponse",
		"decide_reassignment": "GrievanceChangeResponse",
		"feedback": "GrievanceFeedbackResponse",
		"anonymity_decision": "GrievanceChangeResponse",
		"summary": "GrievanceStatusSummaryResponse",
		"options": "SubmitterOptionsResponse" if "submitters" in path else "GrievanceOptionsResponse",
		"me": "SubmitterProfileResponse",
		"submit_documents": "AttachmentUploadResponse",
		"get_attachments": "AttachmentListResponse",
		"download": "AttachmentDownloadResponse",
		"view": None,  # Binary stream
		"delete": "DeleteAttachmentResponse",
		"list_requests": "ChangeRequestListResponse",
		"get_request": "ChangeRequestResponse",
		"decide": "GrievanceChangeResponse",
		"register_submitter": "SubmitterRegisterResponse",
		"block_submitter": "SubmitterBlockResponse",
		"unblock_submitter": "SubmitterBlockResponse",
		"search_farmer": "FarmerSearchResponse",
		"farmer_details": "FarmerDetailsResponse",
		"get_charts": "DashboardChartsResponse",
		"list_assignments": "CategoryAssignmentListResponse",
		"get_assignment": "CategoryAssignmentResponse",
		"create_assignment": "CategoryAssignmentResponse",
		"update_assignment": "CategoryAssignmentResponse",
		"deactivate_assignment": "CategoryAssignmentResponse",
		"list_role_levels": "RoleLevelListResponse",
		"get_role_level": "RoleLevelResponse",
	}
	if func_name.startswith("get_public_chart_"):
		return "DashboardChartResponse"
	return mapping.get(func_name, "GrievanceActionResultResponse" if method == "POST" else None)


APP = "oan-grievance"
API_MODULES = [
	"oan_grievance_service.api.router",
	*(
		f"oan_grievance_service.api.v1.{name}"
		for name in (
			"administrative_area",
			"grievance",
			"attachment",
			"category_assignment",
			"change_request",
			"charts",
			"notification_template",
			"officer",
			"officer_statistics",
			"organization",
			"profile",
			"response_config",
			"role_level",
			"service_provider",
			"service_provider_source",
			"sla_rules",
			"sla_settings",
			"submitter",
			"taxonomy",
			"webhooks",
		)
	),
]
DESCRIPTION = (
	"Grievance management and citizen feedback service for OpenAgriNet (OAN). "
	+ "Provides RESTful endpoints for submitting complaints, tracking resolution progress, "
	+ "cascading administrative area drill-downs, citizen-officer timeline messaging, "
	+ "escalation management, change requests, and case resolution workflows."
)
SERVERS = [
	{"url": "http://localhost:8000", "description": "Local Frappe Bench"},
	{"url": "https://grievance.openagrinet.org", "description": "Production Grievance Gateway"},
]
TAGS = [
	{"name": "Health & Monitoring", "description": "Service health probes and uptime pings"},
	{
		"name": "Submitter Management",
		"description": "Intake reference options, submitter registration, and moderation",
	},
	{
		"name": "Administrative Areas",
		"description": "Cascading geographic drill-downs, breadcrumbs, and search",
	},
	{"name": "Grievances Core", "description": "Case intake, tracking, and filtered list views"},
	{
		"name": "Change Requests",
		"description": "Request, review, and decide on grievance field changes (department, officer, SLA deferral, anonymity)",
	},
	{
		"name": "Grievance Lifecycle & Actions",
		"description": "Communication threads, notes, reopen, reject, escalate, and resolution confirmation",
	},
	{
		"name": "Attachments",
		"description": "Supporting document and evidence upload, listing, download, and deletion",
	},
	{
		"name": "Administration",
		"description": "Category-to-department routing rules, and the response templates officers respond with",
	},
	{
		"name": "Organizations",
		"description": "NGO, FPO and cooperative registration, profile, and the admin verification queue",
	},
	{
		"name": "Dashboard Charts",
		"description": "Aggregate counts from the dashboard rollups, refreshed within seconds of a case changing and at least every 15 minutes, for the OAN programme dashboards",
	},
	{
		"name": "Webhooks",
		"description": "Inbound calls from partner platforms, sent with a Frappe API key and secret",
	},
]
# Besides BearerAuth, which the generator always declares.
SECURITY_SCHEMES = {
	"FrappeTokenAuth": {
		"type": "apiKey",
		"in": "header",
		"name": "Authorization",
		"description": "Frappe API key and secret of a user holding Grievance Dashboard Reader, sent as `token <api_key>:<api_secret>`. Generated on that user in the desk and checked by the platform; the gateway passes the header through.",
	},
	"GatewayKeyAuth": {
		"type": "apiKey",
		"in": "header",
		"name": "apikey",
		"description": "The gateway key issued to the IVR platform, sent as `apikey: <key>`. The gateway checks it, removes it, and calls Frappe as the Grievance Submitter user with that user's own API key and secret, which the platform never holds.",
	},
}
# Path parameters whose name alone does not say what they are.
PATH_PARAM_DOCS = {
	"ticket_number": "Unique grievance ticket number (e.g. 3-001-002A-0 or 3001002A0)",
	"attachment": "Unique attachment record ID",
	"area_id_or_path": "Area ID (e.g. 'kebele-ET140108101008') or path_code ('ET.ET14.01.08.101.008')",
	"level_code": "Unique grievance role level code (e.g. 'nodal_officer', 'department_head')",
}


# ---------------------------------------------------------------------------
# Hooks for oan_auth_service.openapi_generator: what the endpoints cannot declare
# ---------------------------------------------------------------------------
def query_parameters_for(func_name: str, path: str, method: str) -> list[dict[str, Any]]:
	"""Query string of the few GET routes that read it without a request model."""
	if func_name == "get_areas":
		return QP["AdministrativeAreas"]
	if func_name == "options" and "submitters" in path:
		return QP["SubmitterOptions"]
	if func_name == "options" and "grievances" in path:
		return QP["GrievanceOptions"]
	if func_name == "view":
		return QP["ViewAttachment"]
	if func_name == "get_charts":
		return QP["DashboardCharts"]
	if func_name.startswith("get_public_chart_"):
		return QP["DashboardChart"]
	return []


def write_registration_overlay(extras_schema: dict[str, Any]) -> None:
	"""Write an OpenAPI Overlay that attaches this app's registration fields to
	oan_auth_service's RegisterRequest under `profile.grievance`.

	Apply it over auth's spec at deployment (e.g. `redocly`/`openapi-overlay` tooling)
	to get the composed register body without editing either repo's spec.
	"""
	overlay = {
		"overlay": "1.0.0",
		"info": {"title": "OAN Grievance registration fields", "version": "1.0.0"},
		"actions": [
			{
				"target": "$.components.schemas",
				"description": "Register the grievance registration fields schema.",
				"update": {"GrievanceRegistrationExtras": extras_schema},
			},
			{
				"target": "$.components.schemas.RegisterRequest.properties",
				"description": "Accept grievance fields under profile.grievance.",
				"update": {
					"profile": {
						"type": "object",
						"description": "App-specific registration fields, namespaced per app.",
						"properties": {
							"grievance": {"$ref": "#/components/schemas/GrievanceRegistrationExtras"}
						},
					}
				},
			},
		],
	}
	header = (
		"# OpenAPI Overlay for POST /api/v1/auth/register (served by oan_auth_service).\n"
		"# Generated by generate_openapi_spec.py -- do not edit manually.\n"
	)
	REGISTRATION_OVERLAY_OUTPUT.write_text(
		header + yaml.safe_dump(overlay, sort_keys=False), encoding="utf-8"
	)


def main() -> None:
	# Declared in this order, which is the order of the spec's paths. Done before the
	# import below, which would otherwise pull the organization routes in first.
	import_route_modules("oan_grievance_service", API_MODULES)
	from oan_grievance_service.api.v1.profile import RegistrationExtras

	# Extra body fields this app reads on POST /api/v1/auth/register (served by
	# oan_auth_service). Published as a component so clients and the gateway can see them.
	extras = oas30(RegistrationExtras.model_json_schema(ref_template="#/components/schemas/{model}"))
	DATA_SCHEMAS["GrievanceRegistrationExtras"] = extras
	write_registration_overlay(extras)

	generate_spec_and_gateway(
		gateway=False,
		app_name=APP,
		module="oan_grievance_service",
		modules=API_MODULES,
		title="OAN Grievance Service API",
		description=DESCRIPTION,
		upstream_host_env="OAN_GRIEVANCE_UPSTREAM_HOST",
		tiers={},
		openapi_dir=SCRIPT_DIR,
		gateway_dir=REPO_ROOT / "apisix",
		servers=SERVERS,
		public_servers=PUBLIC_SERVERS,
		tags=TAGS,
		security_schemes=SECURITY_SCHEMES,
		schemas=DATA_SCHEMAS,
		envelopes=ENVELOPES,
		path_param_docs=PATH_PARAM_DOCS,
		tag_for=_determine_tag,
		response_for=_determine_response,
		query_parameters_for=query_parameters_for,
	)


if __name__ == "__main__":
	main()
