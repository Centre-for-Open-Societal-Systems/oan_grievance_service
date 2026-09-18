# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Draft wizard state.

These exist because the module had none. `draft.py` imported a name that had been
deleted in an earlier refactor, so every endpoint in it raised ImportError on the
first request -- and the suite stayed green throughout, because nothing imported
the module. The first test here is therefore the import itself.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api import middleware
from oan_grievance_service.api.v1 import draft


class TestDraftModuleLoads(FrappeTestCase):
	def test_the_module_imports(self):
		"""The bug this file was written for: draft.py could not be imported at all."""
		self.assertTrue(callable(draft.save))
		self.assertTrue(callable(draft.load))
		self.assertTrue(callable(draft.discard))

	def test_every_endpoint_is_reachable_without_a_token(self):
		"""A draft is saved before the submitter registers, so JWT must not gate it."""
		for name in ("save", "load", "discard"):
			path = f"/api/method/oan_grievance_service.api.v1.draft.{name}"
			self.assertIn(path, middleware.EXEMPT_PATHS)

	def test_every_endpoint_allows_guest(self):
		"""frappe.whitelist(allow_guest=True) registers the function in guest_methods."""
		for fn in (draft.save, draft.load, draft.discard):
			self.assertIn(fn, frappe.whitelisted)
			self.assertIn(fn, frappe.guest_methods)


