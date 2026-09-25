"""FR-02 submission and FR-06 submitter actions, exposed for the mobile app, web
portal, IVR and call centre channels described in FSD 3.2.1.

Every entry point is whitelisted, validates its own input, and routes through the
service layer so the audit trail and notifications cannot be bypassed.
"""

import math

import frappe
from frappe import _
from frappe.utils import now_datetime, validate_phone_number_with_country_code
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	SafeEmail,
	handle_api_errors,
	parse_multi_value,
	require_role,
	success_response,
	validate_request,
)
from pydantic import BaseModel, ConfigDict, Field

from oan_grievance_service.api.v1._options import (
	active_channels,
	expand_status_filter,
	get_departments,
	get_grievance_types,
	get_service_categories,
	get_status_options,
	get_status_summary,
	public_status,
)
from oan_grievance_service.grievance_management.doctype.grievance.grievance import (
	GrievanceSubmissionPayload,
)
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import audit, identity, lifecycle, routing, sla
from oan_grievance_service.services import constants as C

# Aliased: several entry points take a `ticket_number` argument, which would
# otherwise shadow the module inside them.
from oan_grievance_service.services import ticket_number as tn

route = prefixed("/api/v1/grievances")


class SubmitGrievanceRequest(GrievanceSubmissionPayload):
	"""Request body for the one-step submission endpoint."""

	model_config = ConfigDict(extra="allow")

	submitter_type: str | None = None
	submitter_name: str | None = None
	contact_email: SafeEmail | None = None
	submission_channel: str | None = None
	administrative_area: str | None = None
	administrative_unit: str | None = None
	service_category: str | None = None
	grievance_type: str | None = None
	associated_service_provider: str | None = None
	desired_outcome: str | None = None
	is_anonymous: int | bool = 0
	consent_given: int | bool = 1
	anonymity_justification: str | None = None
	client_submission_uuid: str | None = None
	client_uuid: str | None = None


class ListGrievancesRequest(BaseModel):
	model_config = {"extra": "allow"}

	page: int = Field(1, ge=1)
	page_size: int = Field(20, ge=1, le=100)
	limit: int | None = Field(None, ge=1, le=100)
	status: str | list | None = None
	service_category: str | list | None = None
	category: str | list | None = None
	grievance_type: str | list | None = None
	type: str | list | None = None
	assigned_dept: str | list | None = None
	department: str | list | None = None
	dept: str | list | None = None
	assigned_to: str | None = None
	administrative_area: str | list | None = None
	location: str | list | None = None
	region: str | list | None = None
	zone: str | list | None = None
	woreda: str | list | None = None
	kebele: str | list | None = None
	administrative_unit: str | list | None = None
	submission_channel: str | list | None = None
	channel: str | list | None = None
	escalated: bool | str | int | None = None
	is_escalated: bool | str | int | None = None
	is_anonymous: bool | str | int | None = None
	submitter: str | None = None
	from_date: str | None = None
	to_date: str | None = None
	search: str | None = None
	sort_by: str = "creation"
	sort_order: str = "desc"


class GrievanceActionRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	action: str = Field(..., min_length=1)
	reason: str | None = None
	note: str | None = None
	rating: int | None = Field(None, ge=1, le=5)
	comments: str | None = None
	body: str | None = None


class AddNoteRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	body: str = Field(..., min_length=1)
	is_internal: bool | str = True


class PostMessageRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	body: str = Field(..., min_length=1)
	type: str | None = Field(
		None,
		description="Message or Response Type: 'note', 'message', 'info_request', 'info_response', or a Response Type ('Resolved', 'Partially Resolved', 'Referred to another dept', 'Requires further info')",
	)
	action_taken: str | None = Field(
		None, max_length=500, description="Short summary of action taken (for formal department responses)"
	)
	proposed_close_date: str | None = Field(
		None, description="Target closure date (YYYY-MM-DD) for formal department responses"
	)
	referred_to_department: str | None = Field(None, description="Target department if type is a referral")
	is_internal: bool | str | None = Field(
		None, description="Whether this note is hidden from citizens (staff-only)"
	)


class ReassignGrievanceRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	target_department: str = Field(..., min_length=1)
	target_officer: str | None = None
	reason: str | None = None


class DeferSLARequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	additional_days: int = Field(..., ge=1)
	reason: str = Field(..., min_length=1)


class AnonymityDecisionRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	decision: str = Field(..., min_length=1)
	reason: str | None = None


class AnonymityChoiceRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str | None = None
	choice: str = Field(..., min_length=1)


ALLOWED_GRIEVANCE_ROLES = C.ALLOWED_GRIEVANCE_ROLES
STAFF_ROLES = C.STAFF_ROLES

# Columns the server derives on submission. Accepting any of these from the caller
# would let a client forge case ownership -- permissions.py scopes read and write
# access on `submitter` and `assisted_by_officer` -- or rewrite the filing-time
# area snapshot that Grievance.set_administrative_area_metadata is meant to own.
CLIENT_IMMUTABLE_FIELDS = frozenset(
	{
		"submitter",
		"submitter_name",
		"contact_mobile",
		"contact_email",
		"assisted_by_officer",
		"area_lft",
		"area_path_code",
		"status",
	}
)


def _resolve_submitter_identity(kwargs):
	"""Decide who a grievance belongs to, server-side, and snapshot their profile.

	A submitter always files as themselves: the session decides the owning profile,
	never the request. Staff may file on someone's behalf for the assisted and call
	centre channels, in which case the named submitter stays the owner and the staff
	member is recorded as the assisting officer for the audit trail.

	The name and contact columns are a filing-time copy of the profile, so a case
	keeps showing who reported it even after they later change their phone number.
	Staff taking a walk-in or IVR report from someone with no profile yet supply
	those details directly, because there is nothing to copy from.
	"""
	user = frappe.session.user
	is_staff = bool(set(frappe.get_roles(user)) & STAFF_ROLES)

	if is_staff:
		profile_name = kwargs.get("submitter")
		identity = {"submitter": profile_name, "assisted_by_officer": user}
	else:
		profile_name = frappe.db.get_value("Grievance Submitter Profile", {"user": user}, "name")
		if not profile_name:
			frappe.throw(
				_("No submitter profile associated with your user account."),
				title=_("Profile Not Found"),
			)
		identity = {"submitter": profile_name, "assisted_by_officer": None}

	if not profile_name:
		identity.update(
			{
				"submitter_name": kwargs.get("submitter_name"),
				"contact_mobile": kwargs.get("contact_mobile"),
				"contact_email": kwargs.get("contact_email"),
			}
		)
		return identity

	profile = frappe.db.get_value(
		"Grievance Submitter Profile",
		profile_name,
		["submitter_type", "submitter_name", "contact_mobile", "contact_email", "active", "is_blocked"],
		as_dict=True,
	)
	if not profile:
		frappe.throw(_("Unknown submitter profile."), title=_("Invalid Submitter"))

	# The schema records is_blocked as blocking new submissions while leaving existing
	# cases visible, so it is enforced here rather than in the permission layer.
	if profile.is_blocked or not profile.active:
		frappe.throw(
			_("This submitter profile cannot file new grievances."),
			title=_("Submitter Blocked"),
		)

	identity.update(
		{
			"submitter_type": profile.submitter_type,
			"submitter_name": profile.submitter_name,
			"contact_mobile": profile.contact_mobile,
			"contact_email": profile.contact_email,
		}
	)
	return identity


def resolve_administrative_area(area_identifier):
	"""Resolve an area identifier (ID, path_code, or unique code) to canonical doc name.

	Note: area_name is intentionally excluded for lower-level tiers because display names
	recur across regions/woredas (e.g. over 100 kebeles named '1' or '2'). For Region tier,
	display names are unique across the country and safe to match.
	"""
	if not area_identifier:
		return None
	if frappe.db.exists("Grievance Administrative Area", area_identifier):
		return area_identifier
	return (
		frappe.db.get_value("Grievance Administrative Area", {"path_code": area_identifier}, "name")
		or frappe.db.get_value("Grievance Administrative Area", {"code": area_identifier}, "name")
		or frappe.db.get_value(
			"Grievance Administrative Area", {"area_name": area_identifier, "level_name": "Region"}, "name"
		)
	)


