# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Versioned officer response templates (STG-405).

A template is the pre-written wording an officer starts from when filing a formal
response. Editing one never overwrites the wording it replaces: the old content is
copied onto the template's `versions` table first and `version` goes up by one. The
current wording always lives on the template itself, so reads need no join.

What counts as an edit that makes a new version: the title, the response type, the
category or subcategory it is filed under, and the two body texts. Switching the
`is_active` flag does not, because it changes who can use the template, not what it says.

Usage is not stored on the template. A counter on a hot row would serialise every
officer's response behind one lock, so `use_count` and `last_used_on` are read from the
Grievance Response rows that link back through `response_template` (see `usage`).

The API layer stays thin and calls into here. Link and field checks live in
`validate_template`, which the doctype controller also runs, so a desk edit and an
API edit are held to the same rules.
"""

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from oan_grievance_service.services.resolvers import (
	resolve_grievance_type,
	resolve_service_category,
)

DOCTYPE = "Grievance Response Template"
VERSION_DOCTYPE = "Grievance Response Template Version"

# Same values as the doctype's `response_type` Select and the Grievance Response Type master.
RESPONSE_TYPES = (
	"Resolved",
	"Partially Resolved",
	"Referred to another dept",
	"Requires further info",
)

# The fields whose change produces a new version, in the order they are snapshotted.
VERSIONED_FIELDS = (
	"title",
	"response_type",
	"service_category",
	"grievance_type",
	"action_taken",
	"resolution_summary",
)

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def extract_placeholders(*texts: str | None) -> list[str]:
	"""Distinct `{{ name }}` placeholder names across the texts, in order of first appearance."""
	seen: dict[str, None] = {}
	for text in texts:
		for name in _PLACEHOLDER.findall(text or ""):
			seen.setdefault(name)
	return list(seen)


def _check_placeholders(label: str, text: str | None) -> None:
	"""Reject a stray or malformed brace pair, which would reach the officer as raw text."""
	leftover = _PLACEHOLDER.sub("", text or "")
	if "{{" in leftover or "}}" in leftover:
		frappe.throw(
			_(
				"{0} has an unclosed or malformed placeholder. Write placeholders as {1} "
				"using letters, digits, and underscores."
			).format(label, "{{ name }}"),
			frappe.ValidationError,
		)


def validate_template(doc) -> None:
	"""Field rules shared by the API and the desk form."""
	for fieldname, label in (
		("title", _("Title")),
		("service_category", _("Service category")),
		("response_type", _("Response type")),
		("action_taken", _("Action taken")),
		("resolution_summary", _("Resolution summary")),
	):
		value = doc.get(fieldname)
		if not (value.strip() if isinstance(value, str) else value):
			frappe.throw(_("{0} is required.").format(label), frappe.ValidationError)

	if doc.response_type not in RESPONSE_TYPES:
		frappe.throw(
			_("Response type must be one of: {0}.").format(", ".join(RESPONSE_TYPES)),
			frappe.ValidationError,
		)

	if doc.grievance_type:
		category = frappe.db.get_value("Grievance Type", doc.grievance_type, "service_category")
		if category != doc.service_category:
			frappe.throw(
				_("Grievance type {0} does not belong to service category {1}.").format(
					doc.grievance_type, doc.service_category
				),
				frappe.ValidationError,
			)

	_check_placeholders(_("Action taken"), doc.action_taken)
	_check_placeholders(_("Resolution summary"), doc.resolution_summary)


def resolve_type(value: str, category: str | None = None) -> str:
	"""Canonical grievance type name by id or type name, narrowed to the category when given."""
	name = resolve_grievance_type(value, category)
	if not name:
		frappe.throw(_("Grievance type '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def get(name: str):
	"""The template document, or a 404."""
	if not frappe.db.exists(DOCTYPE, name):
		frappe.throw(
			_("Response template '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
			title=_("Not Found"),
		)
	return frappe.get_doc(DOCTYPE, name)


def _new_code() -> str:
	from frappe.model.naming import make_autoname

	return make_autoname("RT-.#####")


def create(
	*,
	title: str,
	service_category: str,
	response_type: str,
	action_taken: str,
	resolution_summary: str,
	grievance_type: str | None = None,
	is_active: bool = True,
):
	"""Insert a template at version 1."""
	category = resolve_service_category(service_category)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"template_code": _new_code(),
			"title": title,
			"service_category": category,
			"grievance_type": resolve_type(grievance_type, category) if grievance_type else None,
			"response_type": response_type,
			"action_taken": action_taken,
			"resolution_summary": resolution_summary,
			"is_active": 1 if is_active else 0,
			"version": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _snapshot(doc, *, change_note: str | None) -> dict:
	"""The wording about to be replaced, as a `versions` row."""
	row = {field: doc.get(field) for field in VERSIONED_FIELDS}
	row.update(
		{
			"version": doc.version,
			"retired_on": now_datetime(),
			"retired_by": frappe.session.user,
			"change_note": change_note,
		}
	)
	return row


def update(doc, changes: dict, *, expected_version: int | None = None, change_note: str | None = None):
	"""Apply `changes` to the template, recording the replaced wording as a version.

	`changes` holds only the fields the client sent. A change that leaves every versioned
	field as it was is not an edit, so repeating the same request does not mint versions.
	`expected_version` lets a client refuse to overwrite an edit it has not seen.
	"""
	changes = dict(changes)

	if expected_version is not None and expected_version != doc.version:
		frappe.throw(
			_("This template is now at version {0}, not {1}. Reload it and apply your change again.").format(
				doc.version, expected_version
			),
			frappe.ValidationError,
			title=_("Version Conflict"),
		)

	if "service_category" in changes:
		changes["service_category"] = resolve_service_category(changes["service_category"])
	category = changes.get("service_category", doc.service_category)
	if changes.get("grievance_type"):
		changes["grievance_type"] = resolve_type(changes["grievance_type"], category)
	elif "grievance_type" in changes:
		changes["grievance_type"] = None
	for field in ("title", "action_taken", "resolution_summary"):
		if isinstance(changes.get(field), str):
			changes[field] = changes[field].strip()

	changed = [
		field
		for field in VERSIONED_FIELDS
		if field in changes and (changes[field] or None) != (doc.get(field) or None)
	]
	flag_changed = "is_active" in changes and int(bool(changes["is_active"])) != int(doc.is_active or 0)
	if not changed and not flag_changed:
		return doc

	if changed:
		doc.append("versions", _snapshot(doc, change_note=change_note))
		doc.version = (doc.version or 1) + 1
		for field in changed:
			doc.set(field, changes[field])
	if flag_changed:
		doc.is_active = 1 if changes["is_active"] else 0
	doc.save(ignore_permissions=True)
	return doc


def usage(names: list[str]) -> dict[str, dict]:
	"""`use_count` and `last_used_on` per template, from the responses filed against it.

	One grouped query for all the names. A template with no responses is absent from
	the result, so callers read it with `.get(name)`.
	"""
	if not names:
		return {}
	rows = frappe.get_all(
		"Grievance Response",
		filters={"response_template": ["in", names]},
		fields=[
			"response_template",
			{"COUNT": "name", "as": "use_count"},
			{"MAX": "response_date", "as": "last_used_on"},
		],
		group_by="response_template",
		order_by=None,
	)
	return {row.response_template: row for row in rows}


def remove(doc) -> bool:
	"""Delete a template that was never used, otherwise only switch it off.

	A used template is evidence of what officers sent, so it stays and is deactivated.
	Returns True when the template was deleted.
	"""
	if usage([doc.name]).get(doc.name):
		if doc.is_active:
			doc.is_active = 0
			doc.save(ignore_permissions=True)
		return False
	frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
	return True
