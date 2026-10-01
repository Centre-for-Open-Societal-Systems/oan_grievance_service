#!/usr/bin/env python3
"""
generate_openapi_spec.py

Builds openapi_v1.yaml and openapi_v1.public.yaml for the OAN Grievance Service.
Generates an OpenAPI 3.0.3 specification covering health monitoring, submitter options
and profiles, administrative area cascades, grievance intake, multi-select filtered listing,
tracking, timeline inspection, citizen-officer messaging, notes, escalation, rejection,
resolution confirmation, reopen workflows, and offline draft persistence.

Outputs:
  - openapi_v1.yaml: Engineering/Internal specification with vendor extensions
    (x-legacy-rpc-method, x-schema-confidence).
  - openapi_v1.public.yaml: Public/Gateway contract with vendor extensions stripped.

Usage:
  python3 generate_openapi_spec.py
"""

import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
INTERNAL_SPEC_OUTPUT = SCRIPT_DIR / "openapi_v1.yaml"
PUBLIC_SPEC_OUTPUT = SCRIPT_DIR / "openapi_v1.public.yaml"


# ---------------------------------------------------------------------------
# Schema building helper functions
# ---------------------------------------------------------------------------
def S(**kw):
	return {"type": "string", **kw}


def I(**kw):  # noqa: E743
	return {"type": "integer", **kw}


def N(**kw):
	return {"type": "number", **kw}


def B(**kw):
	return {"type": "boolean", **kw}


def ARR(items, **kw):
	return {"type": "array", "items": items, **kw}


def OBJ(props, required=None, description=None, confidence=None, **kw):
	d = {"type": "object", "properties": props, **kw}
	if required:
		d["required"] = required
	if description:
		d["description"] = description
	if confidence:
		d["x-schema-confidence"] = confidence
	return d


# The body of a route that streams a file rather than a JSON envelope.
BINARY = {"type": "string", "format": "binary"}


def REF(name):
	return {"$ref": f"#/components/schemas/{name}"}


RESPONSE_TYPES = ["Resolved", "Partially Resolved", "Referred to another dept", "Requires further info"]

# ---------------------------------------------------------------------------
# Components: Data Schemas
# ---------------------------------------------------------------------------
DATA_SCHEMAS = {}


def data(name, schema):
	DATA_SCHEMAS[name] = schema
	return name


# Standard Envelopes & Metadata
data(
	"ApiMeta",
	OBJ(
		{
			"api_version": S(example="v1", description="Semantic API version"),
			"status": S(example="current", description="Lifecycle status"),
		},
		required=["api_version"],
	),
)

data(
	"StandardErrorResponse",
	OBJ(
		{
			"status": S(example="error", enum=["error"]),
			"message": S(description="Human-readable error description"),
			"exception": S(nullable=True, description="Exception class name"),
			"errors": ARR(
				OBJ(
					{
						"field": S(nullable=True, description="Field causing the validation error"),
						"message": S(description="Error message for the specific field"),
					}
				),
				nullable=True,
				description="Structured validation errors if applicable",
			),
			"meta": REF("ApiMeta"),
			"request_id": S(format="uuid", nullable=True, description="Tracing correlation ID"),
		},
		required=["status", "message"],
		description="Standard error envelope returned on 4xx/5xx responses",
	),
)

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