def resolve_grievance_type(type_identifier: str | None, category: str | None = None) -> str | None:
	"""Resolve a grievance type identifier (DocType name or display type_name) to canonical doc name.

	None when nothing matches. The caller decides whether that is an error; handing
	back the raw input instead would let an unknown string reach a Link field.
	"""
	if not type_identifier:
		return None
	type_identifier = str(type_identifier).strip()
	if frappe.db.exists("Grievance Type", type_identifier):
		return type_identifier
	filters = {"type_name": type_identifier}
	if category:
		filters["service_category"] = category
	resolved = frappe.db.get_value("Grievance Type", filters, "name")
	if resolved:
		return resolved
	return frappe.db.get_value("Grievance Type", {"type_name": type_identifier}, "name")


def _request_anonymity(doc, justification):
	"""FSD 9.2: anonymity is requested at submission and approved separately."""
	frappe.get_doc(
		{
			"doctype": "Grievance Anonymity Request",
			"grievance": doc.name,
			# The request and the grievance use different vocabularies: the request
			# is "Pending", the flag it drives on the grievance is "Pending Approval".
			"status": "Pending",
			"requested_at": now_datetime(),
			"justification": justification,
		}
	).insert(ignore_permissions=True)
	doc.db_set("anonymity_status", "Pending Approval", update_modified=False)


def detect_duplicates(grievance, window_days=7):
	"""FSD 3.2.3 / E3: match on submitter identity, grievance type and time proximity."""
	if not grievance.submitter:
		return []

	candidates = frappe.get_all(
		"Grievance",
		filters={
			"name": ["!=", grievance.name],
			"submitter": grievance.submitter,
			"grievance_type": grievance.grievance_type,
			"creation": [">=", frappe.utils.add_days(now_datetime(), -window_days)],
			"status": ["!=", "Draft"],
			"workflow_state": ["!=", "Draft"],
		},
		pluck="name",
	)

	rows = []
	for candidate in candidates:
		rows.append(
			frappe.get_doc(
				{
					"doctype": "Grievance Duplicate",
					"grievance": grievance.name,
					"duplicate_of": candidate,
					"detected_at": now_datetime(),
					"detection_method": "Identity + Type + Time Proximity",
					"similarity_score": 1.0,
				}
			).insert(ignore_permissions=True)
		)
	return rows


def _resolve_area_filter_identifiers(identifier: str, level_hint: str | None = None) -> list[str]:
	"""Resolve an area identifier, path_code, code, or area_name to matching canonical doc names."""
	if not identifier:
		return []
	identifier = str(identifier).strip()
	if frappe.db.exists("Grievance Administrative Area", identifier):
		return [identifier]

	by_path = frappe.get_all("Grievance Administrative Area", filters={"path_code": identifier}, pluck="name")
	if by_path:
		return by_path

	by_code = frappe.get_all("Grievance Administrative Area", filters={"code": identifier}, pluck="name")
	if by_code:
		return by_code

	if level_hint:
		by_level = frappe.get_all(
			"Grievance Administrative Area",
			filters={"area_name": identifier, "level_name": level_hint},
			pluck="name",
		)
		if by_level:
			return by_level

	by_region = frappe.get_all(
		"Grievance Administrative Area",
		filters={"area_name": identifier, "level_name": "Region"},
		pluck="name",
	)
	if by_region:
		return by_region

	by_name = frappe.get_all("Grievance Administrative Area", filters={"area_name": identifier}, pluck="name")
	if by_name:
		return by_name

	frappe.throw(
		_("Administrative area '{0}' could not be resolved.").format(identifier),
		frappe.DoesNotExistError,
		title=_("Invalid Area Filter"),
	)


def _resolve_area_filter_identifier(identifier: str, level_hint: str | None = None) -> str:
	"""Resolve an area identifier, path_code, code, or area_name for filtering (single name)."""
	matches = _resolve_area_filter_identifiers(identifier, level_hint)
	return matches[0] if matches else ""


