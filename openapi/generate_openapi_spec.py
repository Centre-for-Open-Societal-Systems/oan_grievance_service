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


def REF(name):
	return {"$ref": f"#/components/schemas/{name}"}


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
		},
		required=["page", "page_size", "total_count", "total_pages"],
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
			"ticket_number": S(example="ET14IN000012026"),
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
			"ticket_number": S(example="ET14IN000012026"),
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
			"statuses": ARR(OBJ({"status": S(), "label": S(), "is_open": I(), "is_terminal": I()})),
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


# ---------------------------------------------------------------------------
# Request Body Schemas
# ---------------------------------------------------------------------------
REQ = {}

REQ["SubmitGrievanceRequest"] = OBJ(
	{
		"grievance_type": S(description="Name or ID of Grievance Type e.g. 'Fertilizer Shortage'"),
		"description": S(
			minLength=20,
			description="Detailed narrative of the citizen grievance (minimum 20 characters)",
		),
		"submission_channel": S(
			example="Mobile App",
			enum=["Mobile App", "Web Portal", "Mobile Call", "IVR Helpline", "Development Agent Assisted"],
			description="Channel through which the case is filed",
		),
		"administrative_area": S(
			example="kebele-ET140108101008",
			description="Canonical area ID, code, or path_code of the incident location",
		),
		"service_category": S(nullable=True, description="Service Category name e.g. 'Inputs'"),
		"administrative_unit": S(nullable=True, description="Specific local landmark or village"),
		"preferred_language": S(
			example="en", nullable=True, description="Preferred language code ('am', 'en')"
		),
		"is_anonymous": I(enum=[0, 1], default=0, description="1 to request anonymity under FSD 9.2"),
		"client_submission_uuid": S(nullable=True, description="Idempotency submission UUID"),
		"submitter": S(nullable=True, description="Staff-assisted filing: existing submitter profile ID"),
		"submitter_name": S(nullable=True, description="Staff-assisted filing: submitter citizen name"),
		"contact_mobile": S(nullable=True, description="Staff-assisted filing: citizen mobile phone"),
		"contact_email": S(format="email", nullable=True, description="Staff-assisted filing: citizen email"),
	},
	required=["grievance_type", "description", "submission_channel", "administrative_area"],
	additionalProperties=True,
	description="Payload for lodging a new grievance ticket",
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


# ---------------------------------------------------------------------------
# Envelope Builder Helper
# ---------------------------------------------------------------------------
def make_envelope(data_ref, is_list=False, description="Successful response"):
	data_prop = ARR(REF(data_ref)) if is_list else REF(data_ref)
	return OBJ(
		{
			"status": S(example="success", enum=["success"]),
			"message": S(nullable=True, description="Optional response message"),
			"data": data_prop,
			"meta": REF("ApiMeta"),
			"request_id": S(format="uuid", nullable=True, description="Tracing correlation ID"),
		},
		required=["status", "data"],
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
	"GrievanceTimelineResponse": make_envelope(
		"GrievanceTimelineData", description="Grievance timeline response"
	),
	"GrievanceActionResultResponse": make_envelope(
		"GrievanceActionResultData", description="Action result response"
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
			"description": "Comma-separated status filters e.g. 'Submitted,Under Investigation'",
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
	query=None,
	response=None,
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
		query=query or [],
		response=response,
		path_params=path_params or [],
		legacy=legacy,
		status=status,
		description=description,
	)


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
	# Domain 4: Grievances Core
	R(
		"post",
		"/api/v1/grievances",
		summary="Submit a new grievance",
		tag="Grievances Core",
		security=[{"BearerAuth": []}],
		request="SubmitGrievanceRequest",
		response="GrievanceSubmitResultResponse",
		legacy="oan_grievance_service.api.v1.grievance.submit",
		description=(
			"FSD 4.1: Lodges a citizen grievance on any valid channel (mobile, web, call, IVR, assisted). "
			+ "Derives geographic snapshot, assigns ticket prefix and sequence, triggers acknowledgement, "
			+ "and routes the case to an administrative area / category queue."
		),
	),
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
					"content": {"application/json": {"schema": REF(r["response"])}},
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
			op["requestBody"] = {
				"required": True,
				"content": {"application/json": {"schema": REF(r["request"])}},
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
			{"name": "Grievances Core", "description": "Case intake, tracking, and filtered list views"},
			{
				"name": "Grievance Lifecycle & Actions",
				"description": "Communication threads, notes, reopen, reject, escalate, and resolution confirmation",
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
	with open(INTERNAL_SPEC_OUTPUT, "w") as f:
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
	with open(PUBLIC_SPEC_OUTPUT, "w") as f:
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
