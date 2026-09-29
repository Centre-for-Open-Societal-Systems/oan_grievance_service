"""Change requests: ask for new values on a submitted grievance and decide them.

The Grievance Change Request doctype does the work - snapshotting the case, routing
the request up the hierarchy, checking the decider and applying the change - so these
endpoints only load, insert or save it and shape the answer.
"""

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import handle_api_errors, success_response, validate_request
from pydantic import BaseModel, Field

from oan_grievance_service.permissions import is_unrestricted
from oan_grievance_service.services import ticket_number as tn

DOCTYPE = "Grievance Change Request"

route = prefixed("/api/v1/change-requests")


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
	return {
		"name": req.name,
		"ticket_number": tn.display(frappe.db.get_value("Grievance", req.grievance, "ticket_number")),
		"subject": req.subject,
		"reason": req.reason,
		"status": req.status,
		"requested_by": req.requested_by,
		"requested_at": req.requested_at,
		"pending_with": req.pending_with,
		"pending_since": req.pending_since,
		"decided_by": req.decided_by,
		"decided_at": req.decided_at,
		"decision_note": req.decision_note,
		"changes": [
			{"fieldname": r.fieldname, "old_value": r.old_value, "new_value": r.new_value}
			for r in req.changes
		],
		"trail": [
			{"action": r.action, "user": r.user, "pending_with": r.pending_with, "at": r.at, "note": r.note}
			for r in req.approvals
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
@handle_api_errors
def list_requests(
	status: str | None = "Pending",
	scope: str | None = "pending_with_me",
	ticket_number: str | None = None,
	limit: int = 50,
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

	names = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		order_by="creation desc",
		limit=min(int(limit or 50), 200),
		pluck="name",
	)
	items = [serialize(frappe.get_doc(DOCTYPE, n)) for n in names]
	return success_response(data={"items": items, "count": len(items)})


@route("/<name>", methods=("GET",), summary="Get a change request")
@frappe.whitelist()
@handle_api_errors
def get_request(name: str, **kwargs):
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
	req = frappe.get_doc(DOCTYPE, name)
	req.status = decision.strip().title()
	req.decision_note = (note or "").strip() or None
	req.save(ignore_permissions=True)
	return success_response(
		data=serialize(req),
		message=_("Change request {0}").format(_(req.status).lower()),
	)