# Administrative Areas
data(
	"AdministrativeAreaItem",
	OBJ(
		{
			"area_id": S(example="region-ET14", description="Canonical area ID"),
			"area_name": S(example="Oromia"),
			"code": S(example="ET14", nullable=True),
			"path_code": S(example="ET.ET14", nullable=True),
			"level_name": S(example="Region", enum=["Region", "Zone", "Woreda", "Kebele"]),
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

# Grievance Drafts
data(
	"DraftData",
	OBJ(
		{
			"name": S(description="Draft document name"),
			"ticket_number": S(nullable=True, description="Assigned ticket number if submitted"),
			"client_submission_uuid": S(description="Stable client-generated draft key"),
			"status": S(example="Draft"),
			"workflow_state": S(example="Draft"),
			"submission_channel": S(nullable=True),
			"submitter_type": S(nullable=True),
			"submitter_name": S(nullable=True),
			"contact_mobile": S(nullable=True),
			"contact_email": S(nullable=True),
			"administrative_area": S(nullable=True),
			"administrative_unit": S(nullable=True),
			"service_category": S(nullable=True),
			"grievance_type": S(nullable=True),
			"associated_service_provider": S(nullable=True),
			"description": S(nullable=True),
			"desired_outcome": S(nullable=True),
			"is_anonymous": I(enum=[0, 1]),
			"attachments": ARR(OBJ({})),
			"attachment_count": I(),
			"owner": S(nullable=True),
		},
		required=["client_submission_uuid", "status", "workflow_state"],
		description="Draft grievance state",
	),
)

data(
	"DraftDiscardData",
	OBJ(
		{
			"discarded": B(description="Whether the draft was successfully discarded"),
		},
		required=["discarded"],
		description="Outcome of draft discard operation",
	),
)

# Grievance Core & Lifecycle
data(
	"GrievanceSubmitResultData",
	OBJ(
		{
			"ticket_number": S(example="ET14IN000012026", description="Formatted ticket number"),
			"status": S(example="Submitted"),
			"acknowledgement_status": S(example="Sent", nullable=True),
			"assigned_officer": S(nullable=True),
			"sla_target_date": S(format="date-time", nullable=True),
			"creation": S(format="date-time"),
			"is_anonymous": I(enum=[0, 1], example=0),
		},
		required=["ticket_number", "status", "creation"],
		description="Acknowledgement outcome and ticket identifier returned on submission",
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
				enum=[
					"Draft",
					"Submitted",
					"Under Investigation",
					"More Info Needed",
					"Resolved",
					"Closed",
					"Reopened",
					"Rejected",
				],
			),
			"service_category": S(example="Inputs"),
			"grievance_type": S(example="Fertilizer Shortage"),
			"administrative_area": S(example="kebele-ET140108101008"),
			"administrative_unit": S(nullable=True),
			"submitter_name": S(example="Abebe Bikila"),
			"contact_mobile": S(example="+251911887766"),
			"assigned_officer": S(nullable=True),
			"sla_target_date": S(format="date-time", nullable=True),
			"is_escalated": I(enum=[0, 1]),
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
			"creation": S(format="date-time"),
			"modified": S(format="date-time"),
		},
		required=["ticket_number", "status", "description", "creation"],
		description="Full grievance case details",
	),
)

data(
	"TimelineEventItem",
	OBJ(
		{
			"event_type": S(
				example="Status Change",
				enum=[
					"Submission",
					"Status Change",
					"Assignment",
					"Note",
					"Message",
					"Escalation",
					"Resolution",
					"Reopen",
					"Rejection",
				],
			),
			"from_status": S(nullable=True),
			"to_status": S(nullable=True),
			"actor": S(description="User or officer who triggered the event"),
			"actor_role": S(nullable=True),
			"message": S(nullable=True),
			"communication_channel": S(nullable=True),
			"is_internal": I(enum=[0, 1], example=0),
			"creation": S(format="date-time"),
		},
		required=["event_type", "actor", "creation"],
		description="Audit and communication event on the grievance timeline",
	),
)

data(
	"GrievanceTimelineData",
	OBJ(
		{
			"ticket_number": S(example="ET14IN000012026"),
			"current_status": S(example="Under Investigation"),
			"events": ARR(REF("TimelineEventItem")),
		},
		required=["ticket_number", "current_status", "events"],
		description="Chronological event log and message history",
	),
)

data(
	"GrievanceActionResultData",
	OBJ(
		{
			"ticket_number": S(example="ET14IN000012026"),
			"status": S(example="Under Investigation"),
			"message": S(description="Result confirmation message"),
			"action_timestamp": S(format="date-time", nullable=True),
		},
		required=["ticket_number", "status", "message"],
		description="Outcome of a state transition or action on a grievance",
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
		description="Status summary for the all-grievances queue. Draft is excluded.",
	),
)

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


data(
	"ResponseTemplateVersion",
	OBJ(
		{
			"version": I(minimum=1, example=1, description="Version number this wording had"),
			"title": S(nullable=True),
			"response_type": S(nullable=True, description="Response type this version was filed under"),
			"service_category": S(nullable=True, description="Service category name, kept as text"),
			"grievance_type": S(nullable=True, description="Grievance type name, kept as text"),
			"action_taken": S(nullable=True),
			"resolution_summary": S(nullable=True),
			"replaced_on": S(
				format="date-time", nullable=True, description="When an edit replaced this version"
			),
			"replaced_by": S(nullable=True, description="User who made that edit"),
			"change_note": S(nullable=True, description="Why the edit was made, when the editor said"),
		},
		required=["version"],
		description="One superseded wording of a response template",
	),
)

data(
	"ResponseTemplate",
	OBJ(
		{
			"id": S(example="RT-00001", description="Template id"),
			"title": S(),
			"service_category": S(description="Service category id"),
			"service_category_name": S(nullable=True),
			"grievance_type": S(
				nullable=True, description="Subcategory id. Null when the template covers the whole category."
			),
			"grievance_type_name": S(nullable=True),
			"response_type": S(enum=RESPONSE_TYPES),
			"action_taken": S(description="Action-taken wording. May carry {{ name }} placeholders."),
			"resolution_summary": S(
				description="Resolution-summary wording. May carry {{ name }} placeholders."
			),
			"placeholders": ARR(
				S(), description="Distinct placeholder names across both texts, in order of appearance"
			),
			"version": I(
				minimum=1, example=1, description="Current version. Raised by one on every content edit."
			),
			"is_active": B(),
			"use_count": I(
				minimum=0, description="Responses filed from this template, counted from the response record"
			),
			"last_used_on": S(format="date-time", nullable=True),
			"created_on": S(format="date-time", nullable=True),
			"modified_on": S(format="date-time", nullable=True),
			"modified_by": S(nullable=True),
		},
		required=[
			"id",
			"title",
			"service_category",
			"response_type",
			"action_taken",
			"resolution_summary",
			"placeholders",
			"version",
			"is_active",
			"use_count",
		],
		description="A response template",
	),
)

data(
	"ResponseTemplateDetail",
	{
		"allOf": [
			REF("ResponseTemplate"),
			OBJ(
				{
					"versions": ARR(
						REF("ResponseTemplateVersion"), description="Earlier versions, newest first"
					)
				},
				required=["versions"],
			),
		],
		"description": "A response template with its version history",
	},
)

data(
	"ResponseTemplateData",
	OBJ({"template": REF("ResponseTemplateDetail")}, required=["template"], description="One template"),
)

data(
	"ResponseTemplateListData",
	OBJ(
		{"templates": ARR(REF("ResponseTemplate")), "pagination": REF("PaginationMeta")},
		required=["templates", "pagination"],
		description="One page of templates",
	),
)

data(
	"ResponseTemplateDeleteData",
	OBJ(
		{
			"template": REF("ResponseTemplateDetail"),
			"deleted": B(
				description="True when the template was removed, false when it was only deactivated"
			),
		},
		required=["template", "deleted"],
		description="Outcome of a template delete",
	),
)


# ---------------------------------------------------------------------------
# Request Body Schemas
# ---------------------------------------------------------------------------
REQ = {}

REQ["SaveDraftRequest"] = OBJ(
	{
		"client_submission_uuid": S(
			minLength=1, nullable=True, description="Stable client-generated draft key"
		),
		"client_uuid": S(nullable=True, description="Alias for client_submission_uuid"),
		"submission_channel": S(nullable=True, description="Submission channel"),
		"submitter_type": S(nullable=True, description="Submitter type"),
		"submitter_name": S(nullable=True, description="Submitter citizen name"),
		"contact_mobile": S(nullable=True, description="Contact mobile phone"),
		"contact_email": S(format="email", nullable=True, description="Contact email address"),
		"administrative_area": S(nullable=True, description="Administrative area ID or path_code"),
		"administrative_unit": S(nullable=True, description="Specific local landmark or unit"),
		"service_category": S(nullable=True, description="Service category name"),
		"grievance_type": S(nullable=True, description="Grievance type name"),
		"associated_service_provider": S(nullable=True, description="Associated service provider"),
		"description": S(nullable=True, description="Draft narrative description"),
		"desired_outcome": S(nullable=True, description="Desired resolution outcome"),
		"is_anonymous": I(enum=[0, 1], default=0, nullable=True, description="1 if anonymous"),
		"validate": B(default=False, description="If true, execute validation on the draft payload"),
	},
	required=[],
	description="Payload for saving or updating a grievance draft",
)

REQ["SubmitDocumentsRequest"] = OBJ(
	{
		"grievance": S(description="Grievance ticket number or document identifier"),
		"document_type": S(nullable=True, description="Document type or category"),
		"response": S(nullable=True, description="Associated formal response ID if applicable"),
	},
	required=["grievance"],
	description="Supporting document upload request",
)

REQ["SubmitDraftRequest"] = OBJ(
	{
		"client_submission_uuid": S(minLength=1, description="Stable client-generated draft key to submit"),
		"consent_given": I(enum=[0, 1], default=1, description="1 to record citizen consent"),
		"is_anonymous": I(enum=[0, 1], default=0, description="1 to request anonymity"),
		"anonymity_justification": S(nullable=True, description="Justification for anonymity"),
		"submission_channel": S(nullable=True, description="Submission channel"),
		"submitter_type": S(nullable=True, description="Submitter type"),
		"submitter_name": S(nullable=True, description="Submitter citizen name"),
		"contact_mobile": S(nullable=True, description="Contact mobile phone"),
		"contact_email": S(format="email", nullable=True, description="Contact email"),
		"administrative_area": S(nullable=True, description="Administrative area"),
		"administrative_unit": S(nullable=True, description="Administrative unit"),
		"service_category": S(nullable=True, description="Service category"),
		"grievance_type": S(nullable=True, description="Grievance type"),
		"associated_service_provider": S(nullable=True, description="Associated service provider"),
		"description": S(nullable=True, description="Narrative description"),
		"desired_outcome": S(nullable=True, description="Desired outcome"),
	},
	required=["client_submission_uuid"],
	description="Payload for submitting a saved grievance draft",
)

REQ["DiscardDraftRequest"] = OBJ(
	{
		"client_submission_uuid": S(minLength=1, description="Stable client-generated draft key to discard"),
	},
	required=["client_submission_uuid"],
	description="Payload for discarding an unsubmitted grievance draft",
)

REQ["PostMessageRequest"] = OBJ(
	{
		"message": S(minLength=1, description="Message text posted to the public conversation thread"),
	},
	required=["message"],
	description="Public message payload",
)

REQ["AddNoteRequest"] = OBJ(
	{
		"note": S(minLength=1, description="Internal or external note content"),
		"is_internal": B(default=True, description="Whether this note is hidden from citizens (staff-only)"),
	},
	required=["note"],
	description="Staff note payload",
)

REQ["GrievanceActionRequest"] = OBJ(
	{
		"action": S(
			minLength=1,
			description="Canonical workflow action name (e.g. 'Start Work', 'Confirm Resolution', 'Reopen', 'Reject', 'Escalate')",
		),
		"reason": S(nullable=True, description="Mandatory justification when required by the action"),
		"note": S(nullable=True, description="Optional note text"),
		"rating": I(
			minimum=1,
			maximum=5,
			nullable=True,
			description="Citizen satisfaction rating (1-5) for resolution confirmation",
		),
		"comments": S(nullable=True, description="Optional citizen feedback remarks"),
		"body": S(nullable=True, description="Response body text for submitter reply"),
	},
	required=["action"],
	description="Workflow action and state transition payload",
)

REQ["CreateCategoryAssignmentRequest"] = OBJ(
	{
		"service_category": S(minLength=1, description="Category name or code"),
		"department": S(minLength=1, description="Department name or short name"),
		"l1_officer": S(minLength=1, description="L1 nodal officer user id"),
		"l2_officer": S(nullable=True, description="L2 senior nodal officer user id. Omit or null for none."),
		"sla_days": I(minimum=1, description="SLA window in days"),
		"auto_escalate": B(default=True),
		"active": B(default=True),
	},
	required=["service_category", "department", "l1_officer", "sla_days"],
	description="Create the routing rule for one department and service category",
)

REQ["UpdateCategoryAssignmentRequest"] = OBJ(
	{
		"department": S(minLength=1, description="Department name or short name. Null or blank is rejected."),
		"l1_officer": S(minLength=1, description="L1 nodal officer user id. Null or blank is rejected."),
		"l2_officer": S(nullable=True, description="L2 senior nodal officer. Null clears it."),
		"sla_days": I(minimum=1),
		"auto_escalate": B(),
		"active": B(description="Set false to deactivate. Same as DELETE."),
	},
	description=(
		"Partial update. Omit a field to leave it unchanged. Unknown fields, including "
		+ "service_category, are rejected."
	),
)


REQ["CreateResponseTemplateRequest"] = OBJ(
	{
		"title": S(minLength=1, maxLength=140),
		"service_category": S(minLength=1, description="Category name or code"),
		"grievance_type": S(
			nullable=True, description="Subcategory id or name. Must belong to the category."
		),
		"response_type": S(enum=RESPONSE_TYPES),
		"action_taken": S(
			minLength=1,
			maxLength=500,
			description="Wording, 500 characters at most like a response. Supports {{ name }} placeholders.",
		),
		"resolution_summary": S(
			minLength=1, maxLength=10000, description="Wording. Supports {{ name }} placeholders."
		),
		"is_active": B(default=True),
	},
	required=["title", "service_category", "response_type", "action_taken", "resolution_summary"],
	description="Create a response template at version 1",
)

REQ["UpdateResponseTemplateRequest"] = OBJ(
	{
		"title": S(minLength=1, maxLength=140),
		"service_category": S(minLength=1, description="Category name or code"),
		"grievance_type": S(nullable=True, description="Subcategory. Null clears it."),
		"response_type": S(enum=RESPONSE_TYPES),
		"action_taken": S(minLength=1, maxLength=500),
		"resolution_summary": S(minLength=1, maxLength=10000),
		"is_active": B(description="Switching this alone does not create a version."),
		"expected_version": I(
			minimum=1, description="Reject the edit when the template is no longer at this version"
		),
		"change_note": S(nullable=True, maxLength=500, description="Stored with the replaced version"),
	},
	description=(
		"Partial update. Omit a field to leave it unchanged. A change to any wording or scope field "
		+ "saves the old wording to history and raises the version by one. Unknown fields are rejected."
	),
)


# ---------------------------------------------------------------------------
# Envelope Builder Helper
# ---------------------------------------------------------------------------
def make_envelope(data_ref, is_list=False, description="Successful response", paginated=False):
	data_prop = ARR(REF(data_ref)) if is_list else REF(data_ref)
	props = {
		"status": S(example="success", enum=["success"]),
		"message": S(nullable=True, description="Optional response message"),
		"data": data_prop,
		"meta": REF("ApiMeta"),
		"request_id": S(format="uuid", nullable=True, description="Tracing correlation ID"),
	}
	if paginated:
		props["pagination"] = REF("PaginationMeta")
	return OBJ(
		props,
		required=["status", "data"] + (["pagination"] if paginated else []),
		description=description,
	)


ENVELOPES = {
	"HealthResponse": make_envelope("HealthData", description="Health check response"),
	"PingResponse": make_envelope("PingData", description="Ping response"),
	"SubmitterOptionsResponse": make_envelope(
		"SubmitterOptionsData", description="Submitter options response"
	),
	"SubmitterProfileResponse": make_envelope(
		"SubmitterProfileData", description="Submitter profile response"
	),
	"AdministrativeAreasListResponse": make_envelope(
		"AdministrativeAreasListData", description="Administrative areas list response"
	),
	"AreaAncestorsResponse": make_envelope("AreaAncestorsData", description="Area ancestors response"),
	"DraftResponse": make_envelope("DraftData", description="Grievance draft response"),
	"DraftSubmitResultResponse": make_envelope(
		"GrievanceSubmitResultData", description="Grievance draft submission outcome response"
	),
	"DraftDiscardResponse": make_envelope("DraftDiscardData", description="Draft discard outcome response"),
	"GrievanceSubmitResultResponse": make_envelope(
		"GrievanceSubmitResultData", description="Grievance submission outcome response"
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
	"GrievanceTimelineResponse": make_envelope(
		"GrievanceTimelineData", description="Grievance timeline response"
	),
	"GrievanceActionResultResponse": make_envelope(
		"GrievanceActionResultData", description="Action result response"
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
	"ResponseTemplateResponse": make_envelope("ResponseTemplateData", description="Response template"),
	"ResponseTemplateListResponse": make_envelope(
		"ResponseTemplateListData", description="Response template list"
	),
	"ResponseTemplateDeleteResponse": make_envelope(
		"ResponseTemplateDeleteData", description="Response template delete outcome"
	),
}


# ---------------------------------------------------------------------------
# Query Parameters Catalog
# ---------------------------------------------------------------------------
QP = {
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
	"AdministrativeAreas": [
		{
			"name": "parent",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Parent area ID or path_code (drill-down)",
		},
		{
			"name": "level_name",
			"in": "query",
			"required": False,
			"schema": S(enum=["Region", "Zone", "Woreda", "Kebele"]),
			"description": "Filter by administrative tier",
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
	"ListGrievances": [
		{
			"name": "page",
			"in": "query",
			"required": False,
			"schema": I(default=1, minimum=1),
			"description": "Page number",
		},
		{
			"name": "page_size",
			"in": "query",
			"required": False,
			"schema": I(default=20, minimum=1, maximum=100),
			"description": "Page size",
		},
		{
			"name": "status",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": (
				"Comma-separated queue statuses: All, In Progress, Require More Info, "
				+ "Rejected, Resolved, Closed. Draft is excluded."
			),
		},
		{
			"name": "category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Comma-separated service categories e.g. 'Inputs,Payments'",
		},
		{
			"name": "service_category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Single category alias",
		},
		{
			"name": "grievance_type",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Comma-separated grievance types",
		},
		{
			"name": "region",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Comma-separated regional administrative area IDs",
		},
		{
			"name": "administrative_area",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Single administrative area ID or path_code",
		},
		{
			"name": "from_date",
			"in": "query",
			"required": False,
			"schema": S(format="date"),
			"description": "Creation date lower bound (YYYY-MM-DD)",
		},
		{
			"name": "to_date",
			"in": "query",
			"required": False,
			"schema": S(format="date"),
			"description": "Creation date upper bound (YYYY-MM-DD)",
		},
		{
			"name": "search",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Free text search matching ticket, citizen name, or phone",
		},
		{
			"name": "sort_by",
			"in": "query",
			"required": False,
			"schema": S(default="creation"),
			"description": "Column to order by",
		},
		{
			"name": "sort_order",
			"in": "query",
			"required": False,
			"schema": S(default="desc", enum=["asc", "desc"]),
			"description": "Sort direction",
		},
	],
	"ListCategoryAssignments": [
		{
			"name": "service_category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter by service category name or code",
		},
		{
			"name": "department",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter by department name or short name",
		},
		{
			"name": "active",
			"in": "query",
			"required": False,
			"schema": B(),
			"description": "Filter by active flag. Omit to return active and inactive rules.",
		},
		{
			"name": "page",
			"in": "query",
			"required": False,
			"schema": I(default=1, minimum=1),
			"description": "Page number",
		},
		{
			"name": "page_size",
			"in": "query",
			"required": False,
			"schema": I(default=20, minimum=1, maximum=100),
			"description": "Page size",
		},
	],
	"ListResponseTemplates": [
		{
			"name": "service_category",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter by service category name or code",
		},
		{
			"name": "grievance_type",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Filter by subcategory (grievance type) id or name",
		},
		{
			"name": "response_type",
			"in": "query",
			"required": False,
			"schema": S(enum=RESPONSE_TYPES),
			"description": "Filter by response type",
		},
		{
			"name": "is_active",
			"in": "query",
			"required": False,
			"schema": B(),
			"description": "Filter by active flag. Omit to return active and inactive templates.",
		},
		{
			"name": "q",
			"in": "query",
			"required": False,
			"schema": S(),
			"description": "Title contains this text",
		},
		{
			"name": "page",
			"in": "query",
			"required": False,
			"schema": I(default=1, minimum=1),
			"description": "Page number",
		},
		{
			"name": "page_size",
			"in": "query",
			"required": False,
			"schema": I(default=20, minimum=1, maximum=100),
			"description": "Page size",
		},
	],
}


# ---------------------------------------------------------------------------
# Routes Specification
# ---------------------------------------------------------------------------
def R(
	method,
	path,
	summary,
	tag,
	security,
	request=None,
	request_content_type="application/json",
	query=None,
	response=None,
	response_content_type="application/json",
	path_params=None,
	legacy="",
	status=200,
	description="",
):
	return dict(
		method=method.lower(),
		path=path,
		summary=summary,
		tag=tag,
		security=security,
		request=request,
		request_content_type=request_content_type,
		query=query or [],
		response=response,
		response_content_type=response_content_type,
		path_params=path_params or [],
		legacy=legacy,
		status=status,
		description=description,
	)


# Kept out of the ROUTES list: semgrep reads implicitly joined strings inside a
# list as a mistake, and one long line would trip the formatter.
VIEW_ATTACHMENT_DESCRIPTION = (
	"Stream the bytes of one attachment once it has been scanned clean, under the same "
	"bearer token that listed the case. The body is the file itself in its stored MIME "
	"type, served inline by default with an ETag of its SHA-256 and Cache-Control: "
	"private, no-store. Pending, Infected and Failed scans are withheld."
)

SLA_SHARED_NOTE = (
	" sla_days and auto_escalate belong to the service category, not to the rule. Departments that serve "
	+ "the same category share one SLA row, so changing them through one department's rule changes them "
	+ "for every department's rule on that category."
)

TEMPLATE_PATH_PARAMS = [
	{
		"name": "template",
		"in": "path",
		"required": True,
		"schema": S(),
		"description": "Response template id, for example RT-00001",
	}
]

ROUTES = [
	# Domain 1: Health & Monitoring
	R(
		"get",
		"/api/v1/grievances/health",
		summary="Grievance service health check",
		tag="Health & Monitoring",
		security=[],
		response="HealthResponse",
		legacy="oan_grievance_service.api.router.get_health",
		description="Lightweight health check endpoint for container probes and API gateway health checks.",
	),
	R(
		"get",
		"/api/v1/grievances/ping",
		summary="Grievance service ping",
		tag="Health & Monitoring",
		security=[],
		response="PingResponse",
		legacy="oan_grievance_service.api.router.get_ping",
		description="Ping endpoint returning pong for uptime and connectivity checks.",
	),
	# Domain 2: Submitter Management
	R(
		"get",
		"/api/v1/submitters/options",
		summary="Dropdown options and reference data for submitters",
		tag="Submitter Management",
		security=[],
		query=QP["SubmitterOptions"],
		response="SubmitterOptionsResponse",
		legacy="oan_grievance_service.api.v1.submitter.options",
		description=(
			"Returns public dropdown options and intake reference data: active submitter types, "
			+ "submission channels, languages, categories, grievance types, and dialing prefixes."
		),
	),
	R(
		"get",
		"/api/v1/submitters/me",
		summary="Get current submitter profile (Deprecated)",
		tag="Submitter Management",
		security=[{"BearerAuth": []}],
		response="SubmitterProfileResponse",
		legacy="oan_grievance_service.api.v1.submitter.me",
		description=(
			"Returns the Grievance Submitter Profile for the currently authenticated user. "
			+ "Deprecated: Prefer GET /api/v1/auth/me which returns namespaced profiles under `data.profiles.grievance`."
		),
	),
	# Domain 3: Administrative Areas
	R(
		"get",
		"/api/v1/administrative-areas",
		summary="Fetch administrative areas (cascading drill-down or text search)",
		tag="Administrative Areas",
		security=[],
		query=QP["AdministrativeAreas"],
		response="AdministrativeAreasListResponse",
		legacy="oan_grievance_service.api.v1.administrative_area.get_areas",
		description=(
			"Public endpoint to fetch administrative areas for cascading dropdowns and searches. "
			+ "Supports four query modes: cascading drill-down by parent, tier filter by level_name, "
			+ "free-text search, and ancestor breadcrumbs."
		),
	),
	R(
		"get",
		"/api/v1/administrative-areas/{area_id_or_path}/ancestors",
		summary="Fetch ancestor hierarchy breadcrumbs",
		tag="Administrative Areas",
		security=[],
		path_params=[
			{
				"name": "area_id_or_path",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Area ID (e.g. 'kebele-ET140108101008') or path_code ('ET.ET14.01.08.101.008')",
			}
		],
		response="AreaAncestorsResponse",
		legacy="oan_grievance_service.api.v1.administrative_area.get_area_ancestors",
		description="Fetches the full hierarchical ancestor breadcrumb chain from root down to the specified node.",
	),
	# Domain 3b: Grievance Drafts
	R(
		"post",
		"/api/v1/drafts",
		summary="Save or update a grievance draft",
		tag="Grievance Drafts",
		security=[{"BearerAuth": []}],
		request="SaveDraftRequest",
		response="DraftResponse",
		legacy="oan_grievance_service.api.v1.draft.save",
		description="Persist an in-progress grievance draft directly on Grievance doctype with status='Draft'.",
	),
	R(
		"get",
		"/api/v1/drafts",
		summary="Get authenticated user's latest grievance draft",
		tag="Grievance Drafts",
		security=[{"BearerAuth": []}],
		response="DraftResponse",
		legacy="oan_grievance_service.api.v1.draft.load",
		description="Fetches the caller's latest unsubmitted grievance draft.",
	),
	R(
		"post",
		"/api/v1/drafts/submit",
		summary="Submit a grievance draft into an active case",
		tag="Grievance Drafts",
		security=[{"BearerAuth": []}],
		request="SubmitDraftRequest",
		response="DraftSubmitResultResponse",
		legacy="oan_grievance_service.api.v1.draft.submit_draft",
		description=(
			"Formally submits a saved draft, generating ticket number, transitioning status to Submitted, "
			+ "queuing notifications, and applying routing rules."
		),
	),
	R(
		"delete",
		"/api/v1/drafts",
		summary="Discard an unsubmitted grievance draft",
		tag="Grievance Drafts",
		security=[{"BearerAuth": []}],
		request="DiscardDraftRequest",
		response="DraftDiscardResponse",
		legacy="oan_grievance_service.api.v1.draft.discard",
		description="Deletes an unsubmitted draft and purges its temporary uploaded files.",
	),
	# Domain 4: Grievances Core
	R(
		"get",
		"/api/v1/grievances",
		summary="List grievances with filtering, pagination, and sorting",
		tag="Grievances Core",
		security=[{"BearerAuth": []}],
		query=QP["ListGrievances"],
		response="GrievanceListResponse",
		legacy="oan_grievance_service.api.v1.grievance.list_grievances",
		description=(
			"Queries grievances scoped to the user's role and geographic permissions. Supports multi-select "
			+ "filtering on status, category, region, date range, and free-text search."
		),
	),
	R(
		"get",
		"/api/v1/grievances/summary",
		summary="KPI cards summarising grievance status",
		tag="Grievances Core",
		security=[{"BearerAuth": []}],
		response="GrievanceStatusSummaryResponse",
		legacy="oan_grievance_service.api.v1.grievance.summary",
		description="Counts visible grievances on All, In Progress, Require More Info, Rejected, Resolved and Closed. Draft is excluded. Other workflow states roll up into In Progress. Each card includes display order and whether it is terminal.",
	),
	R(
		"get",
		"/api/v1/grievances/options",
		summary="Get grievance management options and dropdowns",
		tag="Grievances Core",
		security=[{"BearerAuth": []}],
		response="GrievanceOptionsResponse",
		legacy="oan_grievance_service.api.v1.grievance.options",
		description="Returns management dropdown options and active staff officers for case triage and filtering.",
	),
	R(
		"get",
		"/api/v1/grievances/{ticket_number}",
		summary="Get grievance details by ticket number",
		tag="Grievances Core",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(example="ET14IN000012026"),
				"description": "Unique alphanumeric ticket number",
			}
		],
		response="GrievanceDetailResponse",
		legacy="oan_grievance_service.api.v1.grievance.track",
		description="Fetches full details, submission snapshot, assigned officer, and SLA targets for a grievance.",
	),
	# Domain 5: Grievance Lifecycle & Actions
	R(
		"post",
		"/api/v1/grievances/{ticket_number}/action",
		summary="Execute a workflow action on a grievance",
		tag="Grievance Lifecycle & Actions",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Ticket number",
			}
		],
		request="GrievanceActionRequest",
		response="GrievanceActionResultResponse",
		legacy="oan_grievance_service.api.v1.grievance.action",
		description="Executes a workflow state transition or action on a grievance, dynamically validating user permissions and allowed moves.",
	),
	R(
		"post",
		"/api/v1/grievances/{ticket_number}/message",
		summary="Post a public message to the conversation",
		tag="Grievance Lifecycle & Actions",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Ticket number",
			}
		],
		request="PostMessageRequest",
		response="GrievanceActionResultResponse",
		legacy="oan_grievance_service.api.v1.grievance.message",
		description="Appends a public message to the grievance conversation visible to both citizens and staff.",
	),
	R(
		"post",
		"/api/v1/grievances/{ticket_number}/note",
		summary="Add internal or public note (staff only)",
		tag="Grievance Lifecycle & Actions",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Ticket number",
			}
		],
		request="AddNoteRequest",
		response="GrievanceActionResultResponse",
		legacy="oan_grievance_service.api.v1.grievance.add_note",
		description="Records an internal work note or communication entry on the grievance case (staff only).",
	),
	R(
		"get",
		"/api/v1/grievances/{ticket_number}/timeline",
		summary="Get grievance timeline and thread details",
		tag="Grievance Lifecycle & Actions",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Ticket number",
			}
		],
		response="GrievanceTimelineResponse",
		legacy="oan_grievance_service.api.v1.grievance.timeline",
		description="Retrieves the complete chronological audit log, state transitions, and conversation thread.",
	),
	# Domain: Attachments
	R(
		"post",
		"/api/v1/grievances/{ticket_number}/attachments",
		summary="Upload supporting documents against a grievance",
		tag="Attachments",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Grievance ticket number or document identifier",
			}
		],
		request="SubmitDocumentsRequest",
		request_content_type="multipart/form-data",
		response="AttachmentUploadResponse",
		legacy="oan_grievance_service.api.v1.attachment.submit_documents",
		description="Upload one or more supporting documents against a grievance in multipart/form-data.",
	),
	R(
		"get",
		"/api/v1/grievances/{ticket_number}/attachments",
		summary="List attachments for a grievance",
		tag="Attachments",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "ticket_number",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Grievance ticket number or document identifier",
			}
		],
		response="AttachmentListResponse",
		legacy="oan_grievance_service.api.v1.attachment.get_attachments",
		description="List the evidence attachments on a case with scan verdicts.",
	),
	R(
		"get",
		"/api/v1/attachments/{attachment_id}/download",
		summary="Get attachment download URL",
		tag="Attachments",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "attachment_id",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Unique attachment record ID",
			}
		],
		response="AttachmentDownloadResponse",
		legacy="oan_grievance_service.api.v1.attachment.download",
		description="Hand back one attachment's URL once it has been scanned clean.",
	),
	R(
		"get",
		"/api/v1/attachments/{attachment_id}/view",
		summary="Stream a clean attachment inline",
		tag="Attachments",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "attachment_id",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Unique attachment record ID",
			}
		],
		query=[
			{
				"name": "download",
				"in": "query",
				"required": False,
				"schema": B(default=False),
				"description": "Send Content-Disposition: attachment (Save As) instead of inline",
			}
		],
		response=None,
		response_content_type="*/*",
		legacy="oan_grievance_service.api.v1.attachment.view",
		description=VIEW_ATTACHMENT_DESCRIPTION,
	),
	R(
		"delete",
		"/api/v1/attachments/{attachment_id}",
		summary="Delete an attachment from an open case",
		tag="Attachments",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "attachment_id",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Unique attachment record ID",
			}
		],
		response="DeleteAttachmentResponse",
		legacy="oan_grievance_service.api.v1.attachment.delete",
		description="Remove an attachment added by mistake, permitted only while the case is open.",
	),
	# Domain 8: Administration — category assignments (design §3.8 routing rules)
	R(
		"get",
		"/api/v1/category-assignments",
		summary="List category assignments",
		tag="Administration",
		security=[{"BearerAuth": []}],
		query=QP["ListCategoryAssignments"],
		response="CategoryAssignmentListResponse",
		legacy="oan_grievance_service.api.v1.category_assignment.list_assignments",
		description=(
			"Admin list of category-to-department routing rules. One record per department and service category, "
			+ "with the L1 and L2 officers, SLA window, and escalation flag."
		),
	),
	R(
		"post",
		"/api/v1/category-assignments",
		summary="Create a category assignment",
		tag="Administration",
		security=[{"BearerAuth": []}],
		request="CreateCategoryAssignmentRequest",
		response="CategoryAssignmentResponse",
		status=200,
		legacy="oan_grievance_service.api.v1.category_assignment.create_assignment",
		description=(
			"Create the routing rule for one department and service category. Creates the routing desk "
			+ "and sets the category SLA configuration. A second rule for the same pair is rejected."
			+ SLA_SHARED_NOTE
		),
	),
	R(
		"get",
		"/api/v1/category-assignments/{assignment}",
		summary="Get a category assignment",
		tag="Administration",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "assignment",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Grievance RBAC Assignment id, for example GR-RBAC-00001",
			}
		],
		response="CategoryAssignmentResponse",
		legacy="oan_grievance_service.api.v1.category_assignment.get_assignment",
		description="Fetch one category routing rule.",
	),
	R(
		"patch",
		"/api/v1/category-assignments/{assignment}",
		summary="Update a category assignment",
		tag="Administration",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "assignment",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Grievance RBAC Assignment id, for example GR-RBAC-00001",
			}
		],
		request="UpdateCategoryAssignmentRequest",
		response="CategoryAssignmentResponse",
		legacy="oan_grievance_service.api.v1.category_assignment.update_assignment",
		description=(
			"Change department, officers, SLA window, or flags. "
			+ "service_category cannot be changed and is rejected."
			+ SLA_SHARED_NOTE
		),
	),
	R(
		"delete",
		"/api/v1/category-assignments/{assignment}",
		summary="Deactivate a category assignment",
		tag="Administration",
		security=[{"BearerAuth": []}],
		path_params=[
			{
				"name": "assignment",
				"in": "path",
				"required": True,
				"schema": S(),
				"description": "Grievance RBAC Assignment id, for example GR-RBAC-00001",
			}
		],
		response="CategoryAssignmentResponse",
		legacy="oan_grievance_service.api.v1.category_assignment.deactivate_assignment",
		description=(
			"Retire the routing rule. The record is kept and marked inactive. The category's SLA row "
			+ "is not changed. Same as PATCH with active false. Repeating the call leaves the rule inactive."
		),
	),
	# Domain 9: Response templates (design 3.9)
	R(
		"get",
		"/api/v1/response-templates",
		summary="List response templates",
		tag="Response Templates",
		security=[{"BearerAuth": []}],
		query=QP["ListResponseTemplates"],
		response="ResponseTemplateListResponse",
		legacy="oan_grievance_service.api.v1.response_template.list_templates",
		description=(
			"Admin list of response templates, newest edit first, filterable by category, subcategory, "
			+ "response type, active flag, and title. Each row carries its version, use count, and "
			+ "last-used time. History is on the single-template route."
		),
	),
	R(
		"post",
		"/api/v1/response-templates",
		summary="Create a response template",
		tag="Response Templates",
		security=[{"BearerAuth": []}],
		request="CreateResponseTemplateRequest",
		response="ResponseTemplateResponse",
		status=200,
		legacy="oan_grievance_service.api.v1.response_template.create_template",
		description=(
			"Create a template at version 1. The subcategory is optional and must belong to the "
			+ "category. Wording may use {{ name }} placeholders; a malformed placeholder is rejected."
		),
	),
	R(
		"get",
		"/api/v1/response-templates/{template}",
		summary="Get a response template with its history",
		tag="Response Templates",
		security=[{"BearerAuth": []}],
		path_params=TEMPLATE_PATH_PARAMS,
		response="ResponseTemplateResponse",
		legacy="oan_grievance_service.api.v1.response_template.get_template",
		description="One template and every earlier version of its wording, newest first.",
	),
	R(
		"patch",
		"/api/v1/response-templates/{template}",
		summary="Update a response template",
		tag="Response Templates",
		security=[{"BearerAuth": []}],
		path_params=TEMPLATE_PATH_PARAMS,
		request="UpdateResponseTemplateRequest",
		response="ResponseTemplateResponse",
		legacy="oan_grievance_service.api.v1.response_template.update_template",
		description=(
			"Edit a template. A change to the title, response type, category, subcategory, or either "
			+ "body text copies the old wording to history and raises the version by one. A request that "
			+ "changes nothing, or only is_active, creates no version. Send expected_version to refuse "
			+ "overwriting an edit you have not seen."
		),
	),
	R(
		"delete",
		"/api/v1/response-templates/{template}",
		summary="Delete or deactivate a response template",
		tag="Response Templates",
		security=[{"BearerAuth": []}],
		path_params=TEMPLATE_PATH_PARAMS,
		response="ResponseTemplateDeleteResponse",
		legacy="oan_grievance_service.api.v1.response_template.delete_template",
		description=(
			"A template never used in a response is deleted with its history (deleted true). A used "
			+ "template is kept as evidence and deactivated instead (deleted false)."
		),
	),
]


