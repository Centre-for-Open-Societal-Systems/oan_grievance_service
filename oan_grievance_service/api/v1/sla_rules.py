# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""SLA rules (STG-514): `/api/v1/sla-rules`, the one source of a case's SLA days, urgency and priority.

One record per rule (Grievance SLA Rule), linked to its service category, so a client lists,
creates, changes and deletes one rule at a time. Every rule carries `sla_days` and `is_urgent`;
`priority` is read only and has one value, Urgent for an urgent rule (null otherwise). A rule may name a grievance type of the category, an administrative area (covering
the areas inside it) and a recurring window of the year. The category's base rule names none of
them and is its SLA wherever nothing narrower matches. `services/classification.py` decides which
rule a case matches; nothing here does.

Handlers stay thin. The window, the type's category, the no-tie check and the base rule's
protection live in the rule's `validate()` and `on_trash()`, so they hold for the Desk as well
as the API. A rule is read when a case is filed (and when it moves to another type or category),
so a change applies to cases filed afterwards.
"""

from typing import Literal

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	PageParams,
	api_doc,
	handle_api_errors,
	page_meta,
	require_permission,
	require_role,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from pydantic import BaseModel, Field, field_validator

from oan_grievance_service.api.v1._schemas import Body, NonBlank, blank_to_none
from oan_grievance_service.services import classification
from oan_grievance_service.services.administrative_area import external_area_ids
from oan_grievance_service.services.constants import ADMIN_READ_ROLES
from oan_grievance_service.services.resolvers import resolve_administrative_area, resolve_service_category

route = prefixed("/api/v1/sla-rules")

RULE = "Grievance SLA Rule"
WINDOW_FIELDS = ("start_month", "start_day", "end_month", "end_day")
# What a client may write. `service_category` only on create: a rule stays with its category.
EDITABLE = (
	"rule_label",
	"role_level",
	"grievance_type",
	"administrative_area",
	*WINDOW_FIELDS,
	"sla_days",
	"is_urgent",
)
# `priority` is read only: Urgent for an urgent rule, null otherwise (the only value it has).
RECORD_FIELDS = ["name", "service_category", "is_base", *EDITABLE, "priority", "modified"]
ORDER = "service_category asc, creation asc, name asc"

RULE_NOTE = (
	"Every rule sets sla_days and is_urgent for the cases it matches on their filing date; an urgent "
	"rule gives the case priority Urgent, any other rule no priority (priority is read only). "
	"A rule may name a grievance_type of its category, an administrative_area (covering the areas "
	"inside it) and a recurring window (start_month, start_day, end_month, end_day: all four, or "
	"none for all year; a start after the end wraps the year end, for example 10/1 to 4/30). The "
	"category's base rule (is_base) names none of them: it is the category's SLA wherever no "
	"narrower rule matches, a category needs it before any other rule, and it cannot be deleted or "
	"narrowed. The most specific matching rule wins: a type's rule before the category's, a narrower "
	"area before a wider one, a windowed rule before an all-year one. Two windowed rules, or two "
	"all-year rules, for the same type and area may not share a day. A change applies to cases "
	"filed afterwards."
)

Priority = Literal["Urgent"]


class SlaRuleRecord(BaseModel):
	name: str
	service_category: str
	is_base: bool
	rule_label: str | None = None
	role_level: str | None = Field(
		None, description="Escalation rung the days are for. Null: the days for the whole case"
	)
	grievance_type: str | None = None
	administrative_area: str | None = None
	administrative_area_name: str | None = Field(None, description="The area's name. Read only")
	start_month: int | None = None
	start_day: int | None = None
	end_month: int | None = None
	end_day: int | None = None
	sla_days: int
	is_urgent: bool
	priority: Priority | None = Field(
		None, description="Urgent for an urgent rule, null otherwise. Read only"
	)
	modified: str | None = None


class SlaRuleData(BaseModel):
	rule: SlaRuleRecord


class SlaRuleListData(BaseModel):
	rules: list[SlaRuleRecord]
	pagination: dict


class SlaRuleCluster(BaseModel):
	cluster_id: str = Field(
		description="The id of the cluster's oldest rule; any member's id names the cluster"
	)
	grievance_types: list[str] = Field(
		description="The types its rules are for. Empty: one rule for every type"
	)
	rules: list[SlaRuleRecord] = Field(
		description="Oldest first. All share every field but name, grievance_type and modified"
	)


class SlaRuleClusterData(BaseModel):
	cluster: SlaRuleCluster


class SlaRuleClusterListData(BaseModel):
	clusters: list[SlaRuleCluster]
	pagination: dict


class SlaRulesCreated(BaseModel):
	rules: list[SlaRuleRecord] = Field(description="One rule per grievance type sent, in the order sent")


class RuleRef(Body):
	rule_id: NonBlank


class _RuleScope(Body):
	rule_label: str | None = Field(None, max_length=140, description="A name for administrators")
	role_level: NonBlank | None = Field(
		None,
		description="Role level code of the rung these days are for, e.g. nodal_officer. "
		+ "Blank: the days for the whole case, the ones the SLA clock runs on",
	)
	administrative_area: NonBlank | None = Field(
		None, description="Covers the areas inside it. Blank: everywhere"
	)
	start_month: int | None = Field(None, ge=1, le=12)
	start_day: int | None = Field(None, ge=1, le=31)
	end_month: int | None = Field(None, ge=1, le=12)
	end_day: int | None = Field(None, ge=1, le=31, description="All four window fields, or none for all year")

	_blank = field_validator("rule_label", "role_level", "administrative_area", mode="before")(blank_to_none)


class CreateSlaRule(_RuleScope):
	service_category: NonBlank = Field(description="Id, name or code of the category")
	grievance_types: list[NonBlank] | None = Field(
		None,
		description="Types of the category the rule is for; one rule is made for each, all with the "
		+ "same days, urgency, area and window. Blank: one rule for every type",
	)
	sla_days: int = Field(ge=1, description="Working days for a case this rule matches")
	is_urgent: bool = Field(False, description="true: Urgent handling and priority Urgent")


class UpdateSlaRule(_RuleScope):
	"""Partial update. Omitted fields stay as they are; null clears a scope field."""

	rule_id: NonBlank
	grievance_type: NonBlank | None = Field(
		None, description="A type of the rule's category. Blank: every type"
	)
	_blank_type = field_validator("grievance_type", mode="before")(blank_to_none)
	# Typed optional so an omitted field is told apart from a sent one; a sent null is refused.
	sla_days: int | None = Field(None, ge=1)
	is_urgent: bool | None = Field(None, description="true: Urgent handling and priority Urgent")


class ListSlaRules(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	role_level: str | None = None
	grievance_type: str | None = None
	administrative_area: str | None = None
	is_urgent: bool | None = None
	is_base: bool | None = None

	_blank = field_validator("is_urgent", "is_base", mode="before")(blank_to_none)


class UpdateSlaRuleCluster(_RuleScope):
	"""Partial update of a whole cluster. Omitted fields stay as they are; null clears a scope field."""

	rule_id: NonBlank
	grievance_types: list[NonBlank] | None = Field(
		None,
		description="The cluster's new set of types: kept types' rules change, new types get a rule "
		+ "and dropped types lose theirs. Empty: one rule for every type. Omitted or null: the types stay",
	)
	sla_days: int | None = Field(None, ge=1)
	is_urgent: bool | None = Field(None, description="true: Urgent handling and priority Urgent")


class ListSlaRuleClusters(PageParams, Body):
	"""Unknown query parameters are rejected, so a mistyped filter cannot return an unfiltered list."""

	service_category: str | None = None
	role_level: str | None = None
	administrative_area: str | None = None
	is_urgent: bool | None = None
	is_base: bool | None = None

	_blank = field_validator("is_urgent", "is_base", mode="before")(blank_to_none)


def _records(rows: list) -> list[dict]:
	scope = ("rule_label", "role_level", "grievance_type", "administrative_area", *WINDOW_FIELDS)
	ext_areas = external_area_ids(row.get("administrative_area") for row in rows)
	area_ids = {row.get("administrative_area") for row in rows if row.get("administrative_area")}
	area_names = (
		dict(
			frappe.get_all(
				"Grievance Administrative Area",
				filters={"name": ["in", list(area_ids)]},
				fields=["name", "area_name"],
				as_list=True,
				ignore_permissions=True,
			)
		)
		if area_ids
		else {}
	)
	out = []
	for row in rows:
		fields = {field: row.get(field) or None for field in scope}
		if fields.get("administrative_area"):
			fields["administrative_area_name"] = area_names.get(fields["administrative_area"])
			fields["administrative_area"] = ext_areas.get(
				fields["administrative_area"], fields["administrative_area"]
			)
		out.append(
			SlaRuleRecord(
				name=row.name,
				service_category=row.service_category,
				is_base=bool(row.is_base),
				# Int columns store a blank as 0.
				**fields,
				sla_days=row.sla_days or 0,
				is_urgent=bool(row.is_urgent),
				priority=classification.priority_of(row.is_urgent),
				modified=to_tz_aware_iso(row.modified),
			).model_dump()
		)
	return out


def _record(name: str) -> dict:
	return _records(frappe.get_all(RULE, filters={"name": name}, fields=RECORD_FIELDS))[0]


def _get_rule(rule_id: str):
	if not frappe.db.exists(RULE, rule_id):
		frappe.throw(_("SLA rule '{0}' was not found.").format(rule_id), frappe.DoesNotExistError)
	return frappe.get_doc(RULE, rule_id)


def _values(kwargs: dict) -> dict:
	"""The editable fields the client sent, as the document stores them."""
	values = {key: kwargs[key] for key in EDITABLE if key in kwargs}
	for field in ("sla_days", "is_urgent"):
		# These are never cleared: every rule carries both.
		if field in values and values[field] is None:
			frappe.throw(
				_("{0} cannot be empty: every SLA rule sets it.").format(field), frappe.ValidationError
			)
	if "is_urgent" in values:
		values["is_urgent"] = 1 if values["is_urgent"] else 0
	if values.get("administrative_area"):
		values["administrative_area"] = (
			resolve_administrative_area(values["administrative_area"]) or values["administrative_area"]
		)
	return values


def _cluster_key(row) -> tuple:
	"""What a cluster's rules share: every stored field but the type, and whether they name one,
	so a rule for every type never joins the rules for named types."""
	shared = ("service_category", "is_base", *(field for field in EDITABLE if field != "grievance_type"))
	# Int columns store a blank as 0.
	return (*(row.get(field) or None for field in shared), bool(row.get("grievance_type")))


def _clusters(rows: list) -> list[list]:
	"""Group rows (oldest first) into clusters, in the order of each cluster's oldest rule."""
	clusters: dict[tuple, list] = {}
	for row in rows:
		clusters.setdefault(_cluster_key(row), []).append(row)
	return list(clusters.values())