@route("", methods=("GET",), summary="List grievances with filtering, pagination, and sorting")
@frappe.whitelist()
@validate_request(ListGrievancesRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def list_grievances(
	page: int = 1,
	page_size: int = 20,
	limit: int | None = None,
	status: str | list | None = None,
	service_category: str | list | None = None,
	category: str | list | None = None,
	grievance_type: str | list | None = None,
	type: str | list | None = None,
	assigned_dept: str | list | None = None,
	department: str | list | None = None,
	dept: str | list | None = None,
	assigned_to: str | None = None,
	administrative_area: str | list | None = None,
	location: str | list | None = None,
	region: str | list | None = None,
	zone: str | list | None = None,
	woreda: str | list | None = None,
	kebele: str | list | None = None,
	administrative_unit: str | list | None = None,
	submission_channel: str | list | None = None,
	channel: str | list | None = None,
	escalated: bool | str | int | None = None,
	is_escalated: bool | str | int | None = None,
	is_anonymous: bool | str | int | None = None,
	submitter: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	search: str | None = None,
	sort_by: str = "creation",
	sort_order: str = "desc",
	**kwargs,
):
	"""Retrieve paginated and filtered list of grievances.

	Enforces deny-by-default RBAC through permission query conditions:
	- Grievance Submitters only see their own cases and assisted submissions.
	- Grievance Officers only see cases matching their RBAC scope (administrative area subtree,
	  department, category) or directly assigned to them.
	- Grievance Admins and System Managers see all cases.

	Supports multi-select values (list, JSON array, or comma-separated string) for status,
	service_category/category, administrative_area/location/region/zone/woreda/kebele,
	grievance_type, department, and submission_channel.

	Status filters use the queue cards (All, In Progress, Require More Info, Rejected,
	Resolved, Closed; officers also get Assigned). Draft is never returned. Workflow
	stages that are not a card are reported as the card they roll up into.
	"""
	effective_limit = limit if limit is not None else page_size
	offset = (page - 1) * effective_limit

	filters = [
		["workflow_state", "!=", "Draft"],
		["status", "!=", "Draft"],
	]

	status_raw = next((val for val in (status, kwargs.get("status")) if val is not None), None)
	status_vals = parse_multi_value(status_raw)
	if status_vals:
		expanded = expand_status_filter(status_vals)
		if expanded:
			filters.append(["status", "in", expanded])

	MULTI_SELECT_FIELDS = {
		"service_category": [service_category, category, kwargs.get("category")],
		"grievance_type": [grievance_type, type, kwargs.get("type")],
		"assigned_dept": [assigned_dept, department, dept, kwargs.get("dept"), kwargs.get("department")],
		"submission_channel": [submission_channel, channel, kwargs.get("channel")],
	}
	for fieldname, candidates in MULTI_SELECT_FIELDS.items():
		raw = next((val for val in candidates if val is not None), None)
		vals = parse_multi_value(raw)
		if vals:
			filters.append([fieldname, "in", vals])

	if assigned_to:
		target_user = frappe.session.user if assigned_to == "me" else assigned_to
		filters.append(["assigned_to", "=", target_user])

	if submitter:
		if submitter == "me":
			profile_name = frappe.db.get_value(
				"Grievance Submitter Profile", {"user": frappe.session.user}, "name"
			)
			if profile_name:
				filters.append(["submitter", "=", profile_name])
		else:
			filters.append(["submitter", "=", submitter])

	if is_anonymous is not None:
		val = 1 if str(is_anonymous).lower() in ("1", "true", "yes") else 0
		filters.append(["is_anonymous", "=", val])

	esc = is_escalated if is_escalated is not None else escalated
	if esc is not None:
		val = 1 if str(esc).lower() in ("1", "true", "yes") else 0
		filters.append(["escalated", "=", val])

	if from_date:
		filters.append(["creation", ">=", f"{from_date} 00:00:00" if len(from_date) == 10 else from_date])

	if to_date:
		filters.append(["creation", "<=", f"{to_date} 23:59:59" if len(to_date) == 10 else to_date])

	tier_filters = [
		(kebele or kwargs.get("kebele"), "Kebele"),
		(woreda or kwargs.get("woreda"), "Woreda"),
		(zone or kwargs.get("zone"), "Zone"),
		(region or kwargs.get("region"), "Region"),
		(location or kwargs.get("location"), None),
		(administrative_area or kwargs.get("administrative_area"), None),
		(administrative_unit or kwargs.get("administrative_unit"), None),
	]

	active_tier_area_sets = []
	for val, lvl in tier_filters:
		if val is not None:
			raw_items = parse_multi_value(val)
			if not raw_items:
				continue
			tier_area_names = set()
			for item in raw_items:
				canonical_names = _resolve_area_filter_identifiers(item, lvl)
				for canonical in canonical_names:
					bounds = frappe.db.get_value(
						"Grievance Administrative Area",
						canonical,
						["lft", "rgt", "is_group"],
						as_dict=True,
					)
					if bounds and bounds.lft is not None and bounds.rgt is not None:
						if bounds.get("is_group") or (bounds.rgt - bounds.lft > 1):
							descendants = frappe.get_all(
								"Grievance Administrative Area",
								filters=[["lft", ">=", int(bounds.lft)], ["lft", "<=", int(bounds.rgt)]],
								pluck="name",
							)
							tier_area_names.update(descendants)
						else:
							tier_area_names.add(canonical)
					else:
						tier_area_names.add(canonical)
			if tier_area_names:
				active_tier_area_sets.append(tier_area_names)

	if active_tier_area_sets:
		matching_areas = active_tier_area_sets[0]
		for s in active_tier_area_sets[1:]:
			matching_areas = matching_areas.intersection(s)

		if not matching_areas:
			# The area filters exclude each other, so nothing can match: skip the query.
			audit.record_access(audit.ACTION_VIEW_LIST)
			return _grievance_page([], page, effective_limit, total_count=0)
		filters.append(["administrative_area", "in", list(matching_areas)])

	or_filters = []
	if search:
		term = search.strip()
		search_pattern = f"%{term}%"
		matching_types = frappe.get_all(
			"Grievance Type",
			filters={"type_name": ["like", search_pattern]},
			pluck="name",
		)
		or_filters = [
			["ticket_number", "like", search_pattern],
			["name", "like", search_pattern],
			["submitter_name", "like", search_pattern],
		]
		# A ticket is printed grouped and read back over a phone line, but
		# stored flat, so a pasted `B-001-0012-0` matches nothing on the raw
		# pattern alone. Added beside the raw clause rather than replacing it:
		# grievances numbered under FSD 3.2.3 carry real hyphens in the stored
		# value, and cleaning the term would stop those matching.
		ticket_term = tn.clean(term)
		if ticket_term and ticket_term != term:
			or_filters.append(["ticket_number", "like", f"%{ticket_term}%"])
		if matching_types:
			or_filters.append(["grievance_type", "in", matching_types])
		else:
			or_filters.append(["grievance_type", "like", search_pattern])

	allowed_sort_fields = {
		"creation",
		"modified",
		"ticket_number",
		"status",
		"sla_due_date",
		"service_category",
		"grievance_type",
	}
	order_field = sort_by if sort_by in allowed_sort_fields else "creation"
	order_direction = "asc" if str(sort_order).lower() == "asc" else "desc"
	order_by = f"`tabGrievance`.{order_field} {order_direction}"

	fields = [
		"name",
		"ticket_number",
		"status",
		"escalated",
		"submission_channel",
		"submitter",
		"submitter_name",
		"contact_mobile",
		"contact_email",
		"is_anonymous",
		"administrative_area",
		"service_category",
		"grievance_type",
		"description",
		"assigned_dept",
		"assigned_to",
		"sla_due_date",
		"confirmation_deadline",
		"creation as submitted_on",
		"modified as updated_at",
	]

	items = frappe.get_list(
		"Grievance",
		filters=filters,
		or_filters=or_filters if or_filters else None,
		fields=fields,
		order_by=order_by,
		start=offset,
		page_length=effective_limit,
	)

	total_records = frappe.get_list(
		"Grievance",
		filters=filters,
		or_filters=or_filters if or_filters else None,
		fields=[{"COUNT": "*", "as": "total"}],
		limit_page_length=1,
	)
	total_count = int(total_records[0].get("total", 0)) if total_records else 0

	from oan_grievance_service.permissions import UNRESTRICTED_ROLES, _submitter_profiles

	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	is_admin = bool(user_roles & UNRESTRICTED_ROLES)
	user_profiles = set(_submitter_profiles(user)) if user != "Guest" else set()

	from oan_grievance_service.api.v1.administrative_area import (
		format_administrative_location,
		get_administrative_hierarchy,
	)

	area_cache = {}
	unique_areas = {item.get("administrative_area") for item in items if item.get("administrative_area")}
	for area_id in unique_areas:
		h = get_administrative_hierarchy(area_id)
		loc = format_administrative_location(h)
		area_cache[area_id] = {"hierarchy": h, "location": loc}

	for item in items:
		item["escalated"] = bool(item.get("escalated"))
		is_anon = bool(item.get("is_anonymous"))
		item["is_anonymous"] = is_anon
		if is_anon and not is_admin:
			if not (item.get("submitter") and item.get("submitter") in user_profiles):
				item["submitter_name"] = _("Anonymous Submitter")
				item["contact_mobile"] = None
				item["contact_email"] = None
		item["status"] = public_status(item.get("status"))
		item["department"] = item.get("assigned_dept")
		# Grouped for reading, as `timeline` returns it. Stored flat in DB,
		# but exposed formatted to clients as ticket_number.
		item["ticket_number"] = tn.display(item.get("ticket_number"))
		area_info = area_cache.get(item.get("administrative_area"))
		item["location"] = area_info["location"] if area_info else None
		item["administrative_hierarchy"] = area_info["hierarchy"] if area_info else None

	audit.record_access(audit.ACTION_VIEW_LIST)

	return _grievance_page(items, page, effective_limit, total_count)


def _grievance_page(items, page, page_size, total_count):
	"""The list endpoint's envelope, shared by the normal and the short-circuit path."""
	total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
	return success_response(
		data={
			"items": items,
			"pagination": {
				"page": page,
				"page_size": page_size,
				"total_count": total_count,
				"total_pages": total_pages,
				"has_next": page < total_pages,
				"has_prev": page > 1,
			},
		},
		message=_("Grievances retrieved successfully"),
	)


def _get_available_actions_for_user(doc):
	"""List actions available to the current user on this grievance with localized labels."""
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	is_staff = bool(roles & STAFF_ROLES)

	actions = lifecycle.actions_available(doc)
	result = []
	for act in actions:
		if act == "Reject" and not is_staff:
			continue
		if act in ("Assign", "Submit Response"):
			continue
		req_reason = act in ("Reject", "Reopen")
		result.append(
			{
				"action": act,
				"label": _(act),
				"requires_reason": req_reason,
			}
		)

	# If open and not already escalated, check if submitter or staff can escalate
	if doc.status not in ("Closed", "Rejected") and not doc.escalated:
		user_profile = (
			frappe.db.get_value("Grievance Submitter Profile", {"user": user}, "name")
			if not is_staff
			else None
		)
		if is_staff or (doc.submitter and doc.submitter == user_profile):
			result.append(
				{
					"action": "Escalate",
					"label": _("Escalate"),
					"requires_reason": True,
				}
			)

	return result


def _iso(value):
	"""Render a date/datetime field for the wire.

	An unset field is null. Formatting it with `str()` would put the literal
	string "None" on the wire, which a client cannot tell from a real value.
	"""
	if not value:
		return None
	return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _current_state(doc, extra=None):
	"""The `current_state` block every mutating endpoint echoes back.

	One definition, so a field added for one endpoint's client is there for all of
	them and the shape cannot drift between them. `extra` carries the fields only
	one endpoint has a reason to report.
	"""
	state = {
		"status": doc.status,
		"escalated": bool(doc.escalated),
		"assigned_to": doc.assigned_to,
		"department": doc.assigned_dept,
		"updated_at": _iso(doc.modified),
		"available_actions": _get_available_actions_for_user(doc),
	}
	if extra:
		state.update(extra)
	return state


@route("", methods=("POST",), summary="Submit a new grievance")
@frappe.whitelist()
@validate_request(SubmitGrievanceRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def submit(**kwargs):
	"""Submit a grievance in one request using the current draft flow."""
	from oan_grievance_service.api.v1 import draft

	client_submission_uuid = (
		kwargs.get("client_submission_uuid") or kwargs.get("client_uuid") or frappe.generate_hash(length=32)
	)
	fields = (
		"submission_channel",
		"submitter_type",
		"submitter_name",
		"contact_mobile",
		"contact_email",
		"administrative_area",
		"administrative_unit",
		"service_category",
		"grievance_type",
		"associated_service_provider",
		"description",
		"desired_outcome",
		"is_anonymous",
	)
	save_kwargs = {field: kwargs[field] for field in fields if kwargs.get(field) is not None}
	draft.save(client_submission_uuid=client_submission_uuid, **save_kwargs)

	return draft.submit_draft(
		client_submission_uuid=client_submission_uuid,
		consent_given=kwargs.get("consent_given", 1),
		is_anonymous=kwargs.get("is_anonymous", 0),
		anonymity_justification=kwargs.get("anonymity_justification"),
	)


def _format_timeline_event(entry, doc, from_status=None, to_status=None):
	if not entry:
		return None
	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	from oan_grievance_service.permissions import UNRESTRICTED_ROLES, _submitter_profiles

	is_anon = bool(doc.is_anonymous) or getattr(doc, "anonymity_status", None) == "Approved"
	is_admin = bool(user_roles & UNRESTRICTED_ROLES)
	user_profiles = set(_submitter_profiles(user)) if user != "Guest" else set()
	is_owner = (doc.submitter and doc.submitter in user_profiles) or doc.owner == user

	masked_name = (
		_("Anonymous Submitter") if (is_anon and not is_admin and not is_owner) else doc.submitter_name
	)
	author_type = "submitter" if entry.author_submitter else "officer" if entry.author_user else "system"
	if entry.author_submitter:
		author_name = masked_name or entry.author_submitter
		author_role = doc.submitter_type or "Grievance Submitter"
	elif entry.author_user:
		author_name = frappe.db.get_value("User", entry.author_user, "full_name") or entry.author_user
		author_role = "Grievance Officer"
	else:
		author_name = "System"
		author_role = "System"

	return {
		"id": entry.name,
		"entry_type": entry.entry_type,
		"body": entry.body,
		"is_internal": bool(entry.is_internal),
		"author_name": author_name,
		"author_role": author_role,
		"author_type": author_type,
		"from_status": from_status,
		"to_status": to_status or doc.status,
		"created_on": entry.created_on.isoformat()
		if hasattr(entry.created_on, "isoformat")
		else str(entry.created_on),
	}


@route("/<ticket_number>/action", methods=("POST",), summary="Execute a workflow action on a grievance")
@frappe.whitelist()
@validate_request(GrievanceActionRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def action(
	ticket_number: str,
	action: str,
	reason: str | None = None,
	note: str | None = None,
	rating: int | None = None,
	comments: str | None = None,
	body: str | None = None,
	**kwargs,
):
	"""Execute a state-machine workflow action on a grievance.

	Validates role permissions and the current workflow state dynamically.
	"""
	action_name = (action or "").strip()
	if not action_name:
		frappe.throw(_("Action is required."), title=_("Missing Action"))

	# All mutations, including workflow actions, require write permission
	doc = _load(ticket_number, ptype="write")
	from_status = doc.status
	timeline_entry = None

	# 1. Manual Escalation handler
	if action_name.lower() in ("escalate", "manual escalation"):
		if not reason or not reason.strip():
			frappe.throw(_("A reason is required to escalate a grievance."), title=_("Reason Required"))
		user_roles = set(frappe.get_roles(frappe.session.user))
		is_staff = bool(user_roles & STAFF_ROLES)
		sla.manual_escalate(doc, reason.strip(), by_submitter=not is_staff)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="escalation",
			is_internal=False,
			body=reason.strip(),
			author_user=frappe.session.user if is_staff else None,
			author_submitter=doc.submitter if not is_staff else None,
		)
		doc.reload()
		current_state = _current_state(doc)
		return success_response(
			data={
				"ticket_number": tn.display(doc.ticket_number),
				"status": doc.status,
				"escalated": bool(doc.escalated),
				"action": "Escalate",
				"current_state": current_state,
				"timeline_event": _format_timeline_event(timeline_entry, doc, from_status, doc.status),
				"available_actions": current_state["available_actions"],
			},
			message=_("Grievance escalated successfully"),
		)

	# 2. Check legal workflow actions
	allowed_actions = lifecycle.actions_available(doc)
	matching_action = next((a for a in allowed_actions if a.lower() == action_name.lower()), None)

	if not matching_action:
		frappe.throw(
			_("Action '{0}' is not available for this grievance in status '{1}'.").format(
				action_name, doc.status
			),
			frappe.ValidationError,
			title=_("Action Not Permitted"),
		)

	# 3. Action-specific dispatch and reason validation
	if matching_action == "Confirm Resolution":
		if rating is not None:
			doc.db_set("satisfaction_rating", int(rating), update_modified=False)
		if comments:
			doc.db_set("satisfaction_comments", comments, update_modified=False)
		lifecycle.transition(
			doc, "Confirm Resolution", note="Confirmed by submitter", closure_type="confirmed"
		)
		doc.db_set("closure_reason", "Confirmed by submitter", update_modified=False)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="resolution",
			is_internal=False,
			body=comments or "Confirmed resolution",
			author_submitter=doc.submitter,
		)

	elif matching_action == "Reopen":
		if not reason or not reason.strip():
			frappe.throw(_("A reason is required to reopen a grievance."), title=_("Reason Required"))
		lifecycle.transition(doc, "Reopen", reason=reason.strip(), notify=False)
		doc.db_set("reopen_count", (doc.reopen_count or 0) + 1, update_modified=False)
		from oan_grievance_service.services import notifications

		notifications.queue(doc, C.EVENT_REOPENED)
		user_roles = set(frappe.get_roles(frappe.session.user))
		is_staff = bool(user_roles & STAFF_ROLES)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="status_change",
			is_internal=False,
			body=f"Reopened: {reason.strip()}",
			author_user=frappe.session.user if is_staff else None,
			author_submitter=doc.submitter if not is_staff else None,
		)

	elif matching_action == "Reject":
		roles = set(frappe.get_roles(frappe.session.user))
		if not (roles & STAFF_ROLES):
			frappe.throw(_("Only staff can reject grievances."), frappe.PermissionError)
		if not reason or not reason.strip():
			frappe.throw(_("A reason is required to reject a grievance."), title=_("Reason Required"))
		lifecycle.transition(doc, "Reject", reason=reason.strip(), closure_type="rejected")
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="rejection",
			is_internal=False,
			body=reason.strip(),
			author_user=frappe.session.user,
		)

	elif matching_action == "Submitter Reply":
		reply_text = body or reason or note
		if not reply_text or not reply_text.strip():
			frappe.throw(
				_("A response body is required to reply to an information request."),
				title=_("Reply Required"),
			)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="info_response",
			is_internal=False,
			body=reply_text.strip(),
			author_submitter=doc.submitter,
		)
		lifecycle.transition(doc, "Submitter Reply", note="Submitter provided the requested information")
		from oan_grievance_service.services import notifications

		notifications.queue(doc, C.EVENT_SUBMITTER_RESPONDED)

	elif matching_action in ("Request More Info", "More Info Needed"):
		req_text = body or reason or note or "Additional information requested"
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="info_request",
			is_internal=False,
			body=req_text,
			author_user=frappe.session.user,
		)
		lifecycle.transition(doc, matching_action, reason=reason, note=note)

	elif matching_action == "Assign":
		frappe.throw(
			_(
				"Direct 'Assign' action is not permitted on this endpoint. Use the assignment/reassignment API to assign a department and officer."
			),
			frappe.ValidationError,
			title=_("Action Not Permitted"),
		)

	elif matching_action == "Submit Response":
		has_valid_response = frappe.db.exists(
			"Grievance Response",
			{
				"grievance": doc.name,
				"response_type": ["in", ["Resolved", "Partially Resolved"]],
			},
		)
		if not has_valid_response:
			frappe.throw(
				_(
					"Submit Response requires a formal department response with a resolution summary. Please record your response using the message/department-response endpoint."
				),
				frappe.ValidationError,
				title=_("Response Required"),
			)
		lifecycle.transition(doc, matching_action, reason=reason, note=note)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="status_change",
			is_internal=False,
			body=reason or note or f"Status changed to {doc.status}",
			author_user=frappe.session.user,
		)

	elif matching_action == "Start Work":
		if not doc.assigned_dept:
			if doc.assigned_to:
				from oan_grievance_service.permissions import active_scopes

				scopes = active_scopes(doc.assigned_to)
				if scopes and scopes[0].get("department_scope"):
					doc.db_set("assigned_dept", scopes[0].get("department_scope"), update_modified=False)
			if not doc.assigned_dept:
				frappe.throw(
					_("Cannot start work on a grievance without an assigned department."),
					frappe.ValidationError,
					title=_("Department Required"),
				)
		lifecycle.transition(doc, matching_action, reason=reason, note=note)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="status_change",
			is_internal=False,
			body=reason or note or f"Status changed to {doc.status}",
			author_user=frappe.session.user,
		)

	else:
		lifecycle.transition(doc, matching_action, reason=reason, note=note)
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="status_change",
			is_internal=False,
			body=reason or note or f"Status changed to {doc.status}",
			author_user=frappe.session.user,
		)

	doc.reload()
	current_state = _current_state(doc)
	return success_response(
		data={
			"ticket_number": tn.display(doc.ticket_number),
			"status": doc.status,
			"action": matching_action,
			"current_state": current_state,
			"timeline_event": _format_timeline_event(timeline_entry, doc, from_status, doc.status),
			"available_actions": current_state["available_actions"],
		},
		message=_("Grievance updated successfully"),
	)