# ---------------------------------------------------------------------------
# Build Document
# ---------------------------------------------------------------------------
def build_openapi():
	paths = {}

	for r in ROUTES:
		p = r["path"]
		m = r["method"]

		if p not in paths:
			paths[p] = {}

		parameters = []
		if r["path_params"]:
			parameters.extend(r["path_params"])
		if r["query"]:
			parameters.extend(r["query"])

		op = {
			"tags": [r["tag"]],
			"summary": r["summary"],
			"description": r["description"],
			"operationId": f"{m}_{p.strip('/').replace('/', '_').replace('-', '_').replace('{', '').replace('}', '')}",
			"responses": {
				str(r["status"]): {
					"description": "Success",
					"content": {
						r["response_content_type"]: {
							# A route with no envelope streams the raw bytes of a file.
							"schema": REF(r["response"]) if r["response"] else BINARY
						}
					},
				},
				"400": {
					"description": "Validation or Bad Input Error",
					"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
				},
				"401": {
					"description": "Unauthorized / Authentication Required",
					"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
				},
				"403": {
					"description": "Forbidden / Insufficient Role Scope",
					"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
				},
				"404": {
					"description": "Resource Not Found",
					"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
				},
				"500": {
					"description": "Internal Server Error",
					"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
				},
			},
		}

		if parameters:
			op["parameters"] = parameters

		if r["legacy"]:
			op["x-legacy-rpc-method"] = r["legacy"]

		if r["security"] is not None:
			op["security"] = r["security"]

		if r["request"]:
			ct = r.get("request_content_type", "application/json")
			op["requestBody"] = {
				"required": True,
				"content": {ct: {"schema": REF(r["request"])}},
			}

		paths[p][m] = op

	components_schemas = {}
	components_schemas.update(DATA_SCHEMAS)
	components_schemas.update(REQ)
	components_schemas.update(ENVELOPES)

	doc = {
		"openapi": "3.0.3",
		"info": {
			"title": "OAN Grievance Service API",
			"version": "1.0.0",
			"description": (
				"Grievance management and citizen feedback service for OpenAgriNet (OAN). "
				+ "Provides RESTful endpoints for submitting complaints, tracking resolution progress, "
				+ "cascading administrative area drill-downs, citizen-officer timeline messaging, "
				+ "escalation management, and case resolution workflows."
			),
			"contact": {"name": "COSS - Centre for Open Societal Systems"},
		},
		"servers": [
			{"url": "http://localhost:8000", "description": "Local Frappe Bench"},
			{"url": "https://grievance.openagrinet.org", "description": "Production Grievance Gateway"},
		],
		"tags": [
			{"name": "Health & Monitoring", "description": "Service health probes and uptime pings"},
			{
				"name": "Submitter Management",
				"description": "Intake reference options and submitter profiles",
			},
			{
				"name": "Administrative Areas",
				"description": "Cascading geographic drill-downs, breadcrumbs, and search",
			},
			{
				"name": "Grievance Drafts",
				"description": "Draft grievance persistence, resume, submit, and discard",
			},
			{"name": "Grievances Core", "description": "Case intake, tracking, and filtered list views"},
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
				"description": "Category-to-department routing rules for the Category Assignments admin tab",
			},
			{
				"name": "Response Templates",
				"description": "Versioned officer response templates for the Administration console",
			},
		],
		"paths": paths,
		"components": {
			"securitySchemes": {
				"BearerAuth": {
					"type": "http",
					"scheme": "bearer",
					"bearerFormat": "JWT",
					"description": "Provide JWT access token as `Bearer <token>` in the Authorization header.",
				}
			},
			"schemas": components_schemas,
		},
	}
	return doc, paths, components_schemas


