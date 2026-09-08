"""Document event handlers registered in hooks.py.

These are the joins between a saved record and the workflow the FSD describes, kept
out of the doctype controllers so the sequence is readable in one place.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle, notifications, routing, sla


def grievance_after_insert(doc, method=None):
	"""FSD 4.1 steps 6-8: acknowledge, then route or queue for the nodal officer."""
	if frappe.flags.in_install or frappe.flags.in_migrate:
		return
	notifications.queue(doc, C.EVENT_SUBMISSION_RECEIVED)
	routing.apply_routing(doc)


def response_after_insert(doc, method=None):
	"""FSD 3.5 and Appendix D-2: the response outcome drives the next status."""
	grievance = frappe.get_doc("Grievance", doc.grievance)

	# D-3: response_date, responded_by, sequence and prior_status are filled in by the
	# controller before validation, because they are mandatory. The IP is captured here
	# because it is only meaningful for a request that actually reached the server.
	if getattr(frappe.local, "request_ip", None):
		doc.db_set("ip_address", frappe.local.request_ip, update_modified=False)

	next_status = C.RESPONSE_OUTCOME_NEXT_STATUS.get(doc.response_type)
	if next_status and next_status != grievance.status:
		lifecycle.change_status(
			grievance, next_status, note=f"Response {doc.name} ({doc.response_type})"
		)

	doc.db_set("new_status", next_status or grievance.status, update_modified=False)

	# FSD 4.3: a structured response clears the escalation flag.
	sla.clear_escalation(grievance)

	notifications.queue(grievance, C.EVENT_RESPONSE_SENT)
	doc.db_set("notification_sent", 1, update_modified=False)
	doc.db_set("notification_sent_at", now_datetime(), update_modified=False)


def reassignment_on_update(doc, method=None):
	"""FSD 3.3.1: the target office gets no rights until L2 approves.

	The reassignment is committed here, on approval, and nowhere else, which is what
	makes 'the reassigned officer may act only after the approved assignment is
	committed' true rather than aspirational.
	"""
	from oan_grievance_service.permissions import can_approve_reassignment

	if doc.decision == "Pending":
		notifications.queue(
			frappe.get_doc("Grievance", doc.grievance), C.EVENT_REASSIGNMENT_REQUESTED
		)
		return

	if doc.get_doc_before_save() and doc.get_doc_before_save().decision != "Pending":
		return

	if not can_approve_reassignment():
		frappe.throw(
			_("Only an L2 Senior Nodal Officer may approve or reject a reassignment."),
			title=_("Approval Not Permitted"),
		)

	doc.db_set("decided_at", now_datetime(), update_modified=False)
	doc.db_set("approver", frappe.session.user, update_modified=False)

	if doc.decision != "Approved":
		return

	grievance = frappe.get_doc("Grievance", doc.grievance)
	grievance.db_set("assigned_dept", doc.target_department, update_modified=False)
	if doc.target_officer:
		grievance.db_set("assigned_to", doc.target_officer, update_modified=False)

	# FSD 3.3.1: SLA treatment follows configured policy and is never implicit.
	# Appendix D-2 assumes a reset for a referral; 3.3.1 makes it a decision. The
	# field carries that decision, and an unset field means the clock continues.
	if doc.sla_treatment == "Reset":
		grievance.db_set("sla_due_date", None, update_modified=False)
		grievance.db_set("sla_start_at", None, update_modified=False)
		grievance.db_set("reminder_50_sent", 0, update_modified=False)
		grievance.db_set("reminder_80_sent", 0, update_modified=False)
		sla.start_clock(grievance)

	frappe.get_doc(
		{
			"doctype": "Grievance Status History",
			"grievance": grievance.name,
			"from_status": grievance.status,
			"to_status": grievance.status,
			"changed_by": frappe.session.user,
			"timestamp": now_datetime(),
			"notes": f"Reassigned to {doc.target_department} (SLA {doc.sla_treatment or 'Continue'})",
		}
	).insert(ignore_permissions=True)


def deferral_on_update(doc, method=None):
	"""FSD 3.11.7: an approved deferral extends the SLA window."""
	from oan_grievance_service.permissions import can_approve_deferral

	if doc.status == "Pending":
		return
	before = doc.get_doc_before_save()
	if before and before.status != "Pending":
		return

	if not can_approve_deferral():
		frappe.throw(
			_("Only an L2 Senior Nodal Officer or Department Head may decide a deferral."),
			title=_("Approval Not Permitted"),
		)

	doc.db_set("approver", frappe.session.user, update_modified=False)
	doc.db_set("decided_at", now_datetime(), update_modified=False)

	if doc.status != "Approved":
		return

	max_days = frappe.conf.get("grievance_max_deferral_days") or C.DEFAULT_MAX_DEFERRAL_DAYS
	if doc.additional_days > max_days:
		frappe.throw(
			_("A deferral may not exceed {0} days.").format(max_days),
			title=_("Deferral Too Long"),
		)

	grievance = frappe.get_doc("Grievance", doc.grievance)
	sla.extend_for_deferral(grievance, doc.additional_days)


def anonymity_on_update(doc, method=None):
	"""FSD 9.2: approval masks the submitter from department officers."""
	if doc.status == "Pending":
		return
	before = doc.get_doc_before_save()
	if before and before.status != "Pending":
		return

	doc.db_set("decided_by", frappe.session.user, update_modified=False)
	doc.db_set("decided_at", now_datetime(), update_modified=False)

	grievance = frappe.get_doc("Grievance", doc.grievance)
	grievance.db_set("anonymity_status", doc.status, update_modified=False)

	if doc.status == "Approved":
		grievance.db_set("is_anonymous", 1, update_modified=False)
		grievance.db_set("anonymity_approved_by", frappe.session.user, update_modified=False)
	elif doc.status == "Rejected":
		grievance.db_set("is_anonymous", 0, update_modified=False)
		lifecycle.reject(grievance, "Anonymity refused and identity not disclosed")
