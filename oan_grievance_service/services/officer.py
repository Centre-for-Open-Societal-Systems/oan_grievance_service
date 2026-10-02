# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Officer management: create, read, update and list L1 / L2 officer profiles.

Field checks (email, region tier, who an L2 may report to) live in the profile's own
`validate()`. This module resolves client identifiers to canonical doc names, guards
uniqueness, and projects rows to API records. Officers are never deleted: `status`
carries Active / On Leave / Inactive, so history that points at an officer survives.
"""

from collections import defaultdict

import frappe
from frappe import _

from oan_grievance_service.services.resolvers import (
	resolve_administrative_area,
	resolve_department,
	resolve_service_category,
)

DOCTYPE = "Grievance Officer Profile"
CATEGORY_DOCTYPE = "Grievance Officer Service Category"
FIELDS = [
	"name",
	"full_name",
	"designation",
	"level",
	"department",
	"email",
	"phone",
	"region",
	"status",
	"reports_to",
	"user",
]


def get_profile(name: str):
	"""The officer profile with this id, or DoesNotExistError."""
	if not name or not frappe.db.exists(DOCTYPE, name):
		frappe.throw(_("Officer '{0}' was not found.").format(name), frappe.DoesNotExistError)
	return frappe.get_doc(DOCTYPE, name)


def create(
	*,
	full_name: str,
	designation: str,
	level: str,
	department: str,
	email: str,
	phone: str | None = None,
	region: str | None = None,
	status: str = "Active",
	service_categories: list[str] | None = None,
	reports_to: str | None = None,
	user: str | None = None,
):
	"""Insert a profile. Returns the document."""
	values = {
		"full_name": full_name,
		"designation": designation,
		"level": level,
		"email": email,
		"phone": phone,
		"status": status,
		"user": user,
		**_resolved(
			department=department,
			region=region,
			service_categories=service_categories or [],
			reports_to=reports_to,
		),
	}
	_check_unique(values)
	categories = values.pop("service_categories")
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			**values,
			"service_categories": [{"service_category": name} for name in categories],
		}
	)
	doc.insert()
	return doc


def update(doc, changes: dict):
	"""Apply a partial update. `service_categories` replaces the whole list."""
	values = dict(changes)
	resolvable = {"department", "region", "service_categories", "reports_to"} & values.keys()
	values.update(_resolved(**{key: values[key] for key in resolvable}))
	_check_unique(values, exclude=doc.name)
	categories = values.pop("service_categories", None)
	doc.update(values)
	if categories is not None:
		doc.set("service_categories", [{"service_category": name} for name in categories])
	doc.save()
	return doc


def _resolved(**raw) -> dict:
	"""Canonical doc names for whichever identifiers were given. Blank means "clear it"."""
	out = {}
	if "department" in raw:
		out["department"] = resolve_department(raw["department"])
	if "region" in raw:
		out["region"] = _resolve_region(raw["region"])
	if "service_categories" in raw:
		out["service_categories"] = list(
			dict.fromkeys(resolve_service_category(value) for value in raw["service_categories"])
		)
	if "reports_to" in raw:
		out["reports_to"] = get_profile(raw["reports_to"]).name if raw["reports_to"] else None
	return out


def _resolve_region(value: str | None) -> str | None:
	if not value:
		return None
	name = resolve_administrative_area(value)
	if not name:
		frappe.throw(_("Region '{0}' does not exist.").format(value), frappe.ValidationError)
	return name


def _check_unique(values: dict, exclude: str | None = None):
	"""One profile per email and per login. Checked here so a clash is a 400, not a database error."""
	if values.get("email"):
		values["email"] = values["email"].strip().lower()
	for field, label in (("email", _("Email")), ("user", _("User account"))):
		if not values.get(field):
			continue
		clash = frappe.db.exists(DOCTYPE, {field: values[field], "name": ["!=", exclude or ""]})
		if clash:
			frappe.throw(
				_("{0} {1} already belongs to officer {2}.").format(label, values[field], clash),
				frappe.ValidationError,
			)


def list_filters(*, level=None, department=None, status=None) -> dict:
	"""Filters for `frappe.get_all`."""
	filters = {}
	if level:
		filters["level"] = level
	if department:
		filters["department"] = resolve_department(department)
	if status:
		filters["status"] = status
	return filters


def search_filters(q: str | None) -> list | None:
	"""`or_filters` matching name, email or id, or None when there is no search text."""
	if not q:
		return None
	like = f"%{q.strip()}%"
	return [
		[DOCTYPE, "full_name", "like", like],
		[DOCTYPE, "email", "like", like],
		[DOCTYPE, "name", "like", like],
	]


def count(filters: dict, or_filters: list | None) -> int:
	"""Matching profiles, counted in SQL."""
	return frappe.get_all(
		DOCTYPE, filters=filters, or_filters=or_filters, fields=[{"COUNT": "*", "as": "total"}]
	)[0].total


def records(rows: list) -> list[dict]:
	"""Project profile rows to API records with one query each for categories, regions and supervisors."""
	if not rows:
		return []
	categories = defaultdict(list)
	for row in frappe.get_all(
		CATEGORY_DOCTYPE,
		filters={"parent": ["in", [row.name for row in rows]], "parenttype": DOCTYPE},
		fields=["parent", "service_category"],
		order_by="parent, idx",
	):
		categories[row.parent].append(row.service_category)
	region_names = {
		area.name: area.area_name
		for area in frappe.get_all(
			"Grievance Administrative Area",
			filters={"name": ["in", list({row.region for row in rows if row.region})]},
			fields=["name", "area_name"],
		)
	}
	supervisors = {
		profile.name: profile.full_name
		for profile in frappe.get_all(
			DOCTYPE,
			filters={"name": ["in", list({row.reports_to for row in rows if row.reports_to})]},
			fields=["name", "full_name"],
		)
	}
	return [
		{
			**{field: row.get(field) or None for field in FIELDS},
			"region_name": region_names.get(row.region),
			"reports_to_name": supervisors.get(row.reports_to),
			"service_categories": categories[row.name],
		}
		for row in rows
	]


def record(name: str) -> dict:
	"""One profile as an API record."""
	return records(frappe.get_all(DOCTYPE, filters={"name": name}, fields=FIELDS))[0]
