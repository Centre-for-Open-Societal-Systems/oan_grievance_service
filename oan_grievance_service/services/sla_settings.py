# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""SLA settings behind the Administration SLA tab: the installation-wide policy and the per-category windows.

Two records, and no third. The installation-wide policy is the `Grievance Deferral Policy`
Single: the deferral ceiling, who approves a deferral, and the default escalation
threshold. The per-category settings are the category's `Grievance SLA Configuration`:
`sla_days`, `auto_escalate` and `notify_on_breach`. That is the same row the Category
Assignments tab (STG-404) writes `sla_days` and `auto_escalate` to, so a change made on
either tab is the other's value. One active row per category, shared by every department
that serves it, and the row the API edits is the row `sla.resolve_policy` enforces.

Writes go through the document's `save()`, so validation, hooks and version history run.
"""

import frappe
from frappe import _

from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
	auto_escalation_threshold,
	max_deferral_days,
	requires_supervisor_approval,
)
from oan_grievance_service.services import category_assignment
from oan_grievance_service.services.resolvers import resolve_department, resolve_service_category

POLICY_DOCTYPE = "Grievance Deferral Policy"
SLA_DOCTYPE = category_assignment.SLA_DOCTYPE

L2_APPROVAL = "l2_approval"
L1_SELF_APPROVE = "l1_self_approve"

CONFIG_FIELDS = ["name", "service_category", "sla_days", "auto_escalate", "notify_on_breach", "modified"]


def global_policy() -> dict:
	"""The installation-wide policy, with the defaults filled in while the Single is untouched."""
	return {
		"max_deferral_days": max_deferral_days(),
		"auto_escalation_threshold": auto_escalation_threshold(),
		"deferral_approval": L2_APPROVAL if requires_supervisor_approval() else L1_SELF_APPROVE,
		"modified": frappe.get_cached_doc(POLICY_DOCTYPE).modified,
	}


def update_global_policy(changes: dict) -> dict:
	"""Apply a partial update to the installation-wide policy and return it as saved."""
	policy = frappe.get_doc(POLICY_DOCTYPE)
	for field in ("max_deferral_days", "auto_escalation_threshold"):
		if field in changes:
			policy.set(field, changes[field])
	if "deferral_approval" in changes:
		policy.requires_supervisor_approval = 1 if changes["deferral_approval"] == L2_APPROVAL else 0
	policy.save()
	return global_policy()


def get_config(name: str):
	"""The active SLA configuration with this name, or DoesNotExistError.

	An inactive row is not served by the API: routing ignores it, so editing it would change
	nothing a case ever sees.
	"""
	if not name or not frappe.db.exists(SLA_DOCTYPE, {"name": name, "active": 1}):
		frappe.throw(
			_("SLA configuration '{0}' was not found.").format(name),
			frappe.DoesNotExistError,
		)
	return frappe.get_doc(SLA_DOCTYPE, name)


def update_config(config, changes: dict) -> None:
	"""Apply a partial update to a category's SLA configuration."""
	for field in ("sla_days", "auto_escalate", "notify_on_breach"):
		if field in changes:
			config.set(field, int(changes[field]))
	config.save()


def list_configs(
	*,
	service_category: str | None,
	department: str | None,
	start: int,
	page_size: int,
) -> tuple[list[dict], int]:
	"""One page of active SLA configurations, with the departments serving each category.

	Filtering, ordering and paging are done in SQL. A department narrows the list to the
	categories it has an active desk for.
	"""
	filters: dict = {"active": 1}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	if department:
		served = frappe.get_all(
			category_assignment.DOCTYPE,
			filters=category_assignment.desk_filters(
				department_scope=resolve_department(department), active=1
			),
			pluck="category_scope",
		)
		if "service_category" in filters:
			served = [name for name in served if name == filters["service_category"]]
		filters["service_category"] = ["in", served or [""]]
	rows = frappe.get_all(
		SLA_DOCTYPE,
		filters=filters,
		fields=CONFIG_FIELDS,
		order_by="service_category asc, name asc",
		offset=start,
		limit_page_length=page_size,
	)
	return config_records(rows), frappe.db.count(SLA_DOCTYPE, filters)


def config_records(rows: list) -> list[dict]:
	"""Project SLA rows to API records with one query for the departments of the whole page."""
	departments: dict[str, list[str]] = {row.service_category: [] for row in rows}
	if departments:
		for desk in frappe.get_all(
			category_assignment.DOCTYPE,
			filters=category_assignment.desk_filters(category_scope=["in", list(departments)], active=1),
			fields=["category_scope", "department_scope"],
			order_by="department_scope asc",
		):
			departments[desk.category_scope].append(desk.department_scope)
	return [
		{
			"name": row.name,
			"service_category": row.service_category,
			"departments": departments[row.service_category],
			"sla_days": row.sla_days,
			"auto_escalate": bool(row.auto_escalate),
			"notify_on_breach": bool(row.notify_on_breach),
			"modified": row.modified,
		}
		for row in rows
	]


def config_record(name: str) -> dict:
	"""One SLA configuration as an API record."""
	return config_records(frappe.get_all(SLA_DOCTYPE, filters={"name": name}, fields=CONFIG_FIELDS))[0]
