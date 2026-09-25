# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.grievance_masters.doctype.grievance_submitter_profile.grievance_submitter_profile import (
	build_dedupe_key,
	split_dedupe_key,
)


class TestSubmitterProfile(FrappeTestCase):
	def test_dedupe_key_helpers(self):
		self.assertEqual(build_dedupe_key("fayda", "123456"), "fayda:123456")
		self.assertEqual(build_dedupe_key("phone", "+251911000000"), "phone:+251911000000")
		self.assertEqual(split_dedupe_key("fayda:123456"), ("fayda", "123456"))
		self.assertEqual(split_dedupe_key("phone:+251911000000"), ("phone", "+251911000000"))
		self.assertEqual(split_dedupe_key("invalid"), (None, None))

	def test_derive_dedupe_key_automatic_resolution(self):
		from oan_grievance_service.services.identity import derive_dedupe_key

		# Farmer with Fayda ID
		self.assertEqual(
			derive_dedupe_key("Individual Farmer", mobile="+251911000000", fayda_id="1234567890123456"),
			"fayda:1234567890123456",
		)
		# Farmer without Fayda ID (falls back to phone)
		self.assertEqual(
			derive_dedupe_key("Individual Farmer", mobile="+251911000000"),
			"phone:+251911000000",
		)
		# Cooperative with registration number
		self.assertEqual(
			derive_dedupe_key("Cooperative", mobile="+251911000000", registration_number="COOP-REG-100"),
			"org:COOP-REG-100",
		)
		# Cooperative without registration number raises ValidationError
		with self.assertRaises(frappe.ValidationError):
			derive_dedupe_key("Cooperative", mobile="+251911000000")

	def test_profile_dedupe_key_generation(self):
		profile = frappe.new_doc("Grievance Submitter Profile")
		profile.submitter_type = "Individual Farmer"
		profile.submitter_name = "Test Farmer"
		profile.contact_mobile = "+251911223344"
		profile.administrative_unit = "Bishoftu"
		profile.validate()

		self.assertEqual(profile.dedupe_key, "phone:+251911223344")
		self.assertEqual(profile.identity_scheme, "phone")
		self.assertEqual(profile.identity_value, "+251911223344")

	def test_profile_custom_fayda_dedupe_key(self):
		profile = frappe.new_doc("Grievance Submitter Profile")
		profile.submitter_type = "Individual Farmer"
		profile.submitter_name = "Test Farmer"
		profile.contact_mobile = "+251911223344"
		profile.dedupe_key = "fayda:1234567890123456"
		profile.administrative_unit = "Bishoftu"
		profile.validate()

		self.assertEqual(profile.dedupe_key, "fayda:1234567890123456")
		self.assertEqual(profile.identity_scheme, "fayda")
		self.assertEqual(profile.identity_value, "1234567890123456")

	def test_on_user_registered_creates_individual_farmer_profile(self):
		import random

		from oan_grievance_service.services.hooks_handlers import on_user_registered

		random_mobile = f"+25191{random.randint(1000000, 9999999)}"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"test_farmer_{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Abebe",
				"last_name": "Bikila",
				"mobile_no": random_mobile,
				"roles": [{"role": "Grievance Submitter"}],
			}
		).insert(ignore_permissions=True)

		try:
			profile = on_user_registered(
				user_doc=user,
				role="Grievance Submitter",
				roles=["Grievance Submitter"],
				submitter_type="Individual Farmer",
				preferred_language="am",
				administrative_unit="Bishoftu",
			)

			self.assertIsNotNone(profile)
			self.assertEqual(profile.user, user.name)
			self.assertEqual(profile.submitter_type, "Individual Farmer")
			self.assertEqual(profile.submitter_name, "Abebe Bikila")
			self.assertEqual(profile.contact_mobile, random_mobile)
			self.assertEqual(profile.administrative_unit, "Bishoftu")
			self.assertEqual(profile.dedupe_key, f"phone:{random_mobile}")
		finally:
			frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)

	def test_on_user_registered_creates_cooperative_profile_with_org_key(self):
		import random

		from oan_grievance_service.services.hooks_handlers import on_user_registered

		random_mobile = f"+25191{random.randint(1000000, 9999999)}"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"test_coop_{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Oromia Seed",
				"last_name": "Coop",
				"mobile_no": random_mobile,
				"roles": [{"role": "Grievance Submitter"}],
			}
		).insert(ignore_permissions=True)

		try:
			profile = on_user_registered(
				user_doc=user,
				role="Grievance Submitter",
				roles=["Grievance Submitter"],
				submitter_type="Cooperative",
				registration_number="COOP-REG-987",
				administrative_unit="Bishoftu",
			)

			self.assertIsNotNone(profile)
			self.assertEqual(profile.user, user.name)
			self.assertEqual(profile.submitter_type, "Cooperative")
			self.assertEqual(profile.dedupe_key, "org:COOP-REG-987")
			self.assertEqual(profile.administrative_unit, "Bishoftu")
		finally:
			frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)

	def test_on_user_registered_cooperative_without_reg_number_fails(self):
		from oan_grievance_service.services.hooks_handlers import on_user_registered

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"test_coop_fail_{frappe.generate_hash(length=6)}@example.com",
				"first_name": "No Reg",
				"last_name": "Coop",
				"mobile_no": "+251911998811",
				"roles": [{"role": "Grievance Submitter"}],
			}
		).insert(ignore_permissions=True)

		try:
			with self.assertRaises(frappe.ValidationError):
				on_user_registered(
					user_doc=user,
					role="Grievance Submitter",
					roles=["Grievance Submitter"],
					submitter_type="Cooperative",
				)
		finally:
			frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)

	def test_on_user_registered_ignored_for_other_roles(self):
		from oan_grievance_service.services.hooks_handlers import on_user_registered

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"test_officer_{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Grievance",
				"last_name": "Officer",
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)

		try:
			profile = on_user_registered(
				user_doc=user,
				role="Grievance Officer",
				roles=["Grievance Officer"],
				submitter_type="Individual Farmer",
			)

			self.assertIsNone(profile)
			self.assertFalse(frappe.db.exists("Grievance Submitter Profile", {"user": user.name}))
		finally:
			frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)

	def test_end_to_end_auth_registration_creates_submitter_profile(self):
		from oan_auth_service.api.v1.auth import register_user
		from oan_auth_service.tests.utils import configured_keys, override_conf

		with configured_keys(), override_conf(jwt_self_registerable_roles=["Grievance Submitter"]):
			import random

			uid = frappe.generate_hash(length=6)
			phone = "+251911" + "".join(random.choices("0123456789", k=6))
			email = f"farmer_e2e_{uid}@example.com"
			fayda_id = "1" + "".join(random.choices("0123456789", k=15))
			res = register_user(
				email=email,
				password="SecurePassword123!",
				full_name=f"Fatuma Roba {uid}",
				phone_number=phone,
				role="Grievance Submitter",
				submitter_type="Individual Farmer",
				fayda_id=fayda_id,
				administrative_unit="Bishoftu",
				preferred_language="am",
			)

			self.assertEqual(res["status"], "success")
			user_id = res["data"]["user"]

			try:
				profile_name = frappe.db.get_value("Grievance Submitter Profile", {"user": user_id}, "name")
				self.assertTrue(bool(profile_name))

				profile = frappe.get_doc("Grievance Submitter Profile", profile_name)
				self.assertEqual(profile.submitter_name, f"Fatuma Roba {uid}")
				self.assertEqual(profile.contact_mobile, phone)
				self.assertEqual(profile.administrative_unit, "Bishoftu")
				self.assertEqual(profile.dedupe_key, f"fayda:{fayda_id}")
			finally:
				profile_name = frappe.db.get_value("Grievance Submitter Profile", {"user": user_id}, "name")
				if profile_name:
					frappe.delete_doc(
						"Grievance Submitter Profile", profile_name, force=True, ignore_permissions=True
					)
				if frappe.db.exists("User", user_id):
					frappe.db.delete("OAN User Refresh Token", {"user": user_id})
					contacts = frappe.get_all(
						"Dynamic Link", filters={"link_doctype": "User", "link_name": user_id}, pluck="parent"
					)
					for c in contacts:
						if frappe.db.exists("Contact", c):
							frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
					frappe.delete_doc("User", user_id, force=True, ignore_permissions=True)

	def test_profile_resolution_hook_returns_decomposed_variables(self):
		from oan_grievance_service.api.v1.profile import resolve_user_profile_hook

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"test_me_farmer_{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Derartu",
				"last_name": "Tulu",
				"roles": [{"role": "Grievance Submitter"}],
			}
		).insert(ignore_permissions=True)

		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"user": user.name,
				"submitter_type": "Individual Farmer",
				"submitter_name": "Derartu Tulu",
				"contact_mobile": "+251911445566",
				"dedupe_key": "fayda:1122334455667788",
				"administrative_unit": "Bekoji",
			}
		).insert(ignore_permissions=True)

		try:
			namespace, data = resolve_user_profile_hook(user, roles=["Grievance Submitter"])
			self.assertEqual(namespace, "grievance")
			self.assertEqual(data["profile_id"], profile.name)
			self.assertEqual(data["type"], "Individual Farmer")
			self.assertEqual(data["identities"], [{"scheme": "fayda", "value": "1122334455667788"}])
			self.assertEqual(data["administrative_unit"], "Bekoji")
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc(
				"Grievance Submitter Profile", profile.name, force=True, ignore_permissions=True
			)
			frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)

	def test_submitter_options_returns_phone_extensions(self):
		from oan_grievance_service.api.v1.submitter import options

		res = options()
		self.assertIn("data", res)
		data = res["data"]
		self.assertIn("phone_extensions", data)
		extensions = data["phone_extensions"]
		self.assertTrue(len(extensions) > 0)

		# Ensure Ethiopia is first
		self.assertEqual(extensions[0]["country"], "Ethiopia")
		self.assertEqual(extensions[0]["isd"], "+251")
		self.assertEqual(extensions[0]["code"], "ET")

		# Ensure public fields are returned
		self.assertIn("submitter_types", data)
		self.assertIn("submission_types", data)
		self.assertIn("preferred_languages", data)
		self.assertIn("service_categories", data)
		category_names = [c["category_name"] for c in data["service_categories"]]
		self.assertIn("Inputs", category_names)
		self.assertIn("grievance_types", data)

		# Ensure staff-only fields are NOT in public options
		self.assertNotIn("statuses", data)
		self.assertNotIn("departments", data)

	def test_submitter_options_filtering_parameters(self):
		from oan_grievance_service.api.v1.submitter import options

		# 1. Jurisdiction phone extensions (Ethiopia by default on this site)
		res = options()
		phones = res["data"]["phone_extensions"]
		self.assertGreaterEqual(len(phones), 1)
		self.assertEqual(phones[0]["country"], "Ethiopia")
		self.assertEqual(phones[0]["code"], "ET")
		self.assertEqual(phones[0]["isd"], "+251")
		self.assertNotIn("Kenya", [p["country"] for p in phones])

		# 2. Add test Grievance Types for category filtering test
		if not frappe.db.exists("Grievance Service Category", "Inputs"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Inputs",
					"code": "001",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Grievance Service Category", "Credit"):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": "Credit",
					"code": "CRDT",
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		gtype_inputs = frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "Test Input Shortage",
				"service_category": "Inputs",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)

		gtype_credit = frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "Test Credit Delay",
				"service_category": "Credit",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)

		try:
			# Exact country filter within jurisdiction
			res_et = options(country="ET")
			phones_et = res_et["data"]["phone_extensions"]
			self.assertEqual(len(phones_et), 1)
			self.assertEqual(phones_et[0]["code"], "ET")
			self.assertEqual(phones_et[0]["isd"], "+251")

			# Non-jurisdiction country filter returns empty
			res_ken = options(country="KE")
			self.assertEqual(res_ken["data"]["phone_extensions"], [])

			# Search filter
			res_search = options(search_country="eth")
			phones_search = res_search["data"]["phone_extensions"]
			self.assertEqual(len(phones_search), 1)
			self.assertEqual(phones_search[0]["country"], "Ethiopia")

			# Service Category filter on grievance types
			res_cat = options(service_category="Inputs")
			gtype_cats = {gt["service_category"] for gt in res_cat["data"]["grievance_types"]}
			self.assertEqual(gtype_cats, {"Inputs"})
		finally:
			frappe.delete_doc("Grievance Type", gtype_inputs.name, force=True, ignore_permissions=True)
			frappe.delete_doc("Grievance Type", gtype_credit.name, force=True, ignore_permissions=True)

		# 4. Omit phone extensions
		res_no_phones = options(include_phone_extensions=False)
		self.assertNotIn("phone_extensions", res_no_phones["data"])
		self.assertIn("submitter_types", res_no_phones["data"])
		self.assertIn("service_categories", res_no_phones["data"])
