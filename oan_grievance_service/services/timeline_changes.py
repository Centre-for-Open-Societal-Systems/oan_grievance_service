# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""How a change request's internal timeline note reads in the API.

`Grievance Change Request.record_on_timeline` stores the note with the raw values the
request held: field names, officer emails and Grievance Type ids. That text is the audit
record and stays as written. The clients show `body` as it comes, so the timeline API
swaps it for the same note with labels and names (`Assigned Officer: Selam Bekele ->
Hana Girma`), and lists the same changes as data so a client can lay them out itself.
"""

import frappe
from frappe import _

CHANGE_REQUEST = "Grievance Change Request"

# Linked fields whose value is an id nobody reads: the doctype and the field that names it.
_NAMED_BY = {
	"assigned_to": ("User", "full_name"),
	"grievance_type": ("Grievance Type", "type_name"),
}


def field_labels():
	return {
		"assigned_dept": _("Department"),
		"assigned_to": _("Assigned Officer"),
		"service_category": _("Service Category"),
		"grievance_type": _("Grievance Type"),
		"sla_due_date": _("SLA Due Date"),
	}


def load(request_names):
	"""The note body and the changes of each request, keyed by request name."""
	request_names = list(request_names)
	if not request_names:
		return {}

	requests = frappe.get_all(
		CHANGE_REQUEST,
		filters={"name": ["in", request_names]},
		fields=["name", "subject", "status", "reason", "decision_note"],
	)
	rows = frappe.get_all(
		"Grievance Change Request Item",
		filters={"parenttype": CHANGE_REQUEST, "parent": ["in", request_names]},
		fields=["parent", "fieldname", "old_value", "new_value"],
		order_by="idx asc",
	)
	names = _names_for(rows)
	labels = field_labels()

	changes_of = {}
	for row in rows:
		changes_of.setdefault(row.parent, []).append(
			{
				"fieldname": row.fieldname,
				"label": labels.get(row.fieldname, row.fieldname),
				"old_value": row.old_value or None,
				"new_value": row.new_value or None,
				"old_display": _display(names, row.fieldname, row.old_value),
				"new_display": _display(names, row.fieldname, row.new_value),
			}
		)

	return {
		request.name: {
			"body": _body(request, changes_of.get(request.name, [])),
			"changes": changes_of.get(request.name, []),
		}
		for request in requests
	}


def _names_for(rows):
	"""The display name of every officer and grievance type the changes mention."""
	ids = {}
	for row in rows:
		if row.fieldname in _NAMED_BY:
			ids.setdefault(row.fieldname, set()).update(v for v in (row.old_value, row.new_value) if v)

	names = {}
	for fieldname, values in ids.items():
		doctype, title_field = _NAMED_BY[fieldname]
		names[fieldname] = {
			name: title
			for name, title in frappe.get_all(
				doctype,
				filters={"name": ["in", list(values)]},
				fields=["name", title_field],
				as_list=True,
			)
			if title
		}
	return names


def _display(names, fieldname, value):
	if not value:
		return "-"
	return names.get(fieldname, {}).get(value) or value


def _body(request, changes):
	"""The same lines `record_on_timeline` writes, with the display values."""
	lines = [f"{c['label']}: {c['old_display']} -> {c['new_display']}" for c in changes]
	body = f"{request.subject} ({_(request.status).lower()})\n" + "\n".join(lines)
	if request.reason:
		body += f"\nReason: {request.reason}"
	if request.decision_note:
		body += f"\nNote: {request.decision_note}"
	return body
