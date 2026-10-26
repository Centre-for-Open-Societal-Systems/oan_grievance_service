# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""The `oan_auth_on_delegated_login` hook: a Submitter Profile for an IAM (eSignet) sign-in.

An IAM user never goes through `register_user`, so this hook is what gives a farmer who
signs in with their national ID the profile that registration would have created.
"""

import secrets

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.profile import on_delegated_login_hook, resolve_user_profile_hook
from oan_grievance_service.permissions import ROLE_OFFICER

LOGIN_OPTION = "beneficiary"


def _fayda() -> str:
	return "".join(secrets.choice("0123456789") for _ in range(16))


def _phone() -> str:
	return "+25191" + "".join(secrets.choice("0123456789") for _ in range(7))


def _principal(**overrides) -> dict:
	principal = {
		"sub": frappe.generate_hash(length=16),
		"name": "Abebe Kebede",
		"individual_id": _fayda(),
		"phone_number": _phone(),
	}
	principal.update(overrides)
	return {k: v for k, v in principal.items() if v is not ...}


class TestDelegatedLoginProfile(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.db.savepoint("delegated_login_test")
		self.addCleanup(frappe.db.rollback, save_point="delegated_login_test")

	def _user(self, *roles: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "User",
					"email": f"{frappe.generate_hash(length=12)}@id.openagrinet.internal",
					"first_name": "Abebe",
					"last_name": "Kebede",
					"send_welcome_email": 0,
					"roles": [{"role": r} for r in roles],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _profile(self, user: str):
		name = frappe.db.get_value("Grievance Submitter Profile", {"user": user}, "name")
		return frappe.get_doc("Grievance Submitter Profile", name) if name else None

	def test_submitter_gets_a_fayda_anchored_profile_from_the_claims(self):
		user = self._user("Grievance Submitter")
		principal = _principal()

		self.assertTrue(on_delegated_login_hook(user=user, principal=principal, login_option=LOGIN_OPTION))

		profile = self._profile(user)
		self.assertEqual(profile.submitter_type, "Individual Farmer")
		self.assertEqual(profile.submitter_name, "Abebe Kebede")
		self.assertEqual(profile.dedupe_key, f"fayda:{principal['individual_id']}")
		self.assertEqual(profile.submission_channel, "Web Portal")

	def test_a_second_login_changes_nothing(self):
		user = self._user("Grievance Submitter")
		on_delegated_login_hook(user=user, principal=_principal(), login_option=LOGIN_OPTION)

		self.assertFalse(
			on_delegated_login_hook(user=user, principal=_principal(), login_option=LOGIN_OPTION)
		)
		self.assertEqual(frappe.db.count("Grievance Submitter Profile", {"user": user}), 1)

	def test_an_individual_id_that_is_not_a_fayda_id_is_left_out(self):
		user = self._user("Grievance Submitter")
		principal = _principal(individual_id="ABC123")

		self.assertTrue(on_delegated_login_hook(user=user, principal=principal, login_option=LOGIN_OPTION))
		self.assertFalse(self._profile(user).dedupe_key.startswith("fayda:"))

	def test_address_claims_fill_the_identity_section_and_match_the_region(self):
		user = self._user("Grievance Submitter")
		principal = _principal(
			address={
				"street_address": "Kebele 01",
				"locality": "Hawassa",
				"region": "Sidama",
				"postal_code": "1000",
				"country": "Ethiopia",
			}
		)

		on_delegated_login_hook(user=user, principal=principal, login_option=LOGIN_OPTION)

		profile = self._profile(user)
		self.assertEqual(profile.id_source, LOGIN_OPTION)
		self.assertEqual(profile.street_address, "Kebele 01")
		self.assertEqual(profile.region, "Sidama")
		self.assertEqual(profile.administrative_unit, "Hawassa")
		self.assertEqual(
			profile.administrative_area,
			frappe.db.get_value(
				"Grievance Administrative Area", {"area_name": "Sidama", "level_name": "Region"}
			),
		)

	def test_a_region_that_matches_no_area_leaves_the_area_unset(self):
		user = self._user("Grievance Submitter")
		principal = _principal(address={"region": "Atlantis", "locality": "Deep"})

		on_delegated_login_hook(user=user, principal=principal, login_option=LOGIN_OPTION)

		profile = self._profile(user)
		self.assertEqual(profile.region, "Atlantis")
		self.assertFalse(profile.administrative_area)

	def test_a_later_login_refreshes_the_address_of_an_existing_profile(self):
		user = self._user("Grievance Submitter")
		on_delegated_login_hook(
			user=user, principal=_principal(address={"locality": "Hawassa"}), login_option=LOGIN_OPTION
		)

		changed = on_delegated_login_hook(
			user=user, principal=_principal(address={"locality": "Adama"}), login_option=LOGIN_OPTION
		)

		self.assertTrue(changed)
		self.assertEqual(self._profile(user).locality, "Adama")

	def test_the_identity_section_is_returned_in_the_profile_data(self):
		user = self._user("Grievance Submitter")
		on_delegated_login_hook(
			user=user,
			principal=_principal(address={"locality": "Hawassa", "region": "Sidama"}),
			login_option=LOGIN_OPTION,
		)

		_namespace, data = resolve_user_profile_hook(frappe.get_doc("User", user))

		self.assertEqual(data["id_source"], LOGIN_OPTION)
		self.assertEqual(data["address"]["locality"], "Hawassa")
		self.assertEqual(data["address"]["region"], "Sidama")

	def test_officer_gets_no_profile(self):
		user = self._user("Grievance Submitter", ROLE_OFFICER)

		self.assertFalse(
			on_delegated_login_hook(user=user, principal=_principal(), login_option=LOGIN_OPTION)
		)
		self.assertIsNone(self._profile(user))

	def test_user_without_the_submitter_role_gets_no_profile(self):
		user = self._user()

		self.assertFalse(
			on_delegated_login_hook(user=user, principal=_principal(), login_option=LOGIN_OPTION)
		)
		self.assertIsNone(self._profile(user))

	def test_claims_without_a_phone_number_skip_the_profile_without_refusing_the_login(self):
		user = self._user("Grievance Submitter")

		self.assertFalse(
			on_delegated_login_hook(
				user=user, principal=_principal(phone_number=...), login_option=LOGIN_OPTION
			)
		)
		self.assertIsNone(self._profile(user))

	def test_a_fayda_id_already_taken_skips_the_profile_without_refusing_the_login(self):
		fayda = _fayda()
		first = self._user("Grievance Submitter")
		on_delegated_login_hook(
			user=first, principal=_principal(individual_id=fayda), login_option=LOGIN_OPTION
		)
		second = self._user("Grievance Submitter")

		self.assertFalse(
			on_delegated_login_hook(
				user=second, principal=_principal(individual_id=fayda), login_option=LOGIN_OPTION
			)
		)
		self.assertIsNone(self._profile(second))

	def test_db_deadlock_or_exception_skips_profile_without_refusing_login(self):
		user = self._user("Grievance Submitter")
		from unittest.mock import patch

		with patch(
			"oan_grievance_service.api.v1.submitter._create_or_update_submitter_profile",
			side_effect=frappe.QueryDeadlockError("Deadlock found when trying to get lock"),
		):
			self.assertFalse(
				on_delegated_login_hook(user=user, principal=_principal(), login_option=LOGIN_OPTION)
			)
		self.assertIsNone(self._profile(user))

	def test_profile_with_region_area_submits_grievance_with_woreda_area(self):
		from oan_grievance_service.tests.fixtures import a_grievance

		user = self._user("Grievance Submitter")
		principal = _principal(
			address={
				"street_address": "Kebele 01",
				"locality": "Hawassa",
				"region": "Sidama",
				"postal_code": "1000",
				"country": "Ethiopia",
			}
		)
		on_delegated_login_hook(user=user, principal=principal, login_option=LOGIN_OPTION)
		profile = self._profile(user)
		self.assertTrue(profile.administrative_area)

		g = a_grievance(
			submitter=profile.name,
			submitter_type=profile.submitter_type,
			submitter_name=profile.submitter_name,
			contact_mobile=profile.contact_mobile,
		)
		self.assertTrue(g.administrative_area)
		self.assertNotEqual(g.administrative_area, profile.administrative_area)
