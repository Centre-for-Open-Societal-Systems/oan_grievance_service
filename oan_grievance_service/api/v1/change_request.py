"""Change requests: ask for new values on a submitted grievance and decide them.

The Grievance Change Request doctype does the work - snapshotting the case, routing
the request up the hierarchy, checking the decider and applying the change - so these
endpoints only load, insert or save it and shape the answer.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	handle_api_errors,
	page_meta,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from pydantic import BaseModel, Field

from oan_grievance_service.permissions import is_unrestricted
from oan_grievance_service.services import ticket_number as tn

DOCTYPE = "Grievance Change Request"

route = prefixed("/api/v1/change-requests")


class ListChangeRequestsRequest(BaseModel):
	model_config = {"extra": "allow"}

	status: str | None = Field(default="Pending", description="Filter by request status")
	scope: str | None = Field(
		default="pending_with_me", description="Scope: pending_with_me, raised_by_me, all"
	)
	ticket_number: str | None = Field(default=None, description="Filter by grievance ticket number")
	page: int = Field(default=1, ge=1, description="Page number, 1-indexed")
	page_size: int = Field(default=50, ge=1, le=200, description="Items per page")
	limit: int | None = Field(default=None, description="Legacy limit parameter")


class DecideChangeRequest(BaseModel):
	model_config = {"extra": "allow"}

	name: str | None = None
	decision: str = Field(..., min_length=1, description="Approved or Rejected")
	note: str | None = None


def raise_change_request(grievance, subject, changes, reason=None):
	"""Insert a request for `changes` ({fieldname: new_value} or list of dicts) on a loaded grievance."""
	if isinstance(changes, dict):
		change_rows = [{"fieldname": f, "new_value": v} for f, v in changes.items()]
	else:
		change_rows = changes
	return frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"grievance": grievance.name,
			"subject": subject,
			"reason": (reason or "").strip() or None,
			"changes": change_rows,
		}
	).insert(ignore_permissions=True)


def serialize(req):
	ticket_num = frappe.db.get_value("Grievance", req.grievance, "ticket_number") if req.grievance else None
	return {
		"name": req.name,
		"ticket_number": tn.display(ticket_num),
		"subject": req.subject,
		"reason": req.reason,
		"status": req.status,
		"requested_by": req.requested_by,
		"requested_at": to_tz_aware_iso(req.requested_at),
		"pending_with": req.pending_with,
		"pending_since": to_tz_aware_iso(req.pending_since),
		"decided_by": req.decided_by,
		"decided_at": to_tz_aware_iso(req.decided_at),
		"decision_note": req.decision_note,
		"changes": [
			{"fieldname": r.fieldname, "old_value": r.old_value, "new_value": r.new_value}
			for r in getattr(req, "changes", [])
		],
		"trail": [
			{
				"action": r.action,
				"user": r.user,
				"pending_with": r.pending_with,
				"at": to_tz_aware_iso(r.at),
				"note": r.note,
			}
			for r in getattr(req, "approvals", [])
		],
	}


def _can_view(req, user):
	return (
		is_unrestricted(user)
		or user in (req.requested_by, req.pending_with, req.decided_by)
		or frappe.has_permission("Grievance", "read", req.grievance, user=user)
	)


@route("", methods=("GET",), summary="List change requests")
@frappe.whitelist()
@validate_request(ListChangeRequestsRequest)
@handle_api_errors
def list_requests(
	status: str | None = "Pending",
	scope: str | None = "pending_with_me",
	ticket_number: str | None = None,
	page: int = 1,
	page_size: int = 50,
	limit: int | None = None,
	**kwargs,
):
	"""Change requests visible to the caller.

	scope: `pending_with_me` (the caller's approval inbox, and the admin queue for
	admins), `raised_by_me`, or `all` (admins only; everyone else falls back to the
	requests they raised or hold).
	"""
	user = frappe.session.user
	filters = {}
	if status:
		filters["status"] = status
	if ticket_number:
		from oan_grievance_service.api.v1.grievance import _load

		filters["grievance"] = _load(ticket_number, ptype="read").name

	or_filters = None
	if scope == "raised_by_me":
		filters["requested_by"] = user
	elif scope == "all" and is_unrestricted(user):
		pass
	elif scope == "pending_with_me":
		or_filters = [["pending_with", "=", user]]
		if is_unrestricted(user):
			# Requests past the top of the chain wait for any admin.
			or_filters.append(["pending_with", "is", "not set"])
	else:
		or_filters = [["pending_with", "=", user], ["requested_by", "=", user], ["decided_by", "=", user]]

	effective_limit = int(limit or page_size or 50)
	effective_page = max(int(page or 1), 1)
	start = (effective_page - 1) * effective_limit

	all_names = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		pluck="name",
	)
	total_count = len(all_names)

	requests = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"grievance",
			"subject",
			"reason",
			"status",
			"requested_by",
			"requested_at",
			"pending_with",
			"pending_since",
			"decided_by",
			"decided_at",
			"decision_note",
		],
		order_by="creation desc, name desc",
		start=start,
		limit=effective_limit,
	)

	if not requests:
		meta = page_meta(total_count, effective_page, effective_limit)
		return success_response(
			data={"items": [], "count": 0, "pagination": meta},
			pagination=meta,
			message=_("Change requests retrieved successfully"),
		)

	req_names = [r["name"] for r in requests]
	grievance_ids = {r["grievance"] for r in requests if r.get("grievance")}

	ticket_map = {}
	if grievance_ids:
		g_rows = frappe.get_all(
			"Grievance",
			filters={"name": ["in", list(grievance_ids)]},
			fields=["name", "ticket_number"],
		)
		ticket_map = {g["name"]: tn.display(g["ticket_number"]) for g in g_rows}

	changes_map = {name: [] for name in req_names}
	change_items = frappe.get_all(
		"Grievance Change Request Item",
		filters={"parent": ["in", req_names]},
		fields=["parent", "fieldname", "old_value", "new_value"],
		order_by="idx asc",
	)
	for item in change_items:
		changes_map[item["parent"]].append(
			{
				"fieldname": item["fieldname"],
				"old_value": item["old_value"],
				"new_value": item["new_value"],
			}
		)

	approvals_map = {name: [] for name in req_names}
	approval_items = frappe.get_all(
		"Grievance Change Request Approval",
		filters={"parent": ["in", req_names]},
		fields=["parent", "action", "user", "pending_with", "at", "note"],
		order_by="idx asc",
	)
	for app in approval_items:
		approvals_map[app["parent"]].append(
			{
				"action": app["action"],
				"user": app["user"],
				"pending_with": app["pending_with"],
				"at": to_tz_aware_iso(app["at"]),
				"note": app["note"],
			}
		)

	items = []
	for r in requests:
		items.append(
			{
				"name": r["name"],
				"ticket_number": ticket_map.get(r.get("grievance")),
				"subject": r["subject"],
				"reason": r["reason"],
				"status": r["status"],
				"requested_by": r["requested_by"],
				"requested_at": to_tz_aware_iso(r.get("requested_at")),
				"pending_with": r["pending_with"],
				"pending_since": to_tz_aware_iso(r.get("pending_since")),
				"decided_by": r["decided_by"],
				"decided_at": to_tz_aware_iso(r.get("decided_at")),
				"decision_note": r["decision_note"],
				"changes": changes_map.get(r["name"], []),
				"trail": approvals_map.get(r["name"], []),
			}
		)

	meta = page_meta(total_count, effective_page, effective_limit)
	return success_response(
		data={"items": items, "count": len(items), "pagination": meta},
		pagination=meta,
		message=_("Change requests retrieved successfully"),
	)


@route("/<name>", methods=("GET",), summary="Get a change request")
@frappe.whitelist()
@handle_api_errors
def get_request(name: str, **kwargs):
	if not frappe.db.exists(DOCTYPE, name):
		frappe.throw(
			_("Change request '{0}' does not exist.").format(name),
			frappe.DoesNotExistError,
			title=_("Not Found"),
		)
	req = frappe.get_doc(DOCTYPE, name)
	if not _can_view(req, frappe.session.user):
		frappe.throw(_("Not permitted to view this request."), frappe.PermissionError)
	return success_response(data=serialize(req))


@route("/<name>/decide", methods=("POST",), summary="Approve or reject a change request")
@frappe.whitelist()
@validate_request(DecideChangeRequest)
@handle_api_errors
def decide(name: str, decision: str, note: str | None = None, **kwargs):
	"""Rule on a request. Only the person it is pending with, or an admin, may decide."""
	if not frappe.db.exists(DOCTYPE, name):
		frappe.throw(
			_("Change request '{0}' does not exist.").format(name),
			frappe.DoesNotExistError,
			title=_("Not Found"),
		)
	req = frappe.get_doc(DOCTYPE, name)
	req.status = decision.strip().title()
	req.decision_note = (note or "").strip() or None
	req.save(ignore_permissions=True)
	return success_response(
		data=serialize(req),
		message=_("Change request {0}").format(_(req.status).lower()),
	)
