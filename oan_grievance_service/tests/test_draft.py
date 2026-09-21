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

	def test_discard_remains_reachable_without_a_token(self):
		"""Discard may still clear an abandoned wizard without a session."""
		self.assertIn(
			"/api/method/oan_grievance_service.api.v1.draft.discard",
			middleware.EXEMPT_PATHS,
		)
		for name in ("save", "load"):
			path = f"/api/method/oan_grievance_service.api.v1.draft.{name}"
			self.assertNotIn(path, middleware.EXEMPT_PATHS)

	def test_save_and_get_draft_require_authentication(self):
		"""Save Draft and Get Draft are authenticated-only (same as grievance submit)."""
		for fn in (draft.save, draft.load):
			self.assertIn(fn, frappe.whitelisted)
			self.assertNotIn(fn, frappe.guest_methods)
		self.assertIn(draft.discard, frappe.whitelisted)
		self.assertIn(draft.discard, frappe.guest_methods)


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
		result = draft.load()

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["payload"]["submitter_name"], "Test Submitter")
		self.assertEqual(result["data"]["step_reached"], 3)

	def test_saving_again_overwrites_rather_than_merges(self):
		"""The client holds the whole wizard state; a merge would resurrect cleared fields."""
		draft.save(client_uuid=self.uuid, payload=self._payload(description="First attempt text."))
		draft.save(client_uuid=self.uuid, payload=self._payload(description="Second attempt text."))

		result = draft.load()
		self.assertEqual(result["data"]["payload"]["description"], "Second attempt text.")

	def test_step_reached_never_goes_backwards(self):
		"""A late-arriving save from an earlier step must not undo progress."""
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=3)
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=1)

		self.assertEqual(draft.load()["data"]["step_reached"], 3)

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
		loaded = draft.load()
		self.assertEqual(loaded["data"]["payload"], {"description": "still typing"})
		self.assertEqual(loaded["data"]["step_reached"], 1)

	def test_empty_payload_can_be_saved(self):
		"""A draft may be created before the submitter fills any field."""
		result = draft.save(client_uuid=self.uuid, payload={}, step_reached=0)
		self.assertEqual(result["status"], "success")
		self.assertEqual(draft.load()["data"]["payload"], {})

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
		"""A legacy anonymous draft is claimed on the first authenticated save."""
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Draft",
				"client_uuid": self.uuid,
				"payload": frappe.as_json(self._payload(description="started anonymously")),
				"step_reached": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		self.assertFalse(doc.owner_user)

		user = _a_submitter_user("draft.claimer@example.com")
		frappe.set_user(user.name)
		result = draft.save(
			client_uuid=self.uuid,
			payload=self._payload(description="continued after login"),
			step_reached=2,
		)
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["owner_user"], user.name)

	def test_guest_cannot_save_a_draft(self):
		"""Save Draft requires authentication - guests are rejected."""
		frappe.set_user("Guest")
		result = draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=1)
		self.assertEqual(result["status"], "error")
		self.assertIn(frappe.response.get("http_status_code"), (401, 403))

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

	def test_loading_with_no_draft_is_a_404_not_an_empty_success(self):
		other = _a_submitter_user("draft.empty@example.com")
		frappe.set_user(other.name)
		result = draft.load()
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

	def test_load_returns_full_wizard_state(self):
		"""Resume must repopulate every step, including attachment metadata."""
		payload = self._payload(
			service_category="Inputs",
			grievance_type="Fertilizer Shortage",
			administrative_area="kebele-ET140108101008",
			desired_outcome="Deliver the allocated fertilizer this week.",
		)
		draft.save(client_uuid=self.uuid, payload=payload, step_reached=4)
		result = draft.load()

		data = result["data"]
		self.assertEqual(result["status"], "success")
		self.assertEqual(data["client_uuid"], self.uuid)
		self.assertEqual(data["step_reached"], 4)
		self.assertEqual(data["payload"]["service_category"], "Inputs")
		self.assertEqual(data["payload"]["grievance_type"], "Fertilizer Shortage")
		self.assertEqual(data["payload"]["administrative_area"], "kebele-ET140108101008")
		self.assertEqual(data["payload"]["desired_outcome"], "Deliver the allocated fertilizer this week.")
		self.assertEqual(data["contact_mobile"], "+251911234567")
		self.assertIn("attachments", data)
		self.assertEqual(data["attachment_count"], 0)
		self.assertEqual(data["attachments"], [])
		self.assertTrue(data["name"])
		self.assertTrue(data["expires_on"])

	def test_draft_reports_grievance_attachment_rows(self):
		"""Uploads attach Files to Grievance Attachment, not to the draft DocType."""
		saved = draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=2)
		draft_name = frappe.db.get_value("Grievance Draft", {"client_uuid": self.uuid}, "name")
		frappe.get_doc(
			{
				"doctype": "Grievance Attachment",
				"draft": draft_name,
				"file_name": "receipt.jpg",
				"file_url": "/private/files/receipt-test.jpg",
				"mime_type": "image/jpeg",
				"size_bytes": 128,
				"document_type": "Receipt",
				"scan_status": "Pending",
			}
		).insert(ignore_permissions=True)

		loaded = draft.load()
		self.assertEqual(loaded["status"], "success")
		self.assertEqual(loaded["data"]["attachment_count"], 1)
		self.assertEqual(len(loaded["data"]["attachments"]), 1)
		row = loaded["data"]["attachments"][0]
		self.assertEqual(row["file_name"], "receipt.jpg")
		self.assertEqual(row["document_type"], "Receipt")
		self.assertEqual(row["scan_status"], "Pending")
		self.assertEqual(row["mime_type"], "image/jpeg")
		self.assertEqual(row["size_bytes"], 128)
		self.assertNotIn("file_url", row)

		resaved = draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=2)
		self.assertEqual(resaved["data"]["attachment_count"], 1)
		self.assertEqual(saved["data"]["attachment_count"], 0)

	def test_load_returns_the_latest_draft_only(self):
		"""A user has one active draft; Get Draft returns the newest open one."""
		older = frappe.generate_hash(length=20)
		newer = frappe.generate_hash(length=20)
		draft.save(
			client_uuid=older,
			payload=self._payload(description="Older draft text for resume."),
			step_reached=1,
		)
		draft.save(
			client_uuid=newer,
			payload=self._payload(description="Newer draft text for resume."),
			step_reached=3,
		)

		result = draft.load()
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["client_uuid"], newer)
		self.assertEqual(result["data"]["step_reached"], 3)
		self.assertEqual(result["data"]["payload"]["description"], "Newer draft text for resume.")

	def test_another_user_never_receives_my_draft(self):
		draft.save(client_uuid=self.uuid, payload=self._payload(), step_reached=2)
		other = _a_submitter_user("draft.other@example.com")
		frappe.set_user(other.name)

		result = draft.load()
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 404)

	def test_guest_cannot_load_a_draft(self):
		draft.save(client_uuid=self.uuid, payload=self._payload())
		frappe.set_user("Guest")

		result = draft.load()
		self.assertEqual(result["status"], "error")
		self.assertIn(frappe.response["http_status_code"], (401, 403))

	def test_an_expired_draft_is_a_404(self):
		draft.save(client_uuid=self.uuid, payload=self._payload())
		frappe.db.set_value(
			"Grievance Draft",
			{"client_uuid": self.uuid},
			"expires_on",
			"2000-01-01 00:00:00",
		)

		result = draft.load()
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.response["http_status_code"], 404)


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