@route("/<ticket_number>/timeline", methods=("GET",), summary="Get grievance timeline and thread details")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def timeline(
	ticket_number: str,
	is_internal: bool | str | None = None,
	limit: int | str = 20,
	cursor: str | None = None,
):
	"""Retrieve chronological unified conversation, activity timeline, and thread summary for a grievance.

	Submitters only see public entries (is_internal = 0).
	Staff (Officers, Admins) see all entries or can filter by is_internal flag.
	"""
	doc = _load(ticket_number)
	doc.check_permission("read")
	audit.record_access(audit.ACTION_VIEW_DETAIL, grievance=doc.name)

	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	is_staff = bool(roles & STAFF_ROLES)

	filters = {"grievance": doc.name}

	if not is_staff:
		filters["is_internal"] = 0
	elif is_internal is not None:
		filters["is_internal"] = 1 if str(is_internal).lower() in ("1", "true", "yes") else 0

	if cursor:
		filters["created_on"] = ["<", cursor]

	page_limit = max(1, min(int(limit), 100))
	entries = frappe.get_all(
		"Grievance Timeline",
		filters=filters,
		fields=[
			"name",
			"entry_type",
			"is_internal",
			"body",
			"author_user",
			"author_submitter",
			"ref_doctype",
			"ref_docname",
			"created_on",
		],
		order_by="created_on desc, name desc",
		limit=page_limit + 1,
	)

	has_more = len(entries) > page_limit
	if has_more:
		entries = entries[:page_limit]

	next_cursor = entries[-1]["created_on"].isoformat() if (has_more and entries) else None

	from oan_grievance_service.permissions import UNRESTRICTED_ROLES, _submitter_profiles

	is_anon = bool(doc.is_anonymous) or getattr(doc, "anonymity_status", None) == "Approved"
	is_admin = bool(roles & UNRESTRICTED_ROLES)
	user_profiles = set(_submitter_profiles(user)) if user != "Guest" else set()
	is_owner = (doc.submitter and doc.submitter in user_profiles) or doc.owner == user

	masked_name = (
		_("Anonymous Submitter") if (is_anon and not is_admin and not is_owner) else doc.submitter_name
	)
	masked_mobile = None if (is_anon and not is_admin and not is_owner) else doc.contact_mobile
	masked_email = None if (is_anon and not is_admin and not is_owner) else doc.contact_email

	for entry in entries:
		entry["is_internal"] = bool(entry.get("is_internal"))
		if entry["author_submitter"]:
			entry["author_type"] = "submitter"
			entry["author_name"] = masked_name or entry["author_submitter"]
		elif entry["author_user"]:
			entry["author_type"] = "officer"
			entry["author_name"] = (
				frappe.db.get_value("User", entry["author_user"], "full_name") or entry["author_user"]
			)
	# Fetch associated attachments
	file_attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Grievance", "attached_to_name": doc.name},
		fields=["name", "file_name", "file_url", "file_size", "is_private"],
		order_by="creation asc",
	)
	grievance_attachments = frappe.get_all(
		"Grievance Attachment",
		filters={"grievance": doc.name},
		fields=[
			"name",
			"file_name",
			"file_url",
			"size_bytes as file_size",
			"mime_type",
			"document_type",
			"scan_status",
			"timeline_entry",
			"uploaded_by_user",
			"uploaded_by_submitter",
			"creation",
		],
		order_by="creation asc",
		ignore_permissions=True,
	)
	attachments = list(file_attachments) + list(grievance_attachments)

	attachments_by_timeline = {}
	for att in grievance_attachments:
		tl_id = att.get("timeline_entry")
		if tl_id:
			attachments_by_timeline.setdefault(tl_id, []).append(att)

	for entry in entries:
		entry["attachments"] = attachments_by_timeline.get(entry["name"], [])

	from oan_grievance_service.api.v1.administrative_area import (
		format_administrative_location,
		get_administrative_hierarchy,
	)

	area_hierarchy = get_administrative_hierarchy(doc.administrative_area)
	location_str = format_administrative_location(area_hierarchy)

	return success_response(
		data={
			"name": doc.name,
			"ticket_number": tn.display(doc.ticket_number),
			"status": doc.status,
			"escalated": bool(doc.escalated),
			"submitter_name": masked_name,
			"service_category": doc.service_category,
			"grievance_type": doc.grievance_type,
			"administrative_area": doc.administrative_area,
			"administrative_hierarchy": area_hierarchy,
			"location": location_str,
			"summary": {
				"description": doc.description,
				"desired_outcome": doc.desired_outcome,
				"service_category": doc.service_category,
				"grievance_type": doc.grievance_type,
				"administrative_area": doc.administrative_area,
				"administrative_hierarchy": area_hierarchy,
				"location": location_str,
				"administrative_unit": doc.administrative_unit,
				"submission_channel": doc.submission_channel,
			},
			"submitter": {
				"name": masked_name,
				"mobile": masked_mobile,
				"email": masked_email,
				"submitter_type": doc.submitter_type,
				"is_anonymous": bool(doc.is_anonymous),
				"assisted_by_officer": doc.assisted_by_officer,
			},
			"sla": {
				"sla_days": doc.sla_days,
				"sla_start_at": doc.sla_start_at,
				"sla_due_date": doc.sla_due_date,
				"sla_consumed_percent": sla.consumed_percent(doc),
				"next_escalation_at": doc.next_escalation_at,
				"confirmation_deadline": doc.confirmation_deadline,
			},
			"assignment": {
				"department": doc.assigned_dept,
				"assigned_to": doc.assigned_to,
				"routed_automatically": bool(doc.routed_automatically),
			},
			"available_actions": _get_available_actions_for_user(doc),
			"attachments": attachments,
			"timeline": entries,
			"has_more": has_more,
			"next_cursor": next_cursor,
		},
		message=_("Timeline retrieved successfully"),
	)


