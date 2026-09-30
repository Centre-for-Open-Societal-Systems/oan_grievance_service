# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Tests for Draft state persisted directly to Grievance doctype."""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api import middleware
from oan_grievance_service.api.v1 import draft
from oan_grievance_service.tests.fixtures import a_leaf_area, a_service_category


class TestDraftModuleLoads(FrappeTestCase):
	def test_the_module_imports(self):
		self.assertTrue(callable(draft.save))
		self.assertTrue(callable(draft.load))
		self.assertTrue(callable(draft.discard))
		self.assertTrue(callable(draft.submit_draft))

	def test_draft_endpoints_require_authentication(self):
		for name in ("save", "load", "submit_draft", "discard"):
			path = f"/api/method/oan_grievance_service.api.v1.draft.{name}"
			self.assertNotIn(path, middleware.EXEMPT_PATHS)

		for fn in (draft.save, draft.load, draft.submit_draft, draft.discard):
			self.assertIn(fn, frappe.whitelisted)
			self.assertNotIn(fn, frappe.guest_methods)


class TestDraftRoundTrip(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.uuid = frappe.generate_hash(length=20)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def _params(self, **overrides):
		values = {
			"submitter_name": "Test Submitter",
			"contact_mobile": "+251911234567",
			"description": "A description long enough to clear the twenty character minimum.",
		}
		values.update(overrides)
		return values

	def test_save_returns_the_standard_envelope(self):
		result = draft.save(client_submission_uuid=self.uuid, **self._params())
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["client_submission_uuid"], self.uuid)
		self.assertEqual(result["data"]["description"], self._params()["description"])

	def test_a_saved_draft_loads_back(self):
		draft.save(client_submission_uuid=self.uuid, **self._params())
		result = draft.load()

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["submitter_name"], "Test Submitter")
		self.assertEqual(result["data"]["contact_mobile"], "+251911234567")
		self.assertEqual(result["data"]["description"], self._params()["description"])

	def test_saving_again_overwrites_rather_than_merges(self):
		draft.save(client_submission_uuid=self.uuid, **self._params(description="First attempt text."))
		draft.save(client_submission_uuid=self.uuid, **self._params(description="Second attempt text."))

		result = draft.load()
		self.assertEqual(result["data"]["description"], "Second attempt text.")

	def test_a_draft_key_is_required(self):
		result = draft.save(client_submission_uuid="", **self._params())
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "VALIDATION_ERROR")

	def test_partial_fields_can_be_saved(self):
		result = draft.save(
			client_submission_uuid=self.uuid,
			description="still typing",
		)
		self.assertEqual(result["status"], "success")
		loaded = draft.load()
		self.assertEqual(loaded["data"]["description"], "still typing")

	def test_empty_params_can_be_saved(self):
		result = draft.save(client_submission_uuid=self.uuid)
		self.assertEqual(result["status"], "success")
		self.assertEqual(draft.load()["data"]["client_submission_uuid"], self.uuid)

	def test_logged_in_save_associates_owner_user(self):
		user = _a_submitter_user("draft.owner@example.com")
		frappe.set_user(user.name)

		result = draft.save(client_submission_uuid=self.uuid, **self._params())
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["owner"], user.name)
		self.assertEqual(
			frappe.db.get_value("Grievance", {"client_submission_uuid": self.uuid}, "owner"),
			user.name,
		)

	def test_guest_cannot_save_a_draft(self):
		frappe.set_user("Guest")
		result = draft.save(client_submission_uuid=self.uuid, **self._params())
		self.assertEqual(result["status"], "error")
		self.assertIn(frappe.response.get("http_status_code"), (401, 403))

	def test_another_user_cannot_overwrite_my_draft(self):
		owner = _a_submitter_user("draft.save.owner@example.com")
		frappe.set_user(owner.name)
		draft.save(client_submission_uuid=self.uuid, **self._params())

		other = _a_submitter_user("draft.save.other@example.com")
		frappe.set_user(other.name)
		result = draft.save(client_submission_uuid=self.uuid, **self._params(description="not mine"))
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 403)
		self.assertIn("another user", result["message"])

	def test_loading_with_no_draft_returns_success_with_none_data(self):
		other = _a_submitter_user("draft.empty@example.com")
		frappe.set_user(other.name)
		result = draft.load()
		self.assertEqual(result["status"], "success")
		self.assertIsNone(result["data"])

	def test_discarding_removes_it(self):
		draft.save(client_submission_uuid=self.uuid, **self._params())
		result = draft.discard(client_submission_uuid=self.uuid)

		self.assertTrue(result["data"]["discarded"])
		self.assertFalse(frappe.db.exists("Grievance", {"client_submission_uuid": self.uuid}))

	def test_discarding_nothing_succeeds_quietly(self):
		result = draft.discard(client_submission_uuid="never-saved-anything")
		self.assertEqual(result["status"], "success")
		self.assertFalse(result["data"]["discarded"])

	def test_a_submitted_draft_cannot_be_discarded(self):
		draft.save(client_submission_uuid=self.uuid, **self._params())
		name = frappe.db.get_value("Grievance", {"client_submission_uuid": self.uuid}, "name")
		frappe.db.set_value(
			"Grievance",
			name,
			{"workflow_state": "Submitted", "status": "Submitted", "docstatus": 1},
			update_modified=False,
		)

		result = draft.discard(client_submission_uuid=self.uuid)
		self.assertEqual(result["status"], "error")
		self.assertIn("cannot be discarded", result["message"])

	def test_a_submitted_draft_cannot_be_overwritten(self):
		draft.save(client_submission_uuid=self.uuid, **self._params())
		name = frappe.db.get_value("Grievance", {"client_submission_uuid": self.uuid}, "name")
		frappe.db.set_value(
			"Grievance",
			name,
			{"workflow_state": "Submitted", "status": "Submitted", "docstatus": 1},
			update_modified=False,
		)

		result = draft.save(client_submission_uuid=self.uuid, **self._params())
		self.assertEqual(result["status"], "error")
		self.assertIn("already been submitted", result["message"])

	def test_load_returns_saved_fields(self):
		area = a_leaf_area()
		category = a_service_category()
		gtype = frappe.db.get_value("Grievance Type", {"service_category": category, "is_active": 1}, "name")
		params = self._params(
			service_category=category,
			grievance_type=gtype,
			administrative_area=area,
			desired_outcome="Deliver the allocated fertilizer this week.",
		)
		draft.save(client_submission_uuid=self.uuid, **params)
		result = draft.load()

		data = result["data"]
		self.assertEqual(result["status"], "success")
		self.assertEqual(data["client_submission_uuid"], self.uuid)
		self.assertEqual(data["service_category"], category)
		self.assertEqual(data["grievance_type"], gtype)
		self.assertEqual(data["administrative_area"], area)
		self.assertEqual(data["desired_outcome"], "Deliver the allocated fertilizer this week.")
		self.assertEqual(data["contact_mobile"], "+251911234567")
		self.assertIn("attachments", data)
		self.assertEqual(data["attachment_count"], 0)
		self.assertEqual(data["attachments"], [])
		self.assertTrue(data["name"])

	def test_load_returns_the_latest_draft_only(self):
		older = frappe.generate_hash(length=20)
		newer = frappe.generate_hash(length=20)
		draft.save(
			client_submission_uuid=older,
			**self._params(description="Older draft text for resume."),
		)
		draft.save(
			client_submission_uuid=newer,
			**self._params(description="Newer draft text for resume."),
		)

		result = draft.load()
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["client_submission_uuid"], newer)
		self.assertEqual(result["data"]["description"], "Newer draft text for resume.")

	def test_another_user_never_receives_my_draft(self):
		draft.save(client_submission_uuid=self.uuid, **self._params())
		other = _a_submitter_user("draft.other@example.com")
		frappe.set_user(other.name)

		result = draft.load()
		self.assertEqual(result["status"], "success")
		self.assertIsNone(result["data"])

	def test_guest_cannot_load_a_draft(self):
		draft.save(client_submission_uuid=self.uuid, **self._params())
		frappe.set_user("Guest")

		result = draft.load()
		self.assertEqual(result["status"], "error")
		self.assertIn(frappe.response["http_status_code"], (401, 403))

	def test_submit_draft_end_to_end(self):
		"""Saving a draft and then submitting it via POST /api/v1/drafts/submit transitions to Submitted."""
		area = a_leaf_area()
		category = a_service_category()
		gtype = frappe.db.get_value("Grievance Type", {"service_category": category, "is_active": 1}, "name")

		# 1. Partial draft save
		save_res = draft.save(
			client_submission_uuid=self.uuid,
			description="Initial draft description for grievance case.",
			contact_mobile="+251911234567",
		)
		self.assertEqual(save_res["status"], "success")

		# 2. Final submission via draft.submit_draft
		submit_res = draft.submit_draft(
			client_submission_uuid=self.uuid,
			submission_channel="Web Portal",
			submitter_type="Individual Farmer",
			submitter_name="Test Submitter",
			contact_mobile="+251911234567",
			administrative_area=area,
			service_category=category,
			grievance_type=gtype,
			description="Final submitted description long enough to satisfy all requirements.",
			consent_given=1,
		)
		self.assertEqual(submit_res["status"], "success")
		ticket = submit_res["data"]["ticket_number"]
		self.assertEqual(len(ticket), 12)
		self.assertIn("-", ticket)

		# 3. Verify Grievance state in database
		from oan_grievance_service.services import ticket_number as tn

		doc = frappe.get_doc("Grievance", tn.normalize(ticket))
		self.assertEqual(doc.workflow_state, "Submitted")
		self.assertEqual(doc.status, "Submitted")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.client_submission_uuid, self.uuid)

		# 4. Attempting to discard or save draft again now fails
		discard_res = draft.discard(client_submission_uuid=self.uuid)
		self.assertEqual(discard_res["status"], "error")

	def test_draft_submit_endpoint_changes_status_to_submitted(self):
		area = a_leaf_area()
		category = a_service_category()
		gtype = frappe.db.get_value("Grievance Type", {"service_category": category, "is_active": 1}, "name")

		draft.save(
			client_submission_uuid=self.uuid,
			administrative_area=area,
			service_category=category,
			grievance_type=gtype,
			contact_mobile="+251911234567",
			description="A draft description long enough to satisfy all requirements.",
			submitter_type="Individual Farmer",
			submitter_name="Draft Tester",
		)

		res = draft.submit_draft(client_submission_uuid=self.uuid, consent_given=1)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["status"], "Submitted")
		self.assertEqual(res["data"]["workflow_state"], "Submitted")
		ticket = res["data"]["ticket_number"]
		self.assertEqual(len(ticket), 12)
		self.assertIn("-", ticket)

		from oan_grievance_service.services import ticket_number as tn

		doc = frappe.get_doc("Grievance", tn.normalize(ticket))
		self.assertEqual(doc.workflow_state, "Submitted")
		self.assertEqual(doc.status, "Submitted")
		self.assertEqual(doc.docstatus, 1)

	def test_draft_submit_requires_consent(self):
		draft.save(
			client_submission_uuid=self.uuid,
			description="A draft description long enough to satisfy all requirements.",
			contact_mobile="+251911234567",
		)
		res = draft.submit_draft(client_submission_uuid=self.uuid, consent_given=0)
		self.assertEqual(res["status"], "error")
		self.assertIn("consent", res["message"].lower())

	def test_draft_save_with_validate_flag(self):
		# Short description (<20 chars) should pass when validate=False
		res = draft.save(
			client_submission_uuid=self.uuid,
			description="Too short",
			validate=False,
		)
		self.assertEqual(res["status"], "success")

		# Short description (<20 chars) should fail validation when validate=True
		res_val = draft.save(
			client_submission_uuid=self.uuid,
			description="Too short",
			validate=True,
		)
		self.assertEqual(res_val["status"], "error")
		self.assertIn("at least 20", str(res_val))

	def test_draft_save_and_load_with_split_phone_numbers(self):
		res = draft.save(
			client_submission_uuid=self.uuid,
			country_code="+251",
			phone="911234567",
			description="Valid description with split phone number details.",
		)
		self.assertEqual(res["status"], "success")
		data = res["data"]
		self.assertEqual(data["contact_mobile"], "+251911234567")
		self.assertEqual(data["country_code"], "+251")
		self.assertEqual(data["phone_number"], "911234567")
		self.assertNotIn("phone_country_code", data)
		self.assertNotIn("phone_national_number", data)

		loaded = draft.load()
		self.assertEqual(loaded["status"], "success")
		loaded_data = loaded["data"]
		self.assertEqual(loaded_data["contact_mobile"], "+251911234567")
		self.assertEqual(loaded_data["country_code"], "+251")
		self.assertEqual(loaded_data["phone_number"], "911234567")
		self.assertNotIn("phone_country_code", loaded_data)
		self.assertNotIn("phone_national_number", loaded_data)

	def test_draft_submit_with_can_request_more_info_false(self):
		area = a_leaf_area()
		category = a_service_category()
		gtype = frappe.db.get_value("Grievance Type", {"service_category": category, "is_active": 1}, "name")

		draft.save(
			client_submission_uuid=self.uuid,
			administrative_area=area,
			service_category=category,
			grievance_type=gtype,
			contact_mobile="+251911234567",
			contact_email="tester@example.com",
			description="A draft description long enough to satisfy all requirements.",
			submitter_type="Individual Farmer",
			submitter_name="Draft Tester",
		)

		res = draft.submit_draft(
			client_submission_uuid=self.uuid,
			consent_given=1,
			can_request_more_info=False,
		)
		self.assertEqual(res["status"], "success")
		self.assertFalse(res["data"]["can_request_more_info"])

		from oan_grievance_service.services import ticket_number as tn

		doc = frappe.get_doc("Grievance", tn.normalize(res["data"]["ticket_number"]))
		self.assertIsNone(doc.contact_mobile)
		self.assertIsNone(doc.contact_email)
		self.assertFalse(
			frappe.db.exists(
				"Grievance Notification Log",
				{"grievance": doc.name, "recipient": "+251911234567"},
			)
		)

	def test_purge_expired_drafts_scheduled_daily(self):
		import oan_grievance_service.hooks as hooks

		daily_tasks = hooks.scheduler_events.get("daily", [])
		self.assertIn("oan_grievance_service.tasks.purge_expired_drafts", daily_tasks)

	def test_client_uuid_alias_accepted(self):
		key = frappe.generate_hash(length=16)
		res = draft.save(client_uuid=key, description="Initial draft description")
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["client_submission_uuid"], key)

	def test_draft_field_clearing_and_anonymity_preservation(self):
		key = frappe.generate_hash(length=16)
		res1 = draft.save(client_submission_uuid=key, is_anonymous=1, description="Something to clear")
		self.assertEqual(res1["data"]["is_anonymous"], 1)
		self.assertEqual(res1["data"]["description"], "Something to clear")

		res2 = draft.save(client_submission_uuid=key, description="")
		self.assertEqual(res2["data"]["description"], "")
		self.assertEqual(res2["data"]["is_anonymous"], 1, "Omitted is_anonymous must not reset to 0")

	def test_draft_does_not_consume_real_ticket_number(self):
		key = frappe.generate_hash(length=16)
		area = a_leaf_area()
		cat = a_service_category()

		res = draft.save(
			client_submission_uuid=key,
			administrative_area=area,
			service_category=cat,
			description="Draft test",
		)
		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["name"].startswith("DRAFT-"))
		self.assertIsNone(res["data"]["ticket_number"])


def _a_submitter_user(email):
	current = frappe.session.user
	frappe.set_user("Administrator")
	try:
		if not frappe.db.exists("Role", "Grievance Submitter"):
			frappe.get_doc({"doctype": "Role", "role_name": "Grievance Submitter"}).insert(
				ignore_permissions=True
			)
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
		else:
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Draft",
					"last_name": "User",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Submitter"}],
				}
			).insert(ignore_permissions=True)
		return user
	finally:
		frappe.set_user(current)