class TestDraftRoundTrip(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.uuid = frappe.generate_hash(length=20)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def _payload(self, **overrides):
		values = {
			"submitter_name": "Test Submitter",
			"contact_mobile": "+251911234567",
			"description": "A description long enough to clear the twenty character minimum.",
		}
		values.update(overrides)
		return values

	def test_save_returns_the_standard_envelope(self):
		"""Every response carries the shared success shape, not a bare dict."""
		result = draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=2)
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["client_uuid"], self.uuid)
		self.assertEqual(result["data"]["step_reached"], 2)

	def test_a_saved_draft_loads_back(self):
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=3)
		result = draft.load(client_uuid=self.uuid)

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["payload"]["submitter_name"], "Test Submitter")
		self.assertEqual(result["data"]["step_reached"], 3)

	def test_saving_again_overwrites_rather_than_merges(self):
		"""The client holds the whole wizard state; a merge would resurrect cleared fields."""
		draft.save(client_uuid=self.uuid, payload=self._payload(description="First attempt text."))
		draft.save(client_uuid=self.uuid, payload=self._payload(description="Second attempt text."))

		result = draft.load(client_uuid=self.uuid)
		self.assertEqual(result["data"]["payload"]["description"], "Second attempt text.")

	def test_step_reached_never_goes_backwards(self):
		"""A late-arriving save from an earlier step must not undo progress."""
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=3)
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=1)

		self.assertEqual(draft.load(client_uuid=self.uuid)["data"]["step_reached"], 3)

	def test_a_draft_key_is_required(self):
		"""Missing draft key is rejected at the schema edge."""
		result = draft.save(client_uuid="", payload=self._payload())
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "VALIDATION_ERROR")

	def test_partial_payload_can_be_saved(self):
		"""STG-322: incomplete wizard answers must not block a draft save."""
		result = draft.save(
			client_uuid=self.uuid,
			payload={"description": "still typing"},
			step_reached=1,
		)
		self.assertEqual(result["status"], "success")
		loaded = draft.load(client_uuid=self.uuid)
		self.assertEqual(loaded["data"]["payload"], {"description": "still typing"})
		self.assertEqual(loaded["data"]["step_reached"], 1)

	def test_empty_payload_can_be_saved(self):
		"""A draft may be created before the submitter fills any field."""
		result = draft.save(client_uuid=self.uuid, payload={}, step_reached=0)
		self.assertEqual(result["status"], "success")
		self.assertEqual(draft.load(client_uuid=self.uuid)["data"]["payload"], {})

	def test_logged_in_save_associates_owner_user(self):
		"""Saved drafts are keyed to the session user for later resume."""
		user = _a_submitter_user("draft.owner@example.com")
		frappe.set_user(user.name)

		result = draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=2)
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["owner_user"], user.name)
		self.assertEqual(
			frappe.db.get_value("Grievance Draft", {"client_uuid": self.uuid}, "owner_user"),
			user.name,
		)

	def test_logged_in_save_claims_anonymous_draft(self):
		"""Signing in mid-wizard binds the anonymous draft to that user."""
		frappe.set_user("Guest")
		draft.save(
			client_uuid=self.uuid, payload=self._payload(description="started as guest"), step_reached=1
		)
		self.assertFalse(frappe.db.get_value("Grievance Draft", {"client_uuid": self.uuid}, "owner_user"))

		user = _a_submitter_user("draft.claimer@example.com")
		frappe.set_user(user.name)
		result = draft.save(
			client_uuid=self.uuid,
			payload=self._payload(description="continued after login"),
			step_reached=2,
		)
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["owner_user"], user.name)

	def test_guest_cannot_overwrite_a_claimed_draft(self):
		user = _a_submitter_user("draft.claimed@example.com")
		frappe.set_user(user.name)
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=1)

		frappe.set_user("Guest")
		result = draft.save(client_uuid=self.uuid, payload=self._payload(description="intruder"))
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 403)

	def test_another_user_cannot_overwrite_my_draft(self):
		owner = _a_submitter_user("draft.save.owner@example.com")
		frappe.set_user(owner.name)
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=2)

		other = _a_submitter_user("draft.save.other@example.com")
		frappe.set_user(other.name)
		result = draft.save(client_uuid=self.uuid, payload=self._payload(description="not mine"))
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 403)
		self.assertIn("another user", result["message"])

	def test_loading_an_unknown_draft_is_a_404_not_an_empty_success(self):
		result = draft.load(client_uuid="never-saved-anything")
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 404)

	def test_discarding_removes_it(self):
		draft.save(client_uuid=self.uuid, payload=self._payload())
		result = draft.discard(client_uuid=self.uuid)

		self.assertTrue(result["data"]["discarded"])
		self.assertFalse(frappe.db.exists("Grievance Draft", {"client_uuid": self.uuid}))

	def test_discarding_nothing_succeeds_quietly(self):
		"""An abandoned wizard may discard twice; the second is not an error."""
		result = draft.discard(client_uuid="never-saved-anything")
		self.assertEqual(result["status"], "success")
		self.assertFalse(result["data"]["discarded"])

	def test_a_submitted_draft_cannot_be_discarded(self):
		"""It is what makes a retry of submit() return the original ticket."""
		draft.save(client_uuid=self.uuid, payload=self._payload())
		frappe.db.set_value("Grievance Draft", {"client_uuid": self.uuid}, "submitted_as", "GRV-TEST-0001")

		# handle_api_errors rolls the request back before returning the envelope, so
		# the assertion is on the refusal itself rather than on the row surviving.
		result = draft.discard(client_uuid=self.uuid)
		self.assertEqual(result["status"], "error")
		self.assertIn("GRV-TEST-0001", result["message"])

	def test_a_submitted_draft_cannot_be_overwritten(self):
		draft.save(client_uuid=self.uuid, payload=self._payload())
		frappe.db.set_value("Grievance Draft", {"client_uuid": self.uuid}, "submitted_as", "GRV-TEST-0001")

		result = draft.save(client_uuid=self.uuid, payload=self._payload())
		self.assertEqual(result["status"], "error")
		self.assertIn("already been submitted", result["message"])


def _a_submitter_user(email):
	if not frappe.db.exists("Role", "Grievance Submitter"):
		frappe.get_doc({"doctype": "Role", "role_name": "Grievance Submitter"}).insert(
			ignore_permissions=True
		)
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)
	return frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Draft",
			"last_name": "User",
			"send_welcome_email": 0,
			"roles": [{"role": "Grievance Submitter"}],
		}
	).insert(ignore_permissions=True)