# Deprecated alias for backwards compatibility: timeline now handles detail and tracking
track = timeline


@route("/<ticket_number>/note", methods=("POST",), summary="Add internal or public note (staff only)")
@frappe.whitelist()
@validate_request(AddNoteRequest)
@handle_api_errors
@require_role(STAFF_ROLES)
def add_note(ticket_number: str, body: str, is_internal: bool | str = True, **kwargs):
	"""Staff-only endpoint to add an internal or public note to the case timeline."""
	# The undecorated core, not the endpoint: calling `message` here would run its
	# `@handle_api_errors` inside this one, and the inner envelope would become this
	# one's `data` -- serving an error as a 200 success with the real status nested.
	return _post_message(ticket_number=ticket_number, body=body, is_internal=is_internal, type="note")


@route(
	"/<ticket_number>/message",
	methods=("POST",),
	summary="Post a communication, note, reply, or department response",
)
@frappe.whitelist()
@validate_request(PostMessageRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def message(
	ticket_number: str,
	body: str,
	type: str | None = None,
	response_type: str | None = None,
	action_taken: str | None = None,
	proposed_close_date: str | None = None,
	referred_to_department: str | None = None,
	is_internal: bool | str | None = None,
	**kwargs,
):
	"""Unified endpoint for posting public messages, citizen replies, internal notes, or department responses."""
	return _post_message(
		ticket_number=ticket_number,
		body=body,
		type=type,
		response_type=response_type,
		action_taken=action_taken,
		proposed_close_date=proposed_close_date,
		referred_to_department=referred_to_department,
		is_internal=is_internal,
	)


def _parse_flag(value):
	"""A boolean from a JSON body or a form field. The string "false" is truthy in
	Python, so a form-encoded flag has to be read, not tested."""
	if value is None:
		return None
	if isinstance(value, str):
		return value.strip().lower() not in ("", "0", "false", "no", "off")
	return bool(value)


def _post_message(
	ticket_number,
	body,
	type=None,
	response_type=None,
	action_taken=None,
	proposed_close_date=None,
	referred_to_department=None,
	is_internal=None,
):
	"""The body of `message` and `add_note`, deliberately undecorated.

	Both endpoints carry `@handle_api_errors`; sharing this rather than one calling
	the other keeps exactly one envelope per request.
	"""
	is_internal = _parse_flag(is_internal)
	doc = _load(ticket_number, ptype="write")
	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	is_staff = bool(user_roles & STAFF_ROLES)

	req_type = (type or "").strip()
	resp_type_name = (response_type or "").strip()

	# Match response_type if passed via type (e.g. "Resolved")
	if not resp_type_name and req_type and frappe.db.exists("Grievance Response Type", req_type):
		resp_type_name = req_type

	# 1. Formal Department Response
	if resp_type_name:
		if not is_staff:
			frappe.throw(
				_("Only staff members can submit a formal department response."), frappe.PermissionError
			)

		if not frappe.db.exists("Grievance Response Type", resp_type_name):
			frappe.throw(
				_("Response Type '{0}' does not exist.").format(resp_type_name), frappe.ValidationError
			)

		resp_type_doc = frappe.get_cached_doc("Grievance Response Type", resp_type_name)
		if resp_type_doc.requires_referred_dept and not referred_to_department:
			frappe.throw(
				_("Destination department is required for response type '{0}'.").format(resp_type_name),
				frappe.ValidationError,
			)

		from frappe.utils import add_days, today

		close_date = proposed_close_date or add_days(today(), 7)

		# Create formal Grievance Response (triggers response_after_insert hook)
		resp_doc = frappe.get_doc(
			{
				"doctype": "Grievance Response",
				"grievance": doc.name,
				"response_type": resp_type_name,
				"action_taken": (action_taken or body)[:500],
				"resolution_summary": body,
				"proposed_close_date": close_date,
				"referred_to_department": referred_to_department,
				"internal_notes": body if is_internal else None,
			}
		).insert(ignore_permissions=True)

		doc.reload()
		current_state = _current_state(doc)

		latest_tl = frappe.get_all(
			"Grievance Timeline",
			filters={
				"grievance": doc.name,
				"ref_doctype": "Grievance Response",
				"ref_docname": resp_doc.name,
			},
			order_by="creation desc",
			limit=1,
		)
		tl_name = latest_tl[0].name if latest_tl else None

		return success_response(
			data={
				"name": tl_name,
				"ticket_number": tn.display(doc.ticket_number),
				"entry_type": "response",
				"response_type": resp_type_name,
				"response_id": resp_doc.name,
				"is_internal": False,
				"author_type": "officer",
				"status": doc.status,
				"current_state": current_state,
				"available_actions": current_state["available_actions"],
			},
			message=_("Department response recorded successfully"),
		)

	# 2. Information Request from Staff
	if req_type.lower() in ("info_request", "request_more_info", "information request"):
		if not is_staff:
			frappe.throw(_("Only staff members can issue an information request."), frappe.PermissionError)

		if "Request More Info" in lifecycle.actions_available(doc):
			lifecycle.transition(doc, "Request More Info", reason=body)

		entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="info_request",
			is_internal=False,
			body=body,
			author_user=user,
		)
		doc.reload()
		current_state = _current_state(doc)
		return success_response(
			data={
				"name": entry.name,
				"ticket_number": tn.display(doc.ticket_number),
				"entry_type": "info_request",
				"is_internal": False,
				"author_type": "officer",
				"created_on": entry.created_on,
				"status": doc.status,
				"current_state": current_state,
				"available_actions": current_state["available_actions"],
			},
			message=_("Information request posted successfully"),
		)

	# 3. Citizen Reply to Information Request
	if req_type.lower() in ("info_response", "submitter_reply", "reply") or (
		not is_staff and doc.status == "More Info Needed"
	):
		if "Submitter Reply" in lifecycle.actions_available(doc):
			lifecycle.transition(doc, "Submitter Reply", note="Submitter replied to information request")
			from oan_grievance_service.services import notifications

			notifications.queue(doc, C.EVENT_SUBMITTER_RESPONDED)

		entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="info_response",
			is_internal=False,
			body=body,
			author_user=user if is_staff else None,
			author_submitter=doc.submitter if not is_staff else None,
		)
		doc.reload()
		current_state = _current_state(doc)
		return success_response(
			data={
				"name": entry.name,
				"ticket_number": tn.display(doc.ticket_number),
				"entry_type": "info_response",
				"is_internal": False,
				"author_type": "officer" if is_staff else "submitter",
				"created_on": entry.created_on,
				"status": doc.status,
				"current_state": current_state,
				"available_actions": current_state["available_actions"],
			},
			message=_("Reply posted successfully"),
		)

	# 4. Internal Note or Public Message
	internal = False
	if is_staff:
		if req_type.lower() in ("note", "internal_note", "internal"):
			internal = True
		elif is_internal is not None:
			internal = is_internal

	entry_type = "note" if internal else "message"
	entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type=entry_type,
		is_internal=internal,
		body=body,
		author_user=user if is_staff else None,
		author_submitter=doc.submitter if not is_staff else None,
	)

	return success_response(
		data={
			"name": entry.name,
			"ticket_number": tn.display(doc.ticket_number),
			"entry_type": entry.entry_type,
			"is_internal": bool(entry.is_internal),
			"author_type": "officer" if is_staff else "submitter",
			"created_on": entry.created_on,
			"status": doc.status,
		},
		message=_("Note added successfully" if internal else "Message posted successfully"),
	)


