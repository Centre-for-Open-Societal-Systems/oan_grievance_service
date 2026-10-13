# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""STG-456: submitter type, submission channel and Fayda ID at registration.

Covers POST /api/v1/auth/register through the on_user_registered hook, the three keys under
profiles.grievance in GET /api/v1/auth/me, the helper's insert/update rules for the channel,
the backfill patch, and the channels offered by GET /api/v1/submitters/options.
"""

import secrets

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.v1.auth import get_me, register_user
from oan_auth_service.setup.install import LOGIN_EMAIL_FIELD
from oan_auth_service.tests.utils import configured_keys, override_conf

from oan_grievance_service.api.v1._options import get_submission_types
from oan_grievance_service.api.v1.submitter import _create_or_update_submitter_profile

PASSWORD = "SecurePassword123!"


def _fayda() -> str:
	return "".join(secrets.choice("0123456789") for _ in range(16))


def _national_phone() -> str:
	"""A fresh Ethiopian mobile number without the country code (9 digits, 91 prefix)."""
	return "91" + "".join(secrets.choice("0123456789") for _ in range(7))


def _email() -> str:
	return f"stg456_{frappe.generate_hash(length=8)}@example.com"


def _register(**overrides):
	"""POST /auth/register as a guest, with the optional fields given as keywords."""
	payload = {
		"email": _email(),
		"password": PASSWORD,
		"full_name": "Registration Fields Farmer",
		"country_code": "+251",
		"phone_number": _national_phone(),
		"role": "Grievance Submitter",
		"fayda_id": _fayda(),
	}
	payload.update(overrides)
	payload = {k: v for k, v in payload.items() if v is not ...}
	with configured_keys(), override_conf(jwt_self_registerable_roles=["Grievance Submitter"]):
		res = register_user(**payload)
	return payload, res


def _user_for(email: str) -> str | None:
	return frappe.db.get_value("User", {LOGIN_EMAIL_FIELD: email}, "name")


def _forget(email: str):
	"""Remove what a successful registration committed, so the next run starts clean."""
	frappe.set_user("Administrator")
	user = _user_for(email)
	if not user:
		return
	for profile in frappe.get_all(
		"Grievance Submitter Profile", filters={"user": user}, pluck="name"
	):
		frappe.delete_doc(
			"Grievance Submitter Profile", profile, force=True, ignore_permissions=True
		)
	frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit


class TestRegistrationSubmitterFields(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_register_stores_type_fayda_and_channel_and_me_returns_them(self):
		fayda = _fayda()
		payload, res = _register(
			fayda_id=fayda, submitter_type="Individual Farmer", submission_channel="Mobile App"
		)
		try:
			self.assertEqual(res["status"], "success")
			user = _user_for(payload["email"])
			profile = frappe.get_doc(
				"Grievance Submitter Profile",
				frappe.db.get_value("Grievance Submitter Profile", {"user": user}),
			)
			self.assertEqual(profile.submitter_type, "Individual Farmer")
			self.assertEqual(profile.submission_channel, "Mobile App")
			self.assertEqual(profile.dedupe_key, f"fayda:{fayda}")

			frappe.set_user(user)
			grievance = get_me()["data"]["profiles"]["grievance"]
			self.assertEqual(grievance["type"], "Individual Farmer")
			self.assertEqual(grievance["submitter_type"], "Individual Farmer")
			self.assertEqual(grievance["submission_channel"], "Mobile App")
			self.assertEqual(grievance["fayda_id"], fayda)
			self.assertEqual(grievance["identities"], [{"scheme": "fayda", "value": fayda}])
			# The keys that existed before the change are still there.
			for key in (
				"profile_id",
				"submitter_name",
				"contact_mobile",
				"country_code",
				"phone_number",
				"contact_email",
				"administrative_area",
				"administrative_unit",
			):
				self.assertIn(key, grievance)
		finally:
			_forget(payload["email"])

	def test_register_without_channel_defaults_to_web_portal(self):
		payload, res = _register()
		try:
			self.assertEqual(res["status"], "success")
			user = _user_for(payload["email"])
			self.assertEqual(
				frappe.db.get_value(
					"Grievance Submitter Profile", {"user": user}, "submission_channel"
				),
				"Web Portal",
			)
			frappe.set_user(user)
			self.assertEqual(
				get_me()["data"]["profiles"]["grievance"]["submission_channel"], "Web Portal"
			)
		finally:
			_forget(payload["email"])

	def test_register_with_blank_channel_defaults_to_web_portal(self):
		payload, res = _register(submission_channel="   ")
		try:
			self.assertEqual(res["status"], "success")
			user = _user_for(payload["email"])
			self.assertEqual(
				frappe.db.get_value(
					"Grievance Submitter Profile", {"user": user}, "submission_channel"
				),
				"Web Portal",
			)
		finally:
			_forget(payload["email"])

	def test_invalid_channel_is_rejected_and_creates_no_user(self):
		payload, res = _register(submission_channel="Carrier Pigeon")
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertIn("Carrier Pigeon", res["message"])
		self.assertIsNone(_user_for(payload["email"]))

	def test_inactive_channel_is_rejected_and_creates_no_user(self):
		frappe.db.set_value("Grievance Submission Type", "IVR Helpline", "is_active", 0)
		try:
			payload, res = _register(submission_channel="IVR Helpline")
			self.assertEqual(res["status"], "error")
			self.assertEqual(frappe.response["http_status_code"], 400)
			self.assertIsNone(_user_for(payload["email"]))
		finally:
			frappe.db.set_value("Grievance Submission Type", "IVR Helpline", "is_active", 1)

	def test_unknown_submitter_type_is_rejected_and_creates_no_user(self):
		payload, res = _register(submitter_type="Space Farmer")
		self.assertEqual(res["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertIn("Space Farmer", res["message"])
		self.assertIsNone(_user_for(payload["email"]))

	def test_individual_farmer_without_fayda_is_rejected_and_creates_no_user(self):
		payload, res = _register(fayda_id=...)
		self.assertEqual(res["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertIn("Fayda", res["message"])
		self.assertIsNone(_user_for(payload["email"]))

	def test_malformed_fayda_is_rejected_and_creates_no_user(self):
		for bad in ("12345", "12345678901234ab", "12345678901234567"):
			payload, res = _register(fayda_id=bad)
			self.assertEqual(res["status"], "error", bad)
			self.assertEqual(frappe.response["http_status_code"], 400, bad)
			self.assertIsNone(_user_for(payload["email"]), bad)

	def test_numeric_fayda_is_reported_as_a_validation_error_not_a_crash(self):
		payload, res = _register(fayda_id=1234567890)
		self.assertEqual(res["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 400)
		self.assertIsNone(_user_for(payload["email"]))

	def test_explicit_dedupe_key_cannot_replace_a_farmers_fayda(self):
		fayda = _fayda()
		payload, res = _register(fayda_id=fayda, dedupe_key="phone:+251911000099")
		try:
			self.assertEqual(res["status"], "success")
			user = _user_for(payload["email"])
			self.assertEqual(
				frappe.db.get_value("Grievance Submitter Profile", {"user": user}, "dedupe_key"),
				f"fayda:{fayda}",
			)
		finally:
			_forget(payload["email"])

	def test_duplicate_fayda_is_rejected_and_creates_no_second_user(self):
		fayda = _fayda()
		first, first_res = _register(fayda_id=fayda)
		try:
			self.assertEqual(first_res["status"], "success")
			second, second_res = _register(fayda_id=fayda)
			self.assertEqual(second_res["status"], "error")
			self.assertEqual(second_res["code"], "DUPLICATE_ENTRY")
			self.assertEqual(frappe.response["http_status_code"], 409)
			self.assertIsNone(_user_for(second["email"]))
			# The first registration is untouched.
			self.assertIsNotNone(_user_for(first["email"]))
			self.assertEqual(
				frappe.db.count("Grievance Submitter Profile", {"dedupe_key": f"fayda:{fayda}"}), 1
			)
		finally:
			_forget(first["email"])

	def test_non_farmer_registration_does_not_need_a_fayda_id(self):
		payload, res = _register(
			fayda_id=...,
			submitter_type="Cooperative",
			registration_number=f"COOP-{secrets.randbelow(10**6)}",
		)
		try:
			self.assertEqual(res["status"], "success")
			user = _user_for(payload["email"])
			profile = frappe.db.get_value(
				"Grievance Submitter Profile",
				{"user": user},
				["submitter_type", "submission_channel"],
				as_dict=True,
			)
			self.assertEqual(profile.submitter_type, "Cooperative")
			self.assertEqual(profile.submission_channel, "Web Portal")
			frappe.set_user(user)
			grievance = get_me()["data"]["profiles"]["grievance"]
			self.assertIsNone(grievance["fayda_id"])
			self.assertEqual(grievance["identities"][0]["scheme"], "org")
		finally:
			_forget(payload["email"])


class IsolatedTestCase(FrappeTestCase):
	"""Undo each test's writes. FrappeTestCase only rolls back once per class, so a test that
	deactivates a channel would otherwise break the ones after it."""

	def setUp(self):
		super().setUp()
		frappe.db.savepoint("stg456_test")
		self.addCleanup(frappe.db.rollback, save_point="stg456_test")


class TestSubmissionChannelOnProfile(IsolatedTestCase):
	def setUp(self):
		super().setUp()
		self.user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"{frappe.generate_hash(length=12)}@id.openagrinet.internal",
				"first_name": "Channel",
				"last_name": "Tester",
				"mobile_no": "+251" + _national_phone(),
				"send_welcome_email": 0,
				"roles": [{"role": "Grievance Submitter"}],
			}
		).insert(ignore_permissions=True)

	def _save(self, **kwargs):
		return _create_or_update_submitter_profile(
			user=self.user.name,
			submitter_type="Individual Farmer",
			fayda_id=kwargs.pop("fayda_id", None),
			**kwargs,
		)

	def test_insert_without_channel_takes_the_default(self):
		self.assertEqual(self._save().submission_channel, "Web Portal")

	def test_insert_with_channel_stores_it(self):
		self.assertEqual(
			self._save(submission_channel="IVR Helpline").submission_channel, "IVR Helpline"
		)

	def test_channel_is_matched_to_its_canonical_name(self):
		self.assertEqual(
			self._save(submission_channel=" mobile app ").submission_channel, "Mobile App"
		)

	def test_update_keeps_a_stored_channel_when_none_is_sent(self):
		self._save(submission_channel="Mobile App")
		for unsent in (None, "", "  "):
			profile = self._save(submission_channel=unsent, administrative_unit="Adama")
			self.assertEqual(profile.submission_channel, "Mobile App", repr(unsent))
		self.assertEqual(
			frappe.db.get_value("Grievance Submitter Profile", profile.name, "submission_channel"),
			"Mobile App",
		)

	def test_update_with_a_channel_replaces_the_stored_one(self):
		self._save(submission_channel="Mobile App")
		self.assertEqual(
			self._save(submission_channel="Mobile Call").submission_channel, "Mobile Call"
		)

	def test_update_fills_a_profile_that_has_no_channel(self):
		profile = self._save(submission_channel="Mobile App")
		frappe.db.set_value(
			"Grievance Submitter Profile", profile.name, "submission_channel", None
		)
		self.assertEqual(self._save().submission_channel, "Web Portal")

	def test_invalid_channel_raises_a_clear_validation_error(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._save(submission_channel="Carrier Pigeon")
		self.assertIn("Carrier Pigeon", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Grievance Submitter Profile", {"user": self.user.name}))

	def test_inactive_channel_raises_a_clear_validation_error(self):
		frappe.db.set_value("Grievance Submission Type", "Mobile Call", "is_active", 0)
		with self.assertRaises(frappe.ValidationError):
			self._save(submission_channel="Mobile Call")

	def test_inactive_default_channel_raises_a_clear_validation_error(self):
		frappe.db.set_value("Grievance Submission Type", "Web Portal", "is_active", 0)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._save()
		self.assertIn("Web Portal", str(ctx.exception))


class TestSubmissionChannelPatchesAndOptions(IsolatedTestCase):
	def test_backfill_patch_sets_web_portal_only_where_blank(self):
		from oan_grievance_service.patches.set_web_portal_on_existing_submitter_profiles import (
			execute,
		)

		def make(channel):
			profile = frappe.get_doc(
				{
					"doctype": "Grievance Submitter Profile",
					"submitter_type": "Individual Farmer",
					"submitter_name": "Backfill Farmer",
					"contact_mobile": "+251" + _national_phone(),
					"submission_channel": channel,
				}
			).insert(ignore_permissions=True)
			return profile.name

		legacy_null = make(None)
		legacy_blank = make(None)
		kept = make("Mobile App")
		frappe.db.set_value("Grievance Submitter Profile", legacy_blank, "submission_channel", "")

		execute()

		def channel(name):
			return frappe.db.get_value("Grievance Submitter Profile", name, "submission_channel")

		self.assertEqual(channel(legacy_null), "Web Portal")
		self.assertEqual(channel(legacy_blank), "Web Portal")
		self.assertEqual(channel(kept), "Mobile App")

	def test_channel_patch_creates_reactivates_and_codes_the_standard_channels(self):
		from oan_grievance_service.patches.ensure_standard_submission_channels import execute
		from oan_grievance_service.setup.install import SUBMISSION_TYPES

		frappe.db.set_value("Grievance Submission Type", "Web Portal", "is_active", 0)
		frappe.db.set_value("Grievance Submission Type", "Mobile App", "code", None)
		frappe.delete_doc(
			"Grievance Submission Type", "IVR Helpline", force=True, ignore_permissions=True
		)

		execute()

		for name, code in SUBMISSION_TYPES:
			row = frappe.db.get_value(
				"Grievance Submission Type", name, ["is_active", "code"], as_dict=True
			)
			self.assertIsNotNone(row, name)
			self.assertEqual(row.is_active, 1, name)
			self.assertEqual(row.code, code, name)

	def test_options_return_every_active_channel_with_type_name_and_code(self):
		frappe.db.set_value("Grievance Submission Type", "Mobile Call", "is_active", 0)
		channels = get_submission_types()
		names = {c["type_name"] for c in channels}

		self.assertNotIn("Mobile Call", names)
		self.assertTrue({"Web Portal", "Mobile App", "IVR Helpline"} <= names)
		for channel in channels:
			self.assertTrue(channel["type_name"])
			self.assertTrue(channel["code"], channel)
