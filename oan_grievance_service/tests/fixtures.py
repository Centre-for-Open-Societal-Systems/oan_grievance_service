# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Shared fixtures for the doctype tests.

Most audit and SLA records hang off a Grievance, and a Grievance in turn needs a
region, a woreda, a category and a type. Building that chain inline in every test
file is how fixtures drift, so it is built once here.

Everything relies on the seed data `setup.install` and `setup.locations` create,
so these helpers assume a migrated site rather than constructing masters of their
own.
"""

import frappe
from frappe.utils import now_datetime

SEED_REGION_CODE = "OROM"
SEED_CATEGORY = "Inputs"


def a_grievance(**overrides):
	"""A minimal valid Grievance, inserted and returned.

	Consent is set because `api.v1.grievance.submit` requires it; this bypasses the
	endpoint and writes the document directly, which is what a test about a
	downstream record wants -- the submission path has its own tests.
	"""
	region = frappe.db.get_value("Region", {"code": SEED_REGION_CODE}, "name")
	woreda = frappe.db.get_value("Woreda", {"region": region}, "name")

	values = {
		"doctype": "Grievance",
		"status": "Submitted",
		"submission_channel": "Web Portal",
		"submitter_type": "Individual Farmer",
		"submitter_name": "Test Submitter",
		"contact_mobile": "+251911234567",
		"region": region,
		"woreda": woreda,
		"service_category": SEED_CATEGORY,
		"grievance_type": a_grievance_type(),
		"description": "A description long enough to clear the twenty character minimum.",
		"consent_given": 1,
		"consent_recorded_at": now_datetime(),
	}
	values.update(overrides)
	return frappe.get_doc(values).insert(ignore_permissions=True)


def a_grievance_type():
	"""The seeded category has no types of its own, so make one on first use."""
	existing = frappe.db.get_value("Grievance Type", {"service_category": SEED_CATEGORY}, "name")
	if existing:
		return existing

	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "Test Grievance Type",
				"service_category": SEED_CATEGORY,
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