@route("/<ticket_number>/reassign", methods=("POST",), summary="Reassign grievance department and officer")
@frappe.whitelist()
@validate_request(ReassignGrievanceRequest)
@handle_api_errors
@require_role(STAFF_ROLES)
def reassign(
	ticket_number: str,
	target_department: str,
	target_officer: str | None = None,
	reason: str | None = None,
	**kwargs,
):
	"""Reassign a grievance to a new department and/or officer directly."""
	from oan_grievance_service.permissions import can_approve_reassignment

	doc = _load(ticket_number, ptype="write")
	if not can_approve_reassignment(user=frappe.session.user, request_doc={"initiated_by": doc.assigned_to}):
		frappe.throw(
			_("Only a supervising officer may approve or execute a reassignment."),
			frappe.PermissionError,
			title=_("Approval Not Permitted"),
		)

	if not frappe.db.exists("Grievance Department", target_department):
		frappe.throw(
			_("Department '{0}' does not exist.").format(target_department),
			frappe.ValidationError,
			title=_("Invalid Department"),
		)

	if target_officer and not frappe.db.exists("User", target_officer):
		frappe.throw(
			_("Officer '{0}' does not exist.").format(target_officer),
			frappe.ValidationError,
			title=_("Invalid Officer"),
		)

	from_dept = doc.assigned_dept
	from_officer = doc.assigned_to

	g_updates = {"assigned_dept": target_department}
	if target_officer:
		g_updates["assigned_to"] = target_officer

	doc.db_set(g_updates, update_modified=False)

	timeline_body = f"Reassigned to {target_department}"
	if target_officer:
		timeline_body += f" ({target_officer})"
	if reason and reason.strip():
		timeline_body += f" - Reason: {reason.strip()}"

	# Record audit doc
	req_doc = frappe.get_doc(
		{
			"doctype": "Grievance Reassignment Request",
			"grievance": doc.name,
			"target_department": target_department,
			"target_officer": target_officer,
			"prior_department": from_dept,
			"prior_officer": from_officer,
			"sla_treatment": "Continue",
			"reason": reason or "Reassigned via API",
			"requested_at": now_datetime(),
			"initiated_by": frappe.session.user,
			"decision": "Approved",
			"approver": frappe.session.user,
			"decided_at": now_datetime(),
		}
	).insert(ignore_permissions=True)

	timeline_entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="assignment",
		is_internal=False,
		body=timeline_body,
		author_user=frappe.session.user,
		ref_doctype="Grievance Reassignment Request",
		ref_docname=req_doc.name,
	)

	doc.reload()
	current_state = _current_state(doc)

	return success_response(
		data={
			"ticket_number": tn.display(doc.ticket_number),
			"status": doc.status,
			"assigned_dept": doc.assigned_dept,
			"assigned_to": doc.assigned_to,
			"current_state": current_state,
			"timeline_event": _format_timeline_event(timeline_entry, doc, doc.status, doc.status),
		},
		message=_("Grievance reassigned successfully"),
	)


