# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Shared fixtures for the doctype tests.

Most audit and SLA records hang off a Grievance, and a Grievance in turn needs a
leaf Administrative Area, a category and a type. Building that chain inline in
every test file is how fixtures drift, so it is built once here.
"""

import frappe
from frappe.utils import now_datetime

from oan_grievance_service.services import constants as C

SEED_CATEGORY = "Inputs"
_CACHED_LEAF_AREA = None
_CACHED_DEPARTMENT = None
_CACHED_SUBMITTER_TYPE = None
_CACHED_SERVICE_CATEGORY = None
_CACHED_GRIEVANCE_TYPE = None


def a_leaf_area():
	"""A leaf Administrative Area a grievance may attach to.

	`Grievance.set_administrative_area_metadata` refuses a group node, so this has
	to be an operational leaf under a region with a valid ticket code. The Ethiopia
	seed provides plenty; one is created only when the seed has not been loaded,
	which is the case on a bare test site.
	"""
	global _CACHED_LEAF_AREA
	if _CACHED_LEAF_AREA and frappe.db.exists("Grievance Administrative Area", _CACHED_LEAF_AREA):
		return _CACHED_LEAF_AREA

	from oan_grievance_service.services.ticket_number import region_of

	for area in frappe.db.get_all(
		"Grievance Administrative Area",
		{"is_group": 0, "is_active": 1},
		pluck="name",
		limit=20,
	):
		if region_of(area):
			_CACHED_LEAF_AREA = area
			return area

	root = frappe.db.get_value(
		"Grievance Administrative Area", {"parent_administrative_area": ["is", "not set"]}, "name"
	)
	if not root:
		root = (
			frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Country",
					"code": "TC",
					"level_name": "Country",
					"country": "Test Country",
					"is_group": 1,
					"is_active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	region = frappe.db.get_value(
		"Grievance Administrative Area", {"level_name": "Region", "ticket_code": ["is", "set"]}, "name"
	)
	if not region:
		region = (
			frappe.get_doc(
				{
					"doctype": "Grievance Administrative Area",
					"area_name": "Test Region",
					"code": "TRG",
					"ticket_code": "T",
					"level_name": "Region",
					"country": "Test Country",
					"parent_administrative_area": root,
					"is_group": 1,
					"is_active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Administrative Area",
				"area_name": "Test Leaf Area",
				"code": "TLA",
				"level_name": "Woreda",
				"country": "Test Country",
				"parent_administrative_area": region,
				"is_group": 0,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def a_grievance(**overrides):
	"""A minimal valid Grievance, inserted and returned.

	Consent is set because `api.v1.grievance.submit` requires it; this bypasses the
	endpoint and writes the document directly, which is what a test about a
	downstream record wants -- the submission path has its own tests.
	"""
	values = {
		"doctype": "Grievance",
		"workflow_state": C.STATE_SUBMITTED,
		"submission_channel": "Web Portal",
		"submitter_type": a_submitter_type(),
		"submitter_name": "Test Submitter",
		"contact_mobile": "+251911234567",
		"administrative_area": a_leaf_area(),
		"service_category": a_service_category(),
		"grievance_type": a_grievance_type(),
		"description": "A description long enough to clear the twenty character minimum.",
		"consent_given": 1,
		"consent_recorded_at": now_datetime(),
	}
	values.update(overrides)
	# Routing is the intake API's step, not the workflow's, so the fixture stays at
	# Submitted unless a test asks for another state.
	return frappe.get_doc(values).insert(ignore_permissions=True)


def discard_grievance(name):
	"""Remove a grievance a test made."""
	if not frappe.db.exists("Grievance", name):
		return
	frappe.delete_doc("Grievance", name, force=True, ignore_permissions=True)


def a_department():
	"""A department for a case to be assigned to; work cannot start without one."""
	global _CACHED_DEPARTMENT
	dept_name = "Test Department"
	if not frappe.db.exists("Grievance Department", dept_name):
		frappe.get_doc(
			{
				"doctype": "Grievance Department",
				"dept_name": dept_name,
				"short_name": "TD",
			}
		).insert(ignore_permissions=True)

	_CACHED_DEPARTMENT = dept_name
	return _CACHED_DEPARTMENT


def a_submitter_type():
	"""Submitter Type became a master doctype; tests should not assume a seed."""
	global _CACHED_SUBMITTER_TYPE
	if _CACHED_SUBMITTER_TYPE and frappe.db.exists("Grievance Submitter Type", _CACHED_SUBMITTER_TYPE):
		return _CACHED_SUBMITTER_TYPE

	existing = frappe.db.get_value(
		"Grievance Submitter Type", {"type_name": "Individual Farmer", "is_active": 1}, "name"
	) or frappe.db.get_value("Grievance Submitter Type", {"is_active": 1}, "name")
	if existing:
		_CACHED_SUBMITTER_TYPE = existing
		return existing

	_CACHED_SUBMITTER_TYPE = (
		frappe.get_doc(
			{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "is_active": 1}
		)
		.insert(ignore_permissions=True)
		.name
	)
	return _CACHED_SUBMITTER_TYPE


def a_service_category():
	global _CACHED_SERVICE_CATEGORY
	if _CACHED_SERVICE_CATEGORY and frappe.db.exists("Grievance Service Category", _CACHED_SERVICE_CATEGORY):
		return _CACHED_SERVICE_CATEGORY

	existing = frappe.db.get_value("Grievance Service Category", SEED_CATEGORY, "name")
	if existing:
		_CACHED_SERVICE_CATEGORY = existing
		return existing

	_CACHED_SERVICE_CATEGORY = (
		frappe.get_doc(
			{
				"doctype": "Grievance Service Category",
				"category_name": SEED_CATEGORY,
				"code": "001",
				"sort_order": 1,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	return _CACHED_SERVICE_CATEGORY


def a_grievance_type():
	"""The seeded category has no types of its own, so make one on first use."""
	global _CACHED_GRIEVANCE_TYPE
	if _CACHED_GRIEVANCE_TYPE and frappe.db.exists("Grievance Type", _CACHED_GRIEVANCE_TYPE):
		return _CACHED_GRIEVANCE_TYPE

	category = a_service_category()
	existing = frappe.db.get_value("Grievance Type", {"service_category": category}, "name")
	if existing:
		_CACHED_GRIEVANCE_TYPE = existing
		return existing

	_CACHED_GRIEVANCE_TYPE = (
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "Test Grievance Type",
				"service_category": category,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	return _CACHED_GRIEVANCE_TYPE


def a_staffed_desk(case, category, department=None):
	"""An active desk for `category` (and `department`) with an active L1 officer, removed after `case`.

	The options lists only offer departments and categories that have such a desk.
	"""
	from oan_grievance_service.api.v1.category_assignment import create_assignment
	from oan_grievance_service.tests.test_category_assignment import _role_level, _user

	_role_level("nodal_officer", 10)
	_role_level("senior_nodal_officer", 20)
	officer = _user(f"staffed-{frappe.generate_hash(length=6)}@example.com", "Staffed Officer")
	desk = create_assignment(service_category=category, department=department, l1_officer=officer, sla_days=5)
	name = desk["data"]["assignment"]["name"]

	def remove():
		frappe.delete_doc("Grievance RBAC Assignment", name, force=True, ignore_permissions=True)
		frappe.delete_doc("User", officer, force=True, ignore_permissions=True)

	case.addCleanup(remove)