def _cluster_records(clusters: list[list]) -> list[dict]:
	records = iter(_records([row for cluster in clusters for row in cluster]))
	out = []
	for cluster in clusters:
		rules = [next(records) for _row in cluster]
		out.append(
			SlaRuleCluster(
				cluster_id=rules[0]["name"],
				grievance_types=[rule["grievance_type"] for rule in rules if rule["grievance_type"]],
				rules=rules,
			).model_dump()
		)
	return out


def _cluster_of(rule_id: str) -> list:
	"""The rows of the cluster the rule belongs to, oldest first."""
	rule = _get_rule(rule_id)
	rows = frappe.get_all(
		RULE, filters={"service_category": rule.service_category}, fields=RECORD_FIELDS, order_by=ORDER
	)
	key = _cluster_key(next(row for row in rows if row.name == rule.name))
	return [row for row in rows if _cluster_key(row) == key]


def _list_filters(
	service_category, role_level, grievance_type, administrative_area, is_urgent, is_base
) -> dict:
	filters: dict = {}
	if service_category:
		filters["service_category"] = resolve_service_category(service_category)
	if role_level:
		filters["role_level"] = role_level
	if grievance_type:
		filters["grievance_type"] = grievance_type
	if administrative_area:
		filters["administrative_area"] = (
			resolve_administrative_area(administrative_area) or administrative_area
		)
	if is_urgent is not None:
		filters["is_urgent"] = 1 if is_urgent else 0
	if is_base is not None:
		filters["is_base"] = 1 if is_base else 0
	return filters