@route("/<ticket_number>/defer-sla", methods=("POST",), summary="Extend SLA deadline by deferral")
@frappe.whitelist()
@validate_request(DeferSLARequest)
@handle_api_errors
@require_role(STAFF_ROLES)
def defer_sla(
	ticket_number: str,
	additional_days: int,
	reason: str,
	**kwargs,
):
	"""Extend the SLA window for a case directly via API."""
	from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
		max_deferral_days,
	)
	from oan_grievance_service.permissions import can_approve_deferral

	doc = _load(ticket_number, ptype="write")
	if not can_approve_deferral(user=frappe.session.user, assignee=doc.assigned_to):
		frappe.throw(
			_("Only a supervising officer may decide a deferral."),
			frappe.PermissionError,
			title=_("Approval Not Permitted"),
		)

	max_days = max_deferral_days()
	if int(additional_days) > max_days:
		frappe.throw(
			_("A deferral may not exceed {0} days.").format(max_days),
			frappe.ValidationError,
			title=_("Deferral Too Long"),
		)

	# The audit record goes first: if it cannot be written the clock must not move,
	# or the SLA is extended with nothing on file to say who extended it or why.
	def_doc = frappe.get_doc(
		{
			"doctype": "Grievance SLA Deferral",
			"grievance": doc.name,
			"additional_days": int(additional_days),
			"justification": reason.strip(),
			"requested_by": frappe.session.user,
			"requested_at": now_datetime(),
			"status": "Approved",
			"approver": frappe.session.user,
			"decided_at": now_datetime(),
		}
	).insert(ignore_permissions=True)

	sla.extend_for_deferral(doc, int(additional_days))

	timeline_entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="note",
		is_internal=True,
		body=f"SLA deadline extended by {additional_days} days. Reason: {reason.strip()}",
		author_user=frappe.session.user,
		ref_doctype="Grievance SLA Deferral",
		ref_docname=def_doc.name,
	)

	doc.reload()
	current_state = _current_state(doc, {"sla_due_date": _iso(doc.sla_due_date)})

	return success_response(
		data={
			"ticket_number": tn.display(doc.ticket_number),
			"sla_due_date": current_state["sla_due_date"],
			"current_state": current_state,
			"timeline_event": _format_timeline_event(timeline_entry, doc, doc.status, doc.status),
		},
		message=_("SLA deadline extended successfully"),
	)


@route(
	"/<ticket_number>/anonymity-decision", methods=("POST",), summary="Approve or reject anonymity request"
)
@frappe.whitelist()
@validate_request(AnonymityDecisionRequest)
@handle_api_errors
@require_role(STAFF_ROLES)
def anonymity_decision(
	ticket_number: str,
	decision: str,
	reason: str | None = None,
	**kwargs,
):
	"""Staff ruling on a pending anonymity request (FSD 9.2).

	Approval masks the submitter from department officers. A refusal never unmasks
	them: the identity is the submitter's to disclose, so the request moves to
	Disclosure Requested and the submitter chooses, through `anonymity-choice`,
	whether to continue identified or withdraw. Until they choose, it stays masked.
	"""
	from oan_grievance_service.permissions import can_decide_anonymity
	from oan_grievance_service.services import notifications

	doc = _load(ticket_number, ptype="write")
	dec = decision.strip().capitalize()
	if dec not in ("Approved", "Rejected"):
		frappe.throw(_("Decision must be 'Approved' or 'Rejected'."), frappe.ValidationError)
	if not can_decide_anonymity(doc):
		frappe.throw(
			_("An anonymity request cannot be decided by the person who raised it."),
			frappe.PermissionError,
			title=_("Approval Not Permitted"),
		)
	if doc.anonymity_status != "Pending Approval":
		frappe.throw(
			_("There is no anonymity request awaiting a decision on this grievance."),
			frappe.ValidationError,
		)
	reason = (reason or "").strip()
	if dec == "Rejected" and not reason:
		frappe.throw(
			_("A reason is required to decline an anonymity request."),
			frappe.ValidationError,
			title=_("Reason Required"),
		)

	approved = dec == "Approved"
	if approved:
		doc.db_set(
			{
				"anonymity_status": "Approved",
				"is_anonymous": 1,
				"anonymity_approved_by": frappe.session.user,
			},
			update_modified=False,
		)
	else:
		# is_anonymous is left set on purpose: refusing the request is not consent to disclose.
		doc.db_set("anonymity_status", "Disclosure Requested", update_modified=False)

	_settle_anonymity_request(
		doc,
		from_status="Pending",
		to_status="Approved" if approved else "Identity Disclosure Requested",
		note=reason,
	)

	if approved:
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="status_change",
			is_internal=True,
			body="Anonymity request approved" + (f": {reason}" if reason else ""),
			author_user=frappe.session.user,
		)
	else:
		# Public, because the submitter has to act on it.
		timeline_entry = GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="status_change",
			is_internal=False,
			body=(
				f"Anonymity request not approved: {reason}. Your identity has not been shared. "
				"Choose whether to continue with your identity disclosed or withdraw the grievance."
			),
			author_user=frappe.session.user,
		)
		notifications.queue(doc, C.EVENT_ANONYMITY_DISCLOSURE_REQUESTED)

	doc.reload()
	current_state = _current_state(
		doc,
		{"is_anonymous": bool(doc.is_anonymous), "anonymity_status": doc.anonymity_status},
	)

	return success_response(
		data={
			"ticket_number": tn.display(doc.ticket_number),
			"is_anonymous": bool(doc.is_anonymous),
			"anonymity_status": doc.anonymity_status,
			"current_state": current_state,
			"timeline_event": _format_timeline_event(timeline_entry, doc, doc.status, doc.status),
		},
		message=_("Anonymity decision recorded successfully"),
	)


