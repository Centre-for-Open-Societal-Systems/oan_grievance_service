# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Shared fixtures for the doctype tests.

Most audit and SLA records hang off a Grievance, and a Grievance in turn needs a
leaf Administrative Area, a category and a type. Building that chain inline in
every test file is how fixtures drift, so it is built once here.
"""

import frappe
from frappe.utils import now_datetime

SEED_CATEGORY = "Inputs"


def a_leaf_area():
	"""A leaf Administrative Area a grievance may attach to.

	`Grievance.set_administrative_area_metadata` refuses a group node, so this has
	to be an operational leaf under a region with a valid ticket code. The Ethiopia
	seed provides plenty; one is created only when the seed has not been loaded,
	which is the case on a bare test site.
	"""
	from oan_grievance_service.services.ticket_number import region_of

	for area in frappe.db.get_all(
		"Grievance Administrative Area",
		{"is_group": 0, "is_active": 1},
		pluck="name",
		limit=20,
	):
		if region_of(area):
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
		"status": "Submitted",
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
	return frappe.get_doc(values).insert(ignore_permissions=True)


def a_submitter_type():
	"""Submitter Type became a master doctype; tests should not assume a seed."""
	existing = frappe.db.get_value("Grievance Submitter Type", {}, "name")
	if existing:
		return existing

	return (
		frappe.get_doc(
			{"doctype": "Grievance Submitter Type", "type_name": "Individual Farmer", "is_active": 1}
		)
		.insert(ignore_permissions=True)
		.name
	)


def a_service_category():
	existing = frappe.db.get_value("Grievance Service Category", SEED_CATEGORY, "name")
	if existing:
		return existing

	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Service Category",
				"category_name": SEED_CATEGORY,
				"code": "INPT",
				"sort_order": 1,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def a_grievance_type():
	"""The seeded category has no types of its own, so make one on first use."""
	category = a_service_category()
	existing = frappe.db.get_value("Grievance Type", {"service_category": category}, "name")
	if existing:
		return existing

	return (
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
