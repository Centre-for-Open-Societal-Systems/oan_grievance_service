"""Access audit trail.

The Grievance Access Audit Event doctype records reads, which the change log cannot, because reading a grievance changes nothing.
Every question about a breach — who opened this case, who exported a region — is a read.
"""

import time

import frappe
from frappe import _
from frappe.utils import now_datetime

ACTION_VIEW_DETAIL = "view_detail"
ACTION_VIEW_LIST = "view_list"
ACTION_EXPORT = "export"
ACTION_VIEW_ATTACHMENT = "view_attachment"
ACTION_VIEW_SUBMITTER_IDENTITY = "view_submitter_identity"
# The one write in a trail otherwise made of reads. Evidence leaving a case is
# exactly what an auditor asking "what was this decided on" needs to see, and
# recording it as a view -- which is what it was doing -- hides it among them.
ACTION_DELETE_ATTACHMENT = "delete_attachment"
# An export that was allowed but produced nothing. `export` rows mean the file left the
# system; this one means someone was cleared to take it and it failed to render.
ACTION_EXPORT_FAILED = "export_failed"

# A durable write that loses an insert race (a lock conflict, or a random name that is already
# taken) is tried again this many times in all.
AUDIT_WRITE_ATTEMPTS = 3
AUDIT_RETRY_PAUSE_SECONDS = 0.05


def record_access(action, grievance=None, scope=None, decision="Allowed", durable=False, strict=False):
	"""Write an access audit row. Never raises unless `strict`: auditing must not break the request.

	`durable` commits the row. The API error handler rolls the transaction back before it
	answers, so a row written just before a refusal or a failure is lost with it; pass it
	where the caller is about to raise and the attempt must still be on record.

	`strict` re-raises a failed write after logging it. Use it where the action must not
	happen unless it is on record, such as an export that hands personal data to the caller.

	Rows are named at random (the doctype's `autoname` is "hash"). They used to be numbered
	from one counter row in `tabSeries` that every audit write moved, so under snapshot
	isolation (MariaDB 11.6+ default) a request that read for a while before auditing, as an
	export does while it renders a PDF, failed with "Record has changed since last read in
	table 'tabSeries'" whenever anyone else audited meanwhile. With no shared counter that
	cannot happen. The rest is defence in depth for a durable write: it commits first, which
	ends the request's read snapshot and loses nothing (it commits straight after anyway); an
	insert that still loses a race is rolled back, which can only discard the failed insert,
	and tried again; a failure of the commit itself is not retried, so one attempt never
	leaves two rows.
	"""
	try:
		if durable:
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		_insert_event(action, grievance, scope, decision, retry=durable)
		if durable:
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	except Exception:
		frappe.log_error(
			title="Access audit write failed",
			message=f"action={action}; durable={durable}\n{frappe.get_traceback()}",
		)
		if strict:
			raise


def _insert_event(action, grievance, scope, decision, retry):
	from oan_grievance_service.permissions import GRIEVANCE_ROLES

	roles = set(frappe.get_roles())
	event = {
		"doctype": "Grievance Access Audit Event",
		"timestamp": now_datetime(),
		"user": frappe.session.user,
		"role": next((role for role in GRIEVANCE_ROLES if role in roles), None),
		"grievance": grievance,
		"action": action,
		"decision": decision,
		"scope_evaluated": scope,
		"source": frappe.local.request.path if getattr(frappe.local, "request", None) else "server",
	}
	attempts = AUDIT_WRITE_ATTEMPTS if retry else 1
	for attempt in range(1, attempts + 1):
		try:
			frappe.get_doc(event).insert(ignore_permissions=True)
			return
		except (frappe.QueryDeadlockError, frappe.DuplicateEntryError):
			if attempt == attempts:
				raise
			frappe.db.rollback()
			time.sleep(AUDIT_RETRY_PAUSE_SECONDS * attempt)


def on_grievance_view(doc, method=None):
	"""Hooked to onload so opening a case leaves a trace."""
	if frappe.session.user == "Administrator" and frappe.flags.in_install:
		return
	record_access(ACTION_VIEW_DETAIL, grievance=doc.name)


def log_denied(action, grievance=None, scope=None, durable=False):
	"""An unauthorised attempt is denied and logged."""
	record_access(action, grievance=grievance, scope=scope, decision="Denied", durable=durable)


class ImmutableRecord:
	"""Mixin for append-only doctypes: a row may be created, never changed or removed.

	The permission flags on these doctypes already deny write and delete in the desk,
	but permissions are exactly what server code bypasses -- every insert here runs
	with `ignore_permissions=True`, and `db_set` skips the ORM altogether. An audit
	trail whose only defence is a permission flag is one `frappe.db.set_value` away
	from being rewritten, with nothing recording that it happened.

	The trail has to be evidence. Evidence that the application can silently
	revise is not evidence, so the refusal lives in the document lifecycle where the
	service layer cannot step around it.
	"""

	def on_update(self):
		# get_doc_before_save() is None on insert and the prior version on update,
		# which is the only reliable way to tell the two apart in this hook.
		if self.get_doc_before_save():
			frappe.throw(
				_("{0} is an audit record and cannot be modified after it is written.").format(self.doctype),
				title=_("Immutable Record"),
			)

	def on_trash(self):
		frappe.throw(
			_("{0} is an audit record and cannot be deleted.").format(self.doctype),
			title=_("Immutable Record"),
		)