ANONYMITY_CHOICES = ("disclose", "withdraw")


@route(
	"/<ticket_number>/anonymity-choice",
	methods=("POST",),
	summary="Submitter's choice after an anonymity request is declined",
)
@frappe.whitelist()
@validate_request(AnonymityChoiceRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def anonymity_choice(ticket_number: str, choice: str, **kwargs):
	"""The submitter answers a declined anonymity request (FSD 9.2).

	`disclose` continues the grievance with their identity visible to officers;
	`withdraw` closes it as rejected without their identity ever being disclosed.
	Only the submitter can make this choice: disclosing an identity on someone
	else's behalf is exactly what the refusal path exists to prevent.
	"""
	from oan_grievance_service.services import notifications

	doc = _load(ticket_number, ptype="write")
	choice = (choice or "").strip().lower()
	if choice not in ANONYMITY_CHOICES:
		frappe.throw(
			_("Choice must be one of: {0}.").format(", ".join(ANONYMITY_CHOICES)), frappe.ValidationError
		)

	submitter_user = (
		frappe.db.get_value("Grievance Submitter Profile", doc.submitter, "user") if doc.submitter else None
	)
	if not submitter_user or submitter_user != frappe.session.user:
		frappe.throw(
			_("Only the submitter can choose whether to disclose their identity."),
			frappe.PermissionError,
			title=_("Forbidden"),
		)
	if doc.anonymity_status != "Disclosure Requested":
		frappe.throw(
			_("This grievance is not waiting on an anonymity choice."),
			frappe.ValidationError,
		)

	from_status = doc.status
	if choice == "withdraw":
		if "Reject" not in lifecycle.actions_available(doc):
			frappe.throw(
				_("A grievance in status '{0}' cannot be withdrawn.").format(doc.status),
				frappe.ValidationError,
			)
		# Automated: the Workflow gives Reject to officers only, and this move is the
		# system carrying out the submitter's choice, not a submitter taking that action.
		lifecycle.transition(
			doc,
			"Reject",
			reason="Withdrawn by the submitter after anonymity was not approved",
			automated=True,
			closure_type="rejected",
		)
		doc.db_set("anonymity_status", "Rejected", update_modified=False)
		body = "Grievance withdrawn by the submitter; identity not disclosed."
	else:
		doc.db_set({"anonymity_status": "Rejected", "is_anonymous": 0}, update_modified=False)
		body = "Submitter chose to continue with their identity disclosed."

	_settle_anonymity_request(doc, from_status="Identity Disclosure Requested", to_status="Rejected")

	timeline_entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="status_change",
		is_internal=False,
		body=body,
		author_submitter=doc.submitter,
	)
	if choice == "disclose":
		notifications.queue(doc, C.EVENT_SUBMITTER_RESPONDED)

	doc.reload()
	current_state = _current_state(
		doc,
		{"is_anonymous": bool(doc.is_anonymous), "anonymity_status": doc.anonymity_status},
	)
	return success_response(
		data={
			"ticket_number": tn.display(doc.ticket_number),
			"choice": choice,
			"status": doc.status,
			"is_anonymous": bool(doc.is_anonymous),
			"anonymity_status": doc.anonymity_status,
			"current_state": current_state,
			"timeline_event": _format_timeline_event(timeline_entry, doc, from_status, doc.status),
		},
		message=_("Anonymity choice recorded successfully"),
	)


def _settle_anonymity_request(doc, from_status, to_status, note=None):
	"""Move the grievance's open anonymity request on, recording who decided and when."""
	updates = {"status": to_status, "decided_by": frappe.session.user, "decided_at": now_datetime()}
	if note:
		updates["decision_note"] = note
	for name in frappe.get_all(
		"Grievance Anonymity Request",
		filters={"grievance": doc.name, "status": from_status},
		pluck="name",
	):
		frappe.db.set_value("Grievance Anonymity Request", name, updates, update_modified=False)


def _load(ticket_number, ptype="read"):
	"""Fetch a grievance by ticket number as the submitter typed it.

	Normalised first: the number is printed grouped (3-001-002A-0) and read back
	over a phone line, so the hyphens, casing and the O/I/L substitutions the
	alphabet anticipates must not decide whether a farmer can reach their own
	case.
	"""
	normalized = tn.normalize(ticket_number)
	name = frappe.db.get_value("Grievance", {"ticket_number": normalized}, "name")
	if not name:
		if frappe.db.exists("Grievance", normalized):
			name = normalized
		elif frappe.db.exists("Grievance", ticket_number):
			name = ticket_number
		else:
			frappe.throw(_("No grievance found with that ticket number."), title=_("Not Found"))
	doc = frappe.get_doc("Grievance", name)
	from oan_grievance_service.permissions import has_grievance_permission

	if not has_grievance_permission(doc, ptype):
		frappe.throw(
			_("Not permitted to access this grievance."), frappe.PermissionError, title=_("Forbidden")
		)
	return doc


@route("/summary", methods=("GET",), summary="KPI cards summarising grievance status")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def summary():
	"""Counts of visible grievances on each queue status card.

	Draft is excluded. The cards are All, In Progress, Require More Info, Rejected,
	Resolved and Closed. Officers also get an Assigned card. Any other workflow
	state is counted under In Progress. Each card includes its display order and
	whether the workflow treats it as terminal (no per-card default).
	"""
	return success_response(
		data={"cards": get_status_summary()},
		message=_("Grievance status summary fetched successfully"),
	)


@route("/options", methods=("GET",), summary="Get grievance options and dropdowns")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def options(service_category: str | None = None):
	"""Management and lookup options for submitters, grievance officers and admins.

	Returns reference lists for case filing, management, triage, and filtering,
	including departments, lifecycle statuses, categories, and types.

	Args:
	    service_category (str, optional): Filter grievance types by a specific service category (e.g. 'Inputs').

	Returns:
	    departments: Active grievance departments
	    statuses: Grievance lifecycle statuses with metadata
	    service_categories: Active service categories
	    grievance_types: Active grievance types (optionally filtered by service_category)
	    submission_channels: Active intake channels
	"""
	service_categories = get_service_categories()
	grievance_types = get_grievance_types(service_category=service_category)

	data = {
		"departments": get_departments(),
		"statuses": get_status_options(),
		"service_categories": service_categories,
		"grievance_types": grievance_types,
		"submission_channels": active_channels(),
	}

	return success_response(data=data, message=_("Grievance options fetched successfully"))