def strip_extensions(o):
	if isinstance(o, dict):
		return {k: strip_extensions(v) for k, v in o.items() if not k.startswith("x-")}
	if isinstance(o, list):
		return [strip_extensions(v) for v in o]
	return o


def main():
	doc, paths, components_schemas = build_openapi()

	# 1. Write internal spec
	with open(INTERNAL_SPEC_OUTPUT, "w") as f:  # nosemgrep: frappe-security-file-traversal
		f.write("# OAN Grievance Service API -- OpenAPI 3.0.3 (INTERNAL)\n")
		f.write("# Carries internal vendor extensions (x-legacy-rpc-method).\n")
		f.write("# Generated from generate_openapi_spec.py -- do not edit manually.\n")
		yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True)

	n_paths = len(paths)
	n_ops = sum(len(v) for v in paths.values())
	print(
		f"Wrote {INTERNAL_SPEC_OUTPUT.name}: {n_paths} paths, {n_ops} operations, {len(components_schemas)} schemas",
		file=sys.stderr,
	)

	# 2. Write public spec (vendor extensions stripped)
	public_doc = strip_extensions(doc)
	with open(PUBLIC_SPEC_OUTPUT, "w") as f:  # nosemgrep: frappe-security-file-traversal
		f.write("# OAN Grievance Service API -- OpenAPI 3.0.3 (PUBLIC)\n")
		f.write("# Contract with vendor extensions removed. Generated from generate_openapi_spec.py.\n")
		yaml.safe_dump(
			public_doc, f, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
		)

	print(
		f"Wrote {PUBLIC_SPEC_OUTPUT.name}: {n_paths} paths, {n_ops} operations, {len(components_schemas)} schemas",
		file=sys.stderr,
	)


if __name__ == "__main__":
	main()
