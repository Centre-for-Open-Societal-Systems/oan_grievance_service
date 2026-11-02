# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.grievance_masters.doctype.grievance_service_provider_type.grievance_service_provider_type import (
	ensure_usable,
)
from oan_grievance_service.setup.install import SERVICE_PROVIDER_TYPES, seed_service_provider_types

DOCTYPE = "Grievance Service Provider Type"


class TestGrievanceServiceProviderType(FrappeTestCase):
	def setUp(self):
		self.tag = frappe.generate_hash(length=6)

	def test_the_seed_creates_the_first_set_of_types_and_runs_again_without_effect(self):
		seed_service_provider_types()
		for name in SERVICE_PROVIDER_TYPES:
			self.assertTrue(frappe.db.get_value(DOCTYPE, name, "is_active"), name)
		self.assertEqual(seed_service_provider_types(), [])

	def test_seeding_keeps_a_type_an_administrator_retired(self):
		seed_service_provider_types()
		# Rolled back after, so the seeded Market stays active for every other test in the class.
		frappe.db.savepoint("retired_seed_type")
		frappe.db.set_value(DOCTYPE, "Market", "is_active", 0)

		seed_service_provider_types()

		self.assertEqual(frappe.db.get_value(DOCTYPE, "Market", "is_active"), 0)
		frappe.db.rollback(save_point="retired_seed_type")

	def test_the_name_is_trimmed_and_unique(self):
		doc = frappe.get_doc({"doctype": DOCTYPE, "type_name": f"  Union {self.tag}  "}).insert()
		self.assertEqual(doc.name, f"Union {self.tag}")
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc({"doctype": DOCTYPE, "type_name": f"Union {self.tag}"}).insert()

	def test_a_new_type_is_active_by_default(self):
		doc = frappe.get_doc({"doctype": DOCTYPE, "type_name": f"Fresh {self.tag}"}).insert()
		self.assertEqual(doc.is_active, 1)

	def test_only_an_active_type_is_usable(self):
		active = frappe.get_doc({"doctype": DOCTYPE, "type_name": f"On {self.tag}"}).insert()
		retired = frappe.get_doc(
			{"doctype": DOCTYPE, "type_name": f"Off {self.tag}", "is_active": 0}
		).insert()

		ensure_usable(active.name)
		with self.assertRaises(frappe.ValidationError):
			ensure_usable(retired.name)

	def test_nobody_can_delete_a_type(self):
		self.assertFalse([p.role for p in frappe.get_meta(DOCTYPE).permissions if p.delete])
