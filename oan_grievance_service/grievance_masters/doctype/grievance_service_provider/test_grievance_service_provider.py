# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.setup.install import seed_service_provider_types
from oan_grievance_service.tests.fixtures import a_service_provider


class TestGrievanceServiceProvider(FrappeTestCase):
	def setUp(self):
		self.tag = frappe.generate_hash(length=6)
		seed_service_provider_types()

	def test_ids_come_from_the_sp_series_and_do_not_follow_the_name(self):
		name = a_service_provider(f"CBE {self.tag}", provider_type="Bank")
		self.assertRegex(name, r"^SP-\d{5}$")

		doc = frappe.get_doc("Grievance Service Provider", name)
		doc.provider_name = f"Commercial Bank of Ethiopia {self.tag}"
		doc.save()
		self.assertEqual(doc.name, name)

	def test_new_providers_are_active_by_default(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Service Provider",
				"provider_name": f"Default {self.tag}",
				"provider_type": "Market",
			}
		).insert()
		self.assertEqual(doc.is_active, 1)

	def test_the_name_is_trimmed(self):
		name = a_service_provider(f"  Padded {self.tag}  ")
		self.assertEqual(
			frappe.db.get_value("Grievance Service Provider", name, "provider_name"), f"Padded {self.tag}"
		)

	def test_a_provider_type_is_required_and_must_be_known(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Grievance Service Provider", "provider_name": f"No type {self.tag}"}
			).insert()
		with self.assertRaises(frappe.ValidationError):
			a_service_provider(f"Odd type {self.tag}", provider_type="Pawn Shop")

	def test_the_type_is_a_link_to_the_type_master_not_a_typed_in_list(self):
		field = frappe.get_meta("Grievance Service Provider").get_field("provider_type")
		self.assertEqual((field.fieldtype, field.options), ("Link", "Grievance Service Provider Type"))

	def test_a_type_an_administrator_adds_can_be_used_at_once(self):
		new_type = frappe.get_doc(
			{"doctype": "Grievance Service Provider Type", "type_name": f"Type {self.tag}"}
		).insert()
		name = a_service_provider(f"Typed {self.tag}", provider_type=new_type.name)
		self.assertEqual(
			frappe.db.get_value("Grievance Service Provider", name, "provider_type"), new_type.name
		)

	def test_a_retired_type_cannot_be_given_to_a_new_or_changed_provider(self):
		retired = frappe.get_doc(
			{"doctype": "Grievance Service Provider Type", "type_name": f"Old {self.tag}", "is_active": 0}
		).insert()
		with self.assertRaises(frappe.ValidationError):
			a_service_provider(f"New {self.tag}", provider_type=retired.name)

		doc = frappe.get_doc("Grievance Service Provider", a_service_provider(f"Changed {self.tag}"))
		doc.provider_type = retired.name
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_provider_keeps_a_type_that_is_retired_later(self):
		kind = frappe.get_doc(
			{"doctype": "Grievance Service Provider Type", "type_name": f"Kind {self.tag}"}
		).insert()
		name = a_service_provider(f"Kept {self.tag}", provider_type=kind.name)
		frappe.db.set_value("Grievance Service Provider Type", kind.name, "is_active", 0)

		doc = frappe.get_doc("Grievance Service Provider", name)
		doc.registration_number = "REG-1"
		doc.save()

		self.assertEqual(doc.provider_type, kind.name)

	def test_a_provider_cannot_be_its_own_parent(self):
		doc = frappe.get_doc("Grievance Service Provider", a_service_provider(f"Self {self.tag}"))
		doc.parent_provider = doc.name
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_the_parent_chain_cannot_loop(self):
		head = a_service_provider(f"Head {self.tag}", provider_type="Bank")
		branch = a_service_provider(f"Branch {self.tag}", provider_type="Bank", parent_provider=head)
		sub_branch = a_service_provider(f"Sub {self.tag}", provider_type="Bank", parent_provider=branch)

		doc = frappe.get_doc("Grievance Service Provider", head)
		doc.parent_provider = sub_branch
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_branch_can_name_a_parent(self):
		head = a_service_provider(f"Head {self.tag}", provider_type="Bank")
		branch = a_service_provider(f"Branch {self.tag}", provider_type="Bank", parent_provider=head)
		self.assertEqual(frappe.db.get_value("Grievance Service Provider", branch, "parent_provider"), head)

	def test_the_parent_must_exist(self):
		with self.assertRaises(frappe.LinkValidationError):
			a_service_provider(f"Orphan {self.tag}", parent_provider="SP-99999")

	def test_nobody_can_delete_a_provider(self):
		permissions = frappe.get_meta("Grievance Service Provider").permissions
		self.assertFalse(
			[p.role for p in permissions if p.delete], "providers are deactivated, never deleted"
		)
