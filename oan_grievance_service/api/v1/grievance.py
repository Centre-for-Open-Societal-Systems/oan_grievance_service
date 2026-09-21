"""FR-02 submission and FR-06 submitter actions, exposed for the mobile app, web
portal, IVR and call centre channels described in FSD 3.2.1.

Every entry point is whitelisted, validates its own input, and routes through the
service layer so the audit trail and notifications cannot be bypassed.
"""

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
from pydantic import BaseModel, Field

from oan_grievance_service.api.v1._options import (
	active_channels,
	get_departments,
	get_grievance_types,
	get_service_categories,
	get_status_options,
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
	region: str | list | None = None
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

	ticket_number: str
	action: str = Field(..., min_length=1)
	reason: str | None = None
	note: str | None = None
	rating: int | None = Field(None, ge=1, le=5)
	comments: str | None = None
	body: str | None = None


class AddNoteRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str
	body: str = Field(..., min_length=1)
	is_internal: bool | str = True


class PostMessageRequest(BaseModel):
	model_config = {"extra": "allow"}

	ticket_number: str
	body: str = Field(..., min_length=1)


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

	Note: area_name is intentionally excluded because display names recur across
	regions/woredas (e.g. over 100 kebeles named '1' or '2') and resolving by name
	causes silent misrouting to an arbitrary region.
	"""
	if not area_identifier:
		return None
	if frappe.db.exists("Grievance Administrative Area", area_identifier):
		return area_identifier
	return frappe.db.get_value(
		"Grievance Administrative Area", {"path_code": area_identifier}, "name"
	) or frappe.db.get_value("Grievance Administrative Area", {"code": area_identifier}, "name")


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


def _resolve_area_filter_identifier(identifier: str) -> str:
	"""Resolve an area identifier, path_code, code, or region area_name for filtering."""
	if not identifier:
		return ""
	identifier = str(identifier).strip()
	resolved = resolve_administrative_area(identifier)
	if resolved:
		return resolved
	# Check if identifier is a Region by display area_name (e.g. 'Oromia', 'Amhara')
	region_doc = frappe.db.get_value(
		"Grievance Administrative Area",
		{"area_name": identifier, "level_name": "Region"},
		"name",
	)
	if region_doc:
		return region_doc
	# Fallback to general area_name
	by_name = frappe.db.get_value("Grievance Administrative Area", {"area_name": identifier}, "name")
	if by_name:
		return by_name

	frappe.throw(
		_("Administrative area '{0}' could not be resolved.").format(identifier),
		frappe.DoesNotExistError,
		title=_("Invalid Area Filter"),
	)


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
	region: str | list | None = None,
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
	service_category/category, administrative_area/region, grievance_type, department, and submission_channel.
	"""
	import math

	effective_limit = limit if limit is not None else page_size
	offset = (page - 1) * effective_limit

	filters = []

	MULTI_SELECT_FIELDS = {
		"status": [status, kwargs.get("status")],
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

	area_list = parse_multi_value(administrative_area or region or kwargs.get("region"))
	if len(area_list) == 1:
		canonical_area = _resolve_area_filter_identifier(area_list[0])
		area_bounds = frappe.db.get_value(
			"Grievance Administrative Area",
			canonical_area,
			["lft", "rgt"],
			as_dict=True,
		)
		if area_bounds and area_bounds.lft is not None and area_bounds.rgt is not None:
			filters.append(["area_lft", ">=", int(area_bounds.lft)])
			filters.append(["area_lft", "<=", int(area_bounds.rgt)])
		else:
			filters.append(["administrative_area", "=", canonical_area])
	elif len(area_list) > 1:
		area_names = set()
		for item in area_list:
			canonical = _resolve_area_filter_identifier(item)
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
					area_names.update(descendants)
				else:
					area_names.add(canonical)
			else:
				area_names.add(canonical)
		if area_names:
			filters.append(["administrative_area", "in", list(area_names)])

	or_filters = []
	if search:
		search_pattern = f"%{search.strip()}%"
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
	total_pages = math.ceil(total_count / effective_limit) if total_count > 0 else 1

	for item in items:
		item["escalated"] = bool(item.get("escalated"))
		item["is_anonymous"] = bool(item.get("is_anonymous"))
		item["department"] = item.get("assigned_dept")

	audit.record_access(audit.ACTION_VIEW_LIST)

	return success_response(
		data={
			"items": items,
			"pagination": {
				"page": page,
				"page_size": effective_limit,
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

	doc = _load(ticket_number, ptype="read")

	# 1. Manual Escalation handler
	if action_name.lower() in ("escalate", "manual escalation"):
		if not reason or not reason.strip():
			frappe.throw(_("A reason is required to escalate a grievance."), title=_("Reason Required"))
		sla.manual_escalate(doc, reason.strip(), by_submitter=True)
		doc.reload()
		return success_response(
			data={
				"ticket_number": doc.ticket_number,
				"status": doc.status,
				"escalated": bool(doc.escalated),
				"action": "Escalate",
				"available_actions": _get_available_actions_for_user(doc),
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
		lifecycle.transition(
			doc, "Close Case", note="Closed after submitter confirmation", closure_type="confirmed"
		)

	elif matching_action == "Reopen":
		if not reason or not reason.strip():
			frappe.throw(_("A reason is required to reopen a grievance."), title=_("Reason Required"))
		lifecycle.transition(doc, "Reopen", reason=reason.strip(), notify=False)
		doc.db_set("reopen_count", (doc.reopen_count or 0) + 1, update_modified=False)
		from oan_grievance_service.services import notifications

		notifications.queue(doc, C.EVENT_REOPENED)

	elif matching_action == "Reject":
		roles = set(frappe.get_roles(frappe.session.user))
		if not (roles & STAFF_ROLES):
			frappe.throw(_("Only staff can reject grievances."), frappe.PermissionError)
		if not reason or not reason.strip():
			frappe.throw(_("A reason is required to reject a grievance."), title=_("Reason Required"))
		lifecycle.transition(doc, "Reject", reason=reason.strip(), closure_type="rejected")

	elif matching_action == "Submitter Reply":
		reply_text = body or reason or note
		if not reply_text or not reply_text.strip():
			frappe.throw(
				_("A response body is required to reply to an information request."),
				title=_("Reply Required"),
			)
		GrievanceTimeline.record(
			grievance=doc.name,
			entry_type="info_response",
			is_internal=False,
			body=reply_text.strip(),
			author_submitter=doc.submitter,
		)
		lifecycle.transition(doc, "Submitter Reply", note="Submitter provided the requested information")
		from oan_grievance_service.services import notifications

		notifications.queue(doc, C.EVENT_SUBMITTER_RESPONDED)

	else:
		lifecycle.transition(doc, matching_action, reason=reason, note=note)

	doc.reload()
	return success_response(
		data={
			"ticket_number": doc.ticket_number,
			"status": doc.status,
			"action": matching_action,
			"available_actions": _get_available_actions_for_user(doc),
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

	for entry in entries:
		entry["is_internal"] = bool(entry.get("is_internal"))
		if entry["author_submitter"]:
			entry["author_type"] = "submitter"
			entry["author_name"] = doc.submitter_name or entry["author_submitter"]
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
			"uploaded_by_user",
			"uploaded_by_submitter",
			"creation",
		],
		order_by="creation asc",
		ignore_permissions=True,
	)
	attachments = list(file_attachments) + list(grievance_attachments)

	return success_response(
		data={
			"name": doc.name,
			"ticket_number": doc.ticket_number,
			"ticket_number_display": tn.display(doc.ticket_number),
			"status": doc.status,
			"escalated": bool(doc.escalated),
			"submitter_name": doc.submitter_name,
			"service_category": doc.service_category,
			"grievance_type": doc.grievance_type,
			"administrative_area": doc.administrative_area,
			"summary": {
				"description": doc.description,
				"desired_outcome": doc.desired_outcome,
				"service_category": doc.service_category,
				"grievance_type": doc.grievance_type,
				"administrative_area": doc.administrative_area,
				"administrative_unit": doc.administrative_unit,
				"submission_channel": doc.submission_channel,
			},
			"submitter": {
				"name": doc.submitter_name,
				"mobile": doc.contact_mobile,
				"email": doc.contact_email,
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
def add_note(ticket_number: str, body: str, is_internal: bool | str = True):
	"""Staff-only endpoint to add an internal or public note to the case timeline."""
	doc = _load(ticket_number)
	internal = (
		str(is_internal).lower() not in ("0", "false", "no")
		if isinstance(is_internal, str)
		else bool(is_internal)
	)

	entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="note",
		is_internal=internal,
		body=body,
		author_user=frappe.session.user,
	)

	return success_response(
		data={
			"name": entry.name,
			"entry_type": entry.entry_type,
			"is_internal": bool(entry.is_internal),
			"author_type": "officer",
			"created_on": entry.created_on,
		},
		message=_("Note added successfully"),
	)


@route("/<ticket_number>/message", methods=("POST",), summary="Post a public message to the conversation")
@frappe.whitelist()
@validate_request(PostMessageRequest)
@handle_api_errors
@require_role(ALLOWED_GRIEVANCE_ROLES)
def message(ticket_number: str, body: str):
	"""Post a public message to the case conversation thread."""
	doc = _load(ticket_number)
	user = frappe.session.user
	is_staff = bool(set(frappe.get_roles(user)) & STAFF_ROLES)

	entry = GrievanceTimeline.record(
		grievance=doc.name,
		entry_type="message",
		is_internal=False,
		body=body,
		author_user=user if is_staff else None,
		author_submitter=doc.submitter if not is_staff else None,
	)

	return success_response(
		data={
			"name": entry.name,
			"entry_type": entry.entry_type,
			"is_internal": False,
			"author_type": "officer" if is_staff else "submitter",
			"created_on": entry.created_on,
		},
		message=_("Message posted successfully"),
	)


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
	doc.check_permission(ptype)
	return doc


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
