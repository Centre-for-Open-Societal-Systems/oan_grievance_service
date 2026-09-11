"""Save and resume a partially completed submission.

FSD 7 targets farmers on low-connectivity channels and FSD 3.2.1 specifies an
offline-first app. Both make a four-step wizard that only persists on the final
step the wrong shape: the connection is most likely to drop precisely while the
submitter is typing a long description.

A draft is working state, not a record. It is stored unvalidated -- an incomplete
submission is one the Grievance doctype would reject -- and it is cleared on a
schedule once it expires.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

from oan_grievance_service.services import submission

from .grievance import envelope

DRAFT_LIFETIME_DAYS = 30


@frappe.whitelist()
def save(client_uuid, payload=None, step_reached=0):
	"""Create or overwrite the draft for `client_uuid`.

	Overwrites rather than merges: the client holds the whole wizard state, so a
	partial merge would let a field the submitter cleared reappear from an earlier
	save.
	"""
	if not client_uuid:
		frappe.throw(_("A draft key is required."), title=_("Missing Draft Key"))

	data = submission.parse_payload(payload)
	name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")

	if name:
		doc = frappe.get_doc("Grievance Draft", name)
		_assert_owner(doc)
		if doc.submitted_as:
			frappe.throw(
				_("This draft has already been submitted as {0}.").format(doc.submitted_as),
				title=_("Already Submitted"),
			)
	else:
		doc = frappe.new_doc("Grievance Draft")
		doc.client_uuid = client_uuid
		doc.owner_user = _session_user()

	doc.payload = json.dumps(data, ensure_ascii=False)
	doc.step_reached = max(int(step_reached or 0), doc.step_reached or 0)
	doc.contact_mobile = data.get("contact_mobile")
	doc.expires_on = add_days(now_datetime(), DRAFT_LIFETIME_DAYS)
	doc.save(ignore_permissions=True)

	return envelope(
		{
			"client_uuid": doc.client_uuid,
			"step_reached": doc.step_reached,
			"expires_on": doc.expires_on,
			"attachment_count": _attachment_count(doc.name),
		}
	)


@frappe.whitelist()
def load(client_uuid):
	"""Return a saved draft so the wizard resumes where it stopped."""
	name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	if not name:
		frappe.throw(_("No saved draft found."), title=_("Not Found"))

	doc = frappe.get_doc("Grievance Draft", name)
	_assert_owner(doc)

	return envelope(
		{
			"client_uuid": doc.client_uuid,
			"payload": submission.parse_payload(doc.payload),
			"step_reached": doc.step_reached,
			"expires_on": doc.expires_on,
			"submitted_as": doc.submitted_as,
			"attachment_count": _attachment_count(doc.name),
		}
	)


@frappe.whitelist()
def discard(client_uuid):
	"""Delete a draft the submitter abandoned.

	A draft already turned into a grievance is kept: it is what makes a retry of
	`submit` return the original ticket instead of filing a second case.
	"""
	name = frappe.db.get_value("Grievance Draft", {"client_uuid": client_uuid}, "name")
	if not name:
		return envelope({"discarded": False})

	doc = frappe.get_doc("Grievance Draft", name)
	_assert_owner(doc)
	if doc.submitted_as:
		frappe.throw(
			_("This draft became grievance {0} and cannot be discarded.").format(doc.submitted_as),
			title=_("Already Submitted"),
		)

	frappe.delete_doc("Grievance Draft", name, ignore_permissions=True, delete_permanently=True)
	return envelope({"discarded": True})


def purge_expired_drafts():
	"""Daily: clear abandoned drafts. Drafts that became grievances are retained."""
	stale = frappe.get_all(
		"Grievance Draft",
		filters={"expires_on": ["<", now_datetime()], "submitted_as": ["is", "not set"]},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("Grievance Draft", name, ignore_permissions=True, delete_permanently=True)
	return len(stale)


def _session_user():
	user = frappe.session.user
	return None if user in ("Guest", None) else user


def _assert_owner(doc):
	"""A draft claimed by a signed-in user stays with that user.

	Anonymous drafts are protected only by the unguessability of the key, which is
	the same guarantee the ticket-number lookup already relies on.
	"""
	user = _session_user()
	if doc.owner_user and user and doc.owner_user != user:
		if "System Manager" not in frappe.get_roles(user):
			frappe.throw(_("This draft belongs to another user."), frappe.PermissionError)


def _attachment_count(draft_name):
	return frappe.db.count("File", {"attached_to_doctype": "Grievance Draft", "attached_to_name": draft_name})
