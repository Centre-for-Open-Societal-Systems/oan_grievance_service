"""FR-10 Audit and Compliance.

The Grievance Access Audit Event doctype is FSD section 5's Access Audit Event: it
records reads, which the change log cannot, because reading a grievance changes nothing.
Every question about a breach — who opened this case, who exported a region — is a read.
"""

import frappe
from frappe.utils import now_datetime

ACTION_VIEW_DETAIL = "view_detail"
ACTION_VIEW_LIST = "view_list"
ACTION_EXPORT = "export"
ACTION_VIEW_ATTACHMENT = "view_attachment"
ACTION_VIEW_SUBMITTER_IDENTITY = "view_submitter_identity"


def record_access(action, grievance=None, scope=None, decision="Allowed"):
	"""Write an access audit row. Never raises: auditing must not break the request."""
	try:
		frappe.get_doc(
			{
				"doctype": "Grievance Access Audit Event",
				"timestamp": now_datetime(),
				"user": frappe.session.user,
				"role": primary_role(),
				"grievance": grievance,
				"action": action,
				"decision": decision,
				"scope_evaluated": scope,
				"source": frappe.local.request.path if getattr(frappe.local, "request", None) else "server",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Access audit write failed", message=frappe.get_traceback())


def primary_role():
	"""The grievance role this user holds, for the audit row."""
	from oan_grievance_service.permissions import GRIEVANCE_ROLES

	roles = set(frappe.get_roles())
	for role in GRIEVANCE_ROLES:
		if role in roles:
			return role
	return None


def on_grievance_view(doc, method=None):
	"""Hooked to onload so opening a case leaves a trace."""
	if frappe.session.user == "Administrator" and frappe.flags.in_install:
		return
	record_access(ACTION_VIEW_DETAIL, grievance=doc.name)


def log_denied(action, grievance=None, scope=None):
	"""FSD UC-02 E1: an unauthorised attempt is denied and logged."""
	record_access(action, grievance=grievance, scope=scope, decision="Denied")