@route("", methods=("GET",), summary="List SLA rules", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListSlaRules)
@api_doc(
	summary="List SLA rules",
	description="The SLA rules, by service category and then oldest first. Filter by service_category "
	+ "(id, name or code), role_level, grievance_type, administrative_area, is_urgent or is_base. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleListData,
)
def list_rules(
	service_category: str | None = None,
	role_level: str | None = None,
	grievance_type: str | None = None,
	administrative_area: str | None = None,
	is_urgent: bool | str | None = None,
	is_base: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List rules.

	Numeric and boolean parameters also accept str: frappe checks annotations before
	validate_request runs, and a bare int would turn a bad value into its own type error.
	"""
	params = PageParams(page=page, page_size=page_size)
	filters = _list_filters(
		service_category, role_level, grievance_type, administrative_area, is_urgent, is_base
	)
	rows = frappe.get_all(
		RULE,
		filters=filters,
		fields=RECORD_FIELDS,
		order_by=ORDER,
		offset=params.start,
		limit_page_length=params.page_size,
	)
	return success_response(
		data={
			"rules": _records(rows),
			"pagination": page_meta(frappe.db.count(RULE, filters), params.page, params.page_size),
		},
		message=_("SLA rules retrieved"),
	)


CLUSTER_NOTE = (
	"A cluster is the rules that differ only by grievance type: the ones one create request with "
	"several grievance_types makes. A rule for every type is never in a cluster with rules for named "
	"types. A cluster is named by the id of any of its rules; its cluster_id is its oldest rule's, so it "
	"changes when that rule's type is dropped: use the cluster_id the response returns, not the one sent. "
)


@route("/clusters", methods=("GET",), summary="List SLA rule clusters", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(ListSlaRuleClusters)
@api_doc(
	summary="List SLA rule clusters",
	description=CLUSTER_NOTE
	+ "The clusters, by service category and then by their oldest rule; a page counts clusters, not "
	+ "rules, so a cluster is never split across pages. Filter by service_category (id, name or "
	+ "code), role_level, administrative_area, is_urgent or is_base. Without a service_category every "
	+ "rule is read to build the clusters, so pass one on a site with many rules. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleClusterListData,
)
def list_clusters(
	service_category: str | None = None,
	role_level: str | None = None,
	administrative_area: str | None = None,
	is_urgent: bool | str | None = None,
	is_base: bool | str | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	**kwargs,
):
	"""List clusters. Parameters accept str for the reason `list_rules` gives."""
	params = PageParams(page=page, page_size=page_size)
	filters = _list_filters(service_category, role_level, None, administrative_area, is_urgent, is_base)
	# Every matching rule, since a cluster's members are only known once all are read. A category
	# holds tens of rules, so filter by service_category on a site with many categories.
	clusters = _clusters(frappe.get_all(RULE, filters=filters, fields=RECORD_FIELDS, order_by=ORDER))
	return success_response(
		data={
			"clusters": _cluster_records(clusters[params.start : params.start + params.page_size]),
			"pagination": page_meta(len(clusters), params.page, params.page_size),
		},
		message=_("SLA rule clusters retrieved"),
	)


@route("/clusters/<rule_id>", methods=("PATCH",), summary="Update an SLA rule cluster", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "write")
@validate_request(UpdateSlaRuleCluster, exclude_unset=True)
@api_doc(
	summary="Update an SLA rule cluster",
	description=CLUSTER_NOTE
	+ "Change every rule of the cluster at once, in one transaction: if any rule is refused, nothing "
	+ "changes. Omitted fields stay as they are; null clears a scope field. grievance_types is the "
	+ "cluster's new set of types: rules of kept types change, each new type gets a rule and dropped "
	+ "types lose theirs; empty makes the cluster one rule for every type, omitted or null keeps its types. "
	+ "The base rule's cluster is the base rule alone: its grievance_types cannot change. A change that "
	+ "would make the cluster match another existing cluster is refused (VALIDATION_ERROR). "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleClusterData,
)
def update_cluster(rule_id: str, **kwargs):
	"""Bring a cluster to the fields and types sent. `kwargs` holds only the fields the client sent."""
	members = _cluster_of(rule_id)
	values = _values(kwargs)
	picked = kwargs.get("grievance_types")
	if not values and picked is None:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	first = members[0]
	if picked is None:
		picked = [row.grievance_type for row in members if row.grievance_type]
	if first.is_base and picked:
		frappe.throw(_("The base rule is for every grievance type."), frappe.ValidationError)

	by_type = {row.grievance_type or None: row for row in members}
	wanted = list(dict.fromkeys(picked)) or [None]
	# The route checks `write`; adding or dropping types also creates and deletes rules.
	if any(grievance_type not in by_type for grievance_type in wanted):
		frappe.has_permission(RULE, "create", throw=True)
	if any(grievance_type not in wanted for grievance_type in by_type):
		frappe.has_permission(RULE, "delete", throw=True)
	# Dropped rules go first, so they cannot tie with the rules that change or replace them.
	for grievance_type, row in by_type.items():
		if grievance_type not in wanted:
			frappe.delete_doc(RULE, row.name)
	# Int columns store a blank as 0.
	shared = {field: first.get(field) or None for field in EDITABLE if field != "grievance_type"}
	names = []
	for grievance_type in wanted:
		if grievance_type in by_type:
			# save() needs the whole document, and a cluster holds one rule per grievance type.
			doc = frappe.get_doc(RULE, by_type[grievance_type].name)  # nosemgrep
			if values:
				doc.update(values)
				doc.save()
		else:
			doc = frappe.get_doc(
				{
					"doctype": RULE,
					"service_category": first.service_category,
					**shared,
					**values,
					"grievance_type": grievance_type,
				}
			)
			doc.insert()
		names.append(doc.name)
	cluster = _cluster_of(names[0])
	if len(cluster) != len(names):
		# Rolled back with everything above: the cluster is worked out from shared fields.
		frappe.throw(
			_("These changes would make the cluster match another existing cluster."),
			frappe.ValidationError,
		)
	return success_response(
		data={"cluster": _cluster_records([cluster])[0]},
		message=_("SLA rule cluster updated"),
	)


@route("/clusters/<rule_id>", methods=("DELETE",), summary="Delete an SLA rule cluster", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "delete")
@validate_request(RuleRef)
@api_doc(
	summary="Delete an SLA rule cluster",
	description=CLUSTER_NOTE
	+ "Remove every rule of the cluster, in one transaction. The base rule cannot be deleted "
	+ "(VALIDATION_ERROR). Cases already filed keep the SLA and class a rule gave them.",
	tags=["Administration"],
	response_model=SlaRuleClusterData,
)
def delete_cluster(rule_id: str, **kwargs):
	"""Delete a cluster's rules and return the cluster as it was."""
	members = _cluster_of(rule_id)
	record = _cluster_records([members])[0]
	for row in members:
		frappe.delete_doc(RULE, row.name)
	return success_response(data={"cluster": record}, message=_("SLA rule cluster deleted"))


@route("/<rule_id>", methods=("GET",), summary="Get an SLA rule", tier="officer-core")
@handle_api_errors
@require_role(ADMIN_READ_ROLES)
@validate_request(RuleRef)
@api_doc(
	summary="Get an SLA rule",
	description="One rule. The id is the Grievance SLA Rule's name.",
	tags=["Administration"],
	response_model=SlaRuleData,
)
def get_rule(rule_id: str, **kwargs):
	"""Return one rule."""
	return success_response(
		data={"rule": _record(_get_rule(rule_id).name)},
		message=_("SLA rule retrieved"),
	)


@route("", methods=("POST",), summary="Create an SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "create")
@validate_request(CreateSlaRule)
@api_doc(
	summary="Create an SLA rule",
	description="Add rules to a service category. service_category and sla_days are required; "
	+ "grievance_types (several allowed) makes one rule per type, all sharing the other fields, "
	+ "and none makes one rule for every type; "
	+ "is_urgent defaults to false (true makes the case Urgent, with priority Urgent). priority is "
	+ "not sent: it follows is_urgent. A category's first rule must be its base rule (no "
	+ "type, area or window); creating a category with sla_days, or setting sla_days on "
	+ "a category assignment, makes it. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRulesCreated,
)
def create_rule(service_category: str, grievance_types: list[str] | None = None, **kwargs):
	"""Create a rule for each grievance type sent (one for every type when none is).

	A rule names one type, so the cluster is one record per type; the request fails whole if any
	of them is refused. `kwargs` holds the other fields of the request.
	"""
	category = resolve_service_category(service_category)
	values = _values(kwargs)
	names = []
	for grievance_type in dict.fromkeys(grievance_types or [None]):
		doc = frappe.get_doc(
			{"doctype": RULE, "service_category": category, **values, "grievance_type": grievance_type}
		)
		doc.insert()
		names.append(doc.name)
	rows = frappe.get_all(RULE, filters={"name": ["in", names]}, fields=RECORD_FIELDS)
	rules = sorted(_records(rows), key=lambda rule: names.index(rule["name"]))
	return success_response(data={"rules": rules}, message=_("SLA rules created"))


@route("/<rule_id>", methods=("PATCH",), summary="Update an SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "write")
@validate_request(UpdateSlaRule, exclude_unset=True)
@api_doc(
	summary="Update an SLA rule",
	description="Change any field of one rule but service_category. Omitted fields stay as they are; "
	+ "null clears rule_label, grievance_type, administrative_area or a window field (all four null "
	+ "makes the rule all year). sla_days and is_urgent cannot be cleared; priority follows "
	+ "is_urgent and is not sent. The base rule "
	+ "cannot be narrowed. Cases already filed keep the SLA and class they were filed with. "
	+ RULE_NOTE,
	tags=["Administration"],
	response_model=SlaRuleData,
)
def update_rule(rule_id: str, **kwargs):
	"""Update one rule. `kwargs` holds only the fields the client sent."""
	doc = _get_rule(rule_id)
	values = _values(kwargs)
	if not values:
		frappe.throw(_("No fields to update."), frappe.ValidationError)
	doc.update(values)
	doc.save()
	return success_response(data={"rule": _record(doc.name)}, message=_("SLA rule updated"))


@route("/<rule_id>", methods=("DELETE",), summary="Delete an SLA rule", tier="officer-core")
@handle_api_errors
@require_permission(RULE, "delete")
@validate_request(RuleRef)
@api_doc(
	summary="Delete an SLA rule",
	description="Remove one rule. The base rule cannot be deleted (VALIDATION_ERROR): change its "
	+ "sla_days instead. Cases already filed keep the SLA and class a rule gave them: the case "
	+ "stores them, not the rule. Frappe keeps a copy of the deleted rule in Deleted Documents.",
	tags=["Administration"],
	response_model=SlaRuleData,
)
def delete_rule(rule_id: str, **kwargs):
	"""Delete one rule and return it as it was."""
	doc = _get_rule(rule_id)
	record = _record(doc.name)
	frappe.delete_doc(RULE, doc.name)
	return success_response(data={"rule": record}, message=_("SLA rule deleted"))
