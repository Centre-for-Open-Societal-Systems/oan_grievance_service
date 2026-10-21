# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.setup.install import (
	A2C_BANKS_SOURCE,
	seed_service_provider_sources,
	seed_service_provider_types,
)
from oan_grievance_service.tests.fixtures import a_service_provider, a_service_provider_source

DOCTYPE = "Grievance Service Provider Source"


class TestGrievanceServiceProviderSource(FrappeTestCase):
	def setUp(self):
		self.tag = frappe.generate_hash(length=6)

	def test_a_complete_source_can_be_enabled_and_keeps_its_secret_out_of_the_row(self):
		name = a_service_provider_source(f"Src {self.tag}")
		source = frappe.get_doc(DOCTYPE, name)
		self.assertEqual(source.is_enabled, 1)
		self.assertTrue(source.has_credentials())
		self.assertEqual(source.get_password("api_password"), "not-a-real-password")
		# The Password field stores asterisks in the row; the secret itself is encrypted elsewhere.
		self.assertNotIn("not-a-real-password", frappe.db.get_value(DOCTYPE, name, "api_password"))

	def test_the_base_url_must_be_https(self):
		for url in ("http://a2c.example.test/api", "a2c.example.test/api", "ftp://a2c.example.test"):
			with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
				a_service_provider_source(f"Src {self.tag}", is_enabled=0, api_base_url=url)

	def test_the_base_url_cannot_carry_credentials_or_a_query(self):
		for url in (
			"https://user:pass@a2c.example.test/api",
			"https://a2c.example.test/api?token=1",
			"https://a2c.example.test/api#x",
		):
			with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
				a_service_provider_source(f"Src {self.tag}", is_enabled=0, api_base_url=url)

	def test_a_trailing_slash_is_dropped_so_paths_join_cleanly(self):
		name = a_service_provider_source(f"Src {self.tag}", api_base_url="https://a2c.example.test/api/")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "api_base_url"), "https://a2c.example.test/api")

	def test_paths_must_start_with_a_slash(self):
		with self.assertRaises(frappe.ValidationError):
			a_service_provider_source(f"Src {self.tag}", list_path="v1/catalog/facets")
		with self.assertRaises(frappe.ValidationError):
			a_service_provider_source(f"Src {self.tag}", login_path="v1/auth/login")

	def test_an_incomplete_source_cannot_be_enabled(self):
		for overrides in (
			{"api_base_url": ""},
			{"api_username": ""},
			{"list_key": ""},
			{"api_password": ""},
		):
			with self.subTest(overrides=overrides), self.assertRaises(frappe.ValidationError):
				a_service_provider_source(f"Src {self.tag}", **overrides)

	def test_an_incomplete_source_can_be_saved_while_disabled(self):
		name = a_service_provider_source(
			f"Src {self.tag}", is_enabled=0, api_base_url="", api_username="", api_password=""
		)
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "is_enabled"), 0)

	def test_token_mode_needs_a_token_not_a_login(self):
		name = a_service_provider_source(
			f"Src {self.tag}",
			auth_mode="Token",
			api_token="a-long-lived-token",
			api_username="",
			api_password="",
		)
		self.assertTrue(frappe.get_doc(DOCTYPE, name).has_credentials())
		with self.assertRaises(frappe.ValidationError):
			a_service_provider_source(f"Src2 {self.tag}", auth_mode="Token", api_username="", api_password="")

	def test_the_source_name_is_unique(self):
		a_service_provider_source(f"Src {self.tag}")
		with self.assertRaises(frappe.DuplicateEntryError):
			a_service_provider_source(f"Src {self.tag}")

	def test_the_provider_type_is_a_link_to_the_type_master_not_a_typed_in_list(self):
		field = frappe.get_meta(DOCTYPE).get_field("default_provider_type")
		self.assertEqual((field.fieldtype, field.options), ("Link", "Grievance Service Provider Type"))

	def test_the_provider_type_must_exist_and_be_active(self):
		with self.assertRaises(frappe.ValidationError):
			a_service_provider_source(f"Src {self.tag}", default_provider_type="Pawn Shop")

		retired = frappe.get_doc(
			{"doctype": "Grievance Service Provider Type", "type_name": f"Old {self.tag}", "is_active": 0}
		).insert()
		with self.assertRaises(frappe.ValidationError):
			a_service_provider_source(f"Src {self.tag}", default_provider_type=retired.name)

	def test_a_source_can_give_its_providers_any_type_the_master_holds(self):
		seed_service_provider_types()
		name = a_service_provider_source(f"Src {self.tag}", default_provider_type="Cooperative")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "default_provider_type"), "Cooperative")

	def test_nobody_can_delete_a_source(self):
		self.assertFalse([p.role for p in frappe.get_meta(DOCTYPE).permissions if p.delete])

	def _fresh_seed(self):
		"""Seed from scratch: the dev site already holds the record, perhaps configured."""
		frappe.delete_doc(DOCTYPE, A2C_BANKS_SOURCE, force=1, ignore_permissions=True)
		self.assertEqual(seed_service_provider_sources(), [A2C_BANKS_SOURCE])

	def test_the_seeded_a2c_source_is_disabled_and_has_no_connection(self):
		self._fresh_seed()
		source = frappe.get_doc(DOCTYPE, A2C_BANKS_SOURCE)
		self.assertEqual(source.is_enabled, 0)
		self.assertFalse(source.api_base_url)
		self.assertFalse(source.has_credentials())
		self.assertEqual(
			(source.list_path, source.list_key, source.map_external_id, source.map_name, source.map_area),
			("/v1/catalog/facets", "banks", "name", "bank_name", "registered_region"),
		)

	def test_seeding_again_keeps_what_the_site_configured(self):
		self._fresh_seed()
		frappe.db.set_value(DOCTYPE, A2C_BANKS_SOURCE, "api_base_url", "https://configured.example.test/api")
		self.assertEqual(seed_service_provider_sources(), [])
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, A2C_BANKS_SOURCE, "api_base_url"),
			"https://configured.example.test/api",
		)

	def test_a_synced_provider_only_lets_an_admin_change_whether_it_is_active(self):
		source = a_service_provider_source(f"Src {self.tag}")
		name = a_service_provider(
			f"Synced {self.tag}", provider_type="Bank", source=source, external_ref=f"PB-{self.tag}"
		)
		doc = frappe.get_doc("Grievance Service Provider", name)
		doc.provider_name = "Renamed by hand"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

		doc = frappe.get_doc("Grievance Service Provider", name)
		doc.is_active = 0
		doc.save()
		self.assertEqual(frappe.db.get_value("Grievance Service Provider", name, "is_active"), 0)

	def test_the_same_source_cannot_list_a_provider_twice(self):
		source = a_service_provider_source(f"Src {self.tag}")
		a_service_provider(f"One {self.tag}", source=source, external_ref="PB-1")
		with self.assertRaises(frappe.UniqueValidationError):
			a_service_provider(f"Two {self.tag}", source=source, external_ref="PB-1")

	def test_hand_made_providers_never_collide_on_the_source_columns(self):
		a_service_provider(f"Manual A {self.tag}")
		a_service_provider(f"Manual B {self.tag}")
