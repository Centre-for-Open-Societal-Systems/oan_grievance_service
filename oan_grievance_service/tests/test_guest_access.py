"""Link-and-PIN view of a sensitive anonymous case (STG-518).

Each acceptance criterion the backend owns is checked where it is enforced: issuing and the
message in services/guest_access.py and services/notifications.py, the PIN check in
services/guest_access.py, and the one read-only route through the JWT middleware and Frappe's
router, as the portal's backend-for-frontend calls it.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date
from frappe.utils.password import get_decrypted_password

from oan_grievance_service import permissions
from oan_grievance_service.api.v1 import _case_view as case_view
from oan_grievance_service.api.v1 import attachment
from oan_grievance_service.api.v1 import grievance as grievance_api
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import guest_access, notifications
from oan_grievance_service.services import ticket_number as tn
from oan_grievance_service.setup.install import seed_notifications
from oan_grievance_service.tests.fixtures import a_submitter_type, a_user_with_role
from oan_grievance_service.tests.test_attachment_api import _jpeg, _pdf, _Request, _Upload
from oan_grievance_service.tests.test_category_assignment import _keep_transaction
from oan_grievance_service.tests.test_sensitive_cases import OFFICER_NAME, SensitiveFixture

PEPPER = "test-pepper-for-guest-access-0123456789abcdef"
BASE_URL = "https://grievance.example.test"
VIEW = "/api/v1/guest/grievance"
# Rate limits high enough that the suite's own calls from 127.0.0.1 never trip them.
CONF = {
	"guest_access_pepper": PEPPER,
	"guest_access_base_url": BASE_URL,
	"guest_access_verify_ip_limit": 100000,
	"guest_access_verify_ticket_limit": 100000,
	# Off, as on a site that does not set it; the tests of the temporary toast turn it on.
	"guest_access_show_on_submit": 0,
}
INVALID_MESSAGE = "Check the SMS/Email for the correct PIN."
ALL_CANDIDATES = guest_access.backfill_candidates


@contextmanager
def _no_commit():
	"""The dispatcher and the backfill commit as they go, which would keep this test's fixtures."""
	with patch.object(frappe.db, "commit"):
		yield


def _only_candidates(*names):
	"""Limit the backfill to these cases, among the ones it would pick: the dev site has others."""
	return patch.object(
		guest_access, "backfill_candidates", side_effect=lambda: [n for n in ALL_CANDIDATES() if n in names]
	)


def _wrong(pin):
	return "000000" if pin != "000000" else "111111"


class GuestAccessFixture(SensitiveFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		seed_notifications()

	def setUp(self):
		super().setUp()
		conf = patch.dict(frappe.conf, CONF)
		conf.start()
		self.addCleanup(conf.stop)

	def an_open_case(self, **values):
		values.setdefault("is_anonymous", 1)
		values.setdefault("assigned_to", self.focal_officer)
		return self.a_case(**values)

	def issued(self, case, source=guest_access.SOURCE_FILING):
		"""Issue the case's link; returns (token, pin) as the message carries them."""
		self.assertTrue(guest_access.issue_for(case, source))
		return tuple(self.pending(case).split(":", 1))

	def pending(self, case):
		name = frappe.db.get_value(guest_access.ACCESS, {"grievance": case.name}, "name")
		return get_decrypted_password(guest_access.ACCESS, name, "pending_secret", raise_exception=False)

	def access(self, case):
		return frappe.db.get_value(
			guest_access.ACCESS,
			{"grievance": case.name},
			["name", "status", "source", "token_hash", "pin_hash", "pending_secret", "failed_attempts"],
			as_dict=True,
		)

	def link_rows(self, case):
		return frappe.get_all(
			"Grievance Notification Log",
			filters={"grievance": case.name, "event": ["in", list(C.GUEST_ACCESS_EVENTS)]},
			fields=["name", "event", "channel", "recipient", "message", "subject", "status", "language"],
			order_by="creation asc",
		)

	def call(self, method, path, json_body=None):
		"""One request through the middleware and router, as the portal's backend sends it."""
		from oan_auth_service.testing import api_request

		from oan_grievance_service.api.router import ensure_routes_registered

		ensure_routes_registered()
		with _keep_transaction():
			response = api_request(frappe.api.handle, method, path, json=json_body)
		body = response.get_data(as_text=True)
		return response.status_code, (json.loads(body) if body.startswith("{") else body)

	def with_evidence(self, case):
		"""A public file on the case and an officer's file on an internal note; their stored names.

		Stored names, as Frappe suffixes a name already on disk from an earlier run.
		"""
		internal = GrievanceTimeline.record(
			grievance=case.name, entry_type="note", is_internal=True, body="Evidence", author_type="officer"
		)
		saved = getattr(frappe.local, "request", None)
		try:
			frappe.local.request = _Request(_Upload("public.jpg", _jpeg()))
			public = attachment.submit_documents(grievance=case.name)["data"][0]["file_name"]
			frappe.local.request = _Request(_Upload("internal.pdf", _pdf()))
			hidden = attachment.submit_documents(grievance=case.name, timeline_entry=internal.name)["data"][
				0
			]["file_name"]
		finally:
			frappe.local.request = saved
		return public, hidden

	def verify(self, case, token, pin):
		"""The one guest call: the link's ticket and token and the PIN, answered with the case."""
		return self.call(
			"POST", VIEW, {"ticket_number": tn.display(case.ticket_number), "token": token, "pin": pin}
		)


class TestIssuing(GuestAccessFixture):
	def submit(self, user="Administrator", **payload):
		body = {
			"service_category": self.category,
			"grievance_type": self.sensitive_type,
			"administrative_area": self.kebele,
			"description": "A sensitive grievance described in more than twenty characters.",
			"submitter_type": a_submitter_type(),
			"submitter_name": "Test Submitter",
			"contact_mobile": "+251911234567",
			"is_anonymous": True,
			**payload,
		}
		frappe.set_user(user)
		try:
			result = grievance_api.submit(**body)
		finally:
			frappe.set_user("Administrator")
		self.assertIn("data", result, msg=result)
		self.filed = result["data"]
		return frappe.get_doc("Grievance", tn.normalize(result["data"]["ticket_number"]))

	def a_registered_farmer(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_name": "Registered Farmer",
				"submitter_type": a_submitter_type(),
				"contact_mobile": "+251911234567",
				"user": farmer,
			}
		).insert(ignore_permissions=True)
		return farmer

	def test_ac1_filing_issues_a_link_and_queues_one_masked_sms(self):
		case = self.submit()
		access = self.access(case)
		self.assertEqual((access.status, access.source), ("Active", "Filing"))
		rows = self.link_rows(case)
		self.assertEqual([(row.event, row.channel) for row in rows], [(C.EVENT_GUEST_ACCESS_ISSUED, "SMS")])
		token, pin = self.pending(case).split(":", 1)
		self.assertEqual(
			rows[0].message,
			f"Grievance {tn.display(case.ticket_number)}. View it at {BASE_URL}/g/{case.ticket_number}"
			f"#k={guest_access.MASK} PIN: {guest_access.MASK}. Do not share this message.",
		)
		self.assertNotIn(token, f"{rows[0].message} {rows[0].subject}")
		self.assertNotIn(pin, f"{rows[0].message} {rows[0].subject}")

	def test_ac1_the_message_names_no_category_office_or_officer(self):
		case = self.submit()
		row = self.link_rows(case)[0]
		text = notifications._outbound_message(row, case)
		for leaked in (self.category, self.department, self.focal_officer, OFFICER_NAME, "ensitive"):
			self.assertNotIn(leaked, f"{text} {row.subject}")

	def test_ac2_a_submitter_with_a_login_gets_the_link_too_and_can_still_sign_in(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_name": "Registered Farmer",
				"submitter_type": a_submitter_type(),
				"contact_mobile": "+251911234567",
				"user": farmer,
			}
		).insert(ignore_permissions=True)
		case = self.submit(farmer)
		self.assertEqual(self.access(case).status, "Active")
		self.assertEqual({row.recipient for row in self.link_rows(case)}, {farmer})
		self.assertTrue(permissions.has_grievance_permission(case, "read", user=farmer))

	def test_ac2_a_case_filed_on_behalf_sends_the_link_to_the_submitter_not_the_filer(self):
		agent = a_user_with_role("Grievance Assisted Submitter", "agent")
		case = self.submit(agent)
		self.assertEqual(case.assisted_by_officer, agent)
		self.assertEqual([row.recipient for row in self.link_rows(case)], ["+251911234567"])

	def test_ac3_an_ordinary_or_named_case_gets_no_link(self):
		for case in (self.submit(grievance_type=self.ordinary_type), self.submit(is_anonymous=False)):
			self.assertIsNone(self.access(case))
			self.assertEqual(self.link_rows(case), [])

	def test_ac3_a_case_with_no_contact_gets_no_link(self):
		case = frappe._dict(is_sensitive=1, is_anonymous=1, contact_mobile=None, contact_email=None)
		self.assertFalse(guest_access.is_eligible(case))

	def test_ac4_an_email_only_case_gets_the_link_by_email_only(self):
		case = self.an_open_case(contact_email="farmer@example.com")
		case.db_set("contact_mobile", None, update_modified=False)
		self.issued(case)
		self.assertEqual([row.channel for row in self.link_rows(case)], ["Email"])

	def test_ac4_a_case_with_both_gets_both(self):
		"""Q4 is open; the default sends on every channel the case has an address for."""
		case = self.an_open_case(contact_email="farmer@example.com")
		self.issued(case)
		self.assertEqual(sorted(row.channel for row in self.link_rows(case)), ["Email", "SMS"])

	def test_ac5_a_failed_send_is_marked_failed_not_retried_and_the_secret_is_cleared(self):
		case = self.an_open_case()
		self.issued(case)
		with _no_commit(), patch.object(notifications, "_send_sms", side_effect=RuntimeError("down")):
			notifications.dispatch_queued(grievance=case.name)
			self.assertEqual(notifications.dispatch_queued(grievance=case.name), 0)
		self.assertEqual([row.status for row in self.link_rows(case)], ["Failed"])
		self.assertFalse(self.pending(case))

	def test_ac6_only_hashes_are_kept_and_the_secret_goes_once_sent(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		access = self.access(case)
		self.assertEqual(access.token_hash, guest_access.token_hash(PEPPER, token))
		self.assertTrue(guest_access.pin_matches(PEPPER, pin, access.pin_hash))
		self.assertFalse(guest_access.pin_matches("another-pepper", pin, access.pin_hash))
		for column in (access.token_hash, access.pin_hash, access.pending_secret):
			self.assertNotIn(token, column)
			self.assertNotEqual(pin, column)
		self.assertEqual(set(access.pending_secret), {"*"})

		sent = []
		with (
			_no_commit(),
			patch.object(notifications, "_send_sms", side_effect=lambda to, text, **kw: sent.append(text)),
		):
			notifications.dispatch_queued(grievance=case.name)
		self.assertIn(f"{BASE_URL}/g/{case.ticket_number}#k={token}", sent[-1])
		self.assertIn(f"PIN: {pin}", sent[-1])
		row = self.link_rows(case)[0]
		self.assertEqual(row.status, "Sent")
		self.assertNotIn(token, row.message)
		self.assertFalse(self.pending(case))
		self.assertFalse(self.access(case).pending_secret)

	def test_a_row_whose_secret_is_gone_fails_rather_than_going_out_masked(self):
		case = self.an_open_case()
		self.issued(case)
		row = self.link_rows(case)[0]
		frappe.db.set_value("Grievance Notification Log", row.name, "status", "Sent")
		guest_access.clear_pending_secret_when_sent(case.name)
		with self.assertRaises(ValueError):
			notifications._outbound_message(row, case)

	def test_a_case_gets_one_link_only(self):
		case = self.an_open_case()
		self.issued(case)
		self.assertIsNone(guest_access.issue_for(case))
		self.assertEqual(len(self.link_rows(case)), 1)

	def test_nothing_is_issued_without_the_pepper(self):
		case = self.an_open_case()
		with patch.dict(frappe.conf, {"guest_access_pepper": ""}):
			self.assertIsNone(guest_access.issue_for(case))
		self.assertIsNone(self.access(case))

	def test_no_record_is_kept_when_no_channel_can_take_the_message(self):
		case = self.an_open_case()
		with patch.object(notifications, "queue", return_value=[]):
			self.assertIsNone(guest_access.issue_for(case))
		self.assertIsNone(self.access(case))

	def test_a_failure_to_issue_never_stops_the_filing(self):
		with patch.object(guest_access, "issue_for", side_effect=RuntimeError("boom")):
			case = self.submit()
		self.assertTrue(frappe.db.exists("Grievance", case.name))

	def test_the_status_pin_is_left_as_it_is(self):
		"""Q1 is open: the case keeps its encrypted status PIN beside the hashed link PIN."""
		case = self.submit()
		self.assertTrue(frappe.get_doc("Grievance", case.name).get_password("status_pin"))
		self.assertTrue(self.access(case))

	def test_the_filing_answer_shows_the_link_and_pin_to_a_self_filer_when_switched_on(self):
		"""Temporary, while SMS and email are not delivering (guest_access_show_on_submit)."""
		with patch.dict(frappe.conf, {"guest_access_show_on_submit": 1}):
			case = self.submit(self.a_registered_farmer())
		token, pin = self.pending(case).split(":", 1)
		self.assertEqual(
			self.filed["guest_access"],
			{"link": f"{BASE_URL}/g/{case.ticket_number}#k={token}", "pin": pin},
		)
		# The verify route accepts what was shown.
		self.assertEqual(self.verify(case, token, pin)[0], 200)

	def test_the_filing_answer_shows_no_link_by_default(self):
		self.submit(self.a_registered_farmer())
		self.assertNotIn("guest_access", self.filed)

	def test_someone_filing_on_the_submitters_behalf_is_never_shown_the_link(self):
		agent = a_user_with_role("Grievance Assisted Submitter", "agent")
		with patch.dict(frappe.conf, {"guest_access_show_on_submit": 1}):
			for user in ("Administrator", agent):
				self.submit(user)
				self.assertNotIn("guest_access", self.filed)
				self.assertNotIn("status_pin", self.filed)


class TestGuestView(GuestAccessFixture):
	def test_ac8_a_wrong_pin_answers_401_with_the_message(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		status, body = self.verify(case, token, _wrong(pin))
		self.assertEqual(
			(status, body["code"], body["message"]), (401, guest_access.CODE_INVALID, INVALID_MESSAGE)
		)

	def test_ac11_the_right_link_and_pin_answer_with_the_case_and_nothing_identifying(self):
		case = self.an_open_case()
		for internal, body in ((True, "Internal: officer only"), (False, "We are looking into it.")):
			GrievanceTimeline.record(
				grievance=case.name,
				entry_type="note" if internal else "message",
				is_internal=internal,
				body=body,
				author_user=self.focal_officer,
				author_type="officer",
			)
		token, pin = self.issued(case)
		status, body = self.verify(case, token, pin)
		self.assertEqual(status, 200, body)
		data = body["data"]
		self.assertEqual(
			(data["ticket_number"], data["description"]), (tn.display(case.ticket_number), case.description)
		)
		for hidden in (
			"submitter",
			"assignment",
			"available_actions",
			"is_sensitive",
			"name",
			"session_token",
		):
			self.assertNotIn(hidden, data)
		bodies = [entry["body"] for entry in data["timeline"]]
		self.assertIn("We are looking into it.", bodies)
		self.assertNotIn("Internal: officer only", bodies)
		for entry in data["timeline"]:
			self.assertNotIn("author_name", entry)
			self.assertNotIn("is_internal", entry)
		# The officer's rung, as a signed-in submitter's /timeline shows it; never their name.
		reply = next(entry for entry in data["timeline"] if entry["body"] == "We are looking into it.")
		self.assertEqual(reply["author_type"], "officer")
		self.assertTrue(reply["author_role"])
		shown = json.dumps(data)
		for leaked in ("Test Submitter", "+251911234567", self.focal_officer, OFFICER_NAME, self.department):
			self.assertNotIn(leaked, shown)

	def test_ac11_files_are_named_but_not_served_and_internal_evidence_stays_hidden(self):
		case = self.an_open_case()
		public, _hidden = self.with_evidence(case)
		token, pin = self.issued(case)
		data = self.verify(case, token, pin)[1]["data"]
		self.assertEqual([row["file_name"] for row in data["attachments"]], [public])
		# The same name and meaning as /timeline's: `file_size`, and nothing to fetch it by.
		self.assertGreater(data["attachments"][0]["file_size"], 0)
		for hidden in (
			"file_url",
			"size_bytes",
			"timeline_entry",
			"uploaded_by_user",
			"uploaded_by_submitter",
		):
			self.assertNotIn(hidden, data["attachments"][0])

	def test_the_view_is_audited_as_a_link_view(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		self.verify(case, token, pin)
		self.assertTrue(
			frappe.db.exists(
				"Grievance Access Audit Event",
				{"grievance": case.name, "action": "view_detail", "scope_evaluated": "guest link"},
			)
		)

	def test_only_the_read_only_route_exists(self):
		from oan_auth_service.api.router import _rules

		from oan_grievance_service.api.router import ensure_routes_registered

		ensure_routes_registered()
		paths = {rule.rule for rule in _rules}
		self.assertIn(VIEW, paths)
		self.assertEqual(
			{p for p in paths if p.startswith(("/api/v1/guest-access", "/api/v1/guest/"))}, {VIEW}
		)

	def test_ac9_unknown_ticket_wrong_token_and_wrong_pin_answer_alike(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		answers = [
			self.call("POST", VIEW, {"ticket_number": "9-ZZZ-ZZZZ-Z", "token": token, "pin": pin}),
			self.verify(case, "x" * 22, pin),
			self.verify(case, token, _wrong(pin)),
		]
		self.assertEqual(
			{(status, body["code"], body["message"]) for status, body in answers},
			{(401, guest_access.CODE_INVALID, INVALID_MESSAGE)},
		)

	def test_ac10_the_link_locks_after_n_wrong_pins_even_for_the_right_one(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		with patch.dict(frappe.conf, {"guest_access_max_attempts": 3}):
			for _ in range(3):
				self.assertEqual(self.verify(case, token, _wrong(pin))[0], 401)
			status, body = self.verify(case, token, pin)
		self.assertEqual((status, body["code"]), (429, guest_access.CODE_LOCKED))
		self.assertGreater(body["details"]["retry_after_seconds"], 0)
		self.assertEqual(self.access(case).status, "Locked")

	def test_ac10_once_the_lock_ends_the_right_pin_opens_and_resets_the_count(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		frappe.db.set_value(
			guest_access.ACCESS,
			self.access(case).name,
			{"status": "Locked", "locked_until": add_to_date(None, minutes=-1), "failed_attempts": 2},
		)
		self.assertEqual(self.verify(case, token, pin)[0], 200)
		access = self.access(case)
		self.assertEqual((access.status, access.failed_attempts), ("Active", 0))

	def test_a_wrong_pin_is_counted_in_the_database_not_written_back(self):
		"""Parallel guesses each count: the count is incremented where it is stored."""
		case = self.an_open_case()
		token, pin = self.issued(case)
		name = self.access(case).name
		self.verify(case, token, _wrong(pin))
		# Another request's guess lands in between; a read-then-write would overwrite it.
		frappe.db.sql(
			"update `tabGrievance Guest Access` set failed_attempts = failed_attempts + 1 where name = %s",
			name,
		)
		self.verify(case, token, _wrong(pin))
		self.assertEqual(self.access(case).failed_attempts, 3)

	def test_a_wrong_token_does_not_count_toward_the_lock(self):
		"""Ticket numbers are sequential: counting these would let anyone lock a submitter out."""
		case = self.an_open_case()
		_token, pin = self.issued(case)
		with patch.dict(frappe.conf, {"guest_access_max_attempts": 2}):
			for _ in range(3):
				self.verify(case, "y" * 22, pin)
		access = self.access(case)
		self.assertEqual((access.status, access.failed_attempts), ("Active", 0))

	def test_junk_requests_for_a_ticket_do_not_use_up_its_owners_limit(self):
		"""Only attempts that hold the right link count against the ticket."""
		case = self.an_open_case()
		token, pin = self.issued(case)
		frappe.cache.delete_value(f"grievance_guest:verify:ticket:{case.ticket_number}")
		with patch.dict(frappe.conf, {"guest_access_verify_ticket_limit": 2}):
			junk = [self.verify(case, "z" * 22, pin)[0] for _ in range(5)]
			self.assertEqual(junk, [401] * 5)
			self.assertEqual(self.verify(case, token, pin)[0], 200)

	def test_attempts_with_the_right_link_are_rate_limited_per_ticket(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		# Ticket numbers come from a counter the test rollback rewinds, so an earlier run's count
		# for this number may still be in Redis.
		frappe.cache.delete_value(f"grievance_guest:verify:ticket:{case.ticket_number}")
		with patch.dict(frappe.conf, {"guest_access_verify_ticket_limit": 2}):
			statuses = [self.verify(case, token, _wrong(pin))[0] for _ in range(3)]
		self.assertEqual(statuses, [401, 401, 429])

	def test_ac17_a_right_pin_on_a_closed_case_says_closed_and_anything_else_stays_generic(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		case.db_set("workflow_state", C.STATE_CLOSED, update_modified=False)
		status, body = self.verify(case, token, _wrong(pin))
		self.assertEqual((status, body["code"]), (401, guest_access.CODE_INVALID))
		status, body = self.verify(case, token, pin)
		self.assertEqual((status, body["code"]), (410, guest_access.CODE_CLOSED))
		self.assertEqual(self.access(case).status, "Closed")

	def test_verify_without_configuration_refuses(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		with patch.dict(frappe.conf, {"guest_access_pepper": ""}):
			status, body = self.verify(case, token, pin)
		self.assertEqual((status, body["code"]), (503, guest_access.CODE_NOT_CONFIGURED))

	def test_the_view_reads_as_nobody_and_grants_nothing_to_guest(self):
		case = self.an_open_case()
		token, pin = self.issued(case)
		self.verify(case, token, pin)
		self.assertFalse(permissions.has_grievance_permission(case, "read", user="Guest"))


class TestSharedCaseView(GuestAccessFixture):
	"""The submitter-safe parts the guest view and /timeline share (api/v1/_case_view.py)."""

	def test_files_on_internal_entries_are_for_those_who_see_internal_entries(self):
		case = self.an_open_case()
		public, hidden = self.with_evidence(case)

		def files(include):
			return sorted(row.file_name for row in case_view.case_attachments(case, include_internal=include))

		self.assertEqual(files(True), sorted([public, hidden]))
		self.assertEqual(files(False), [public])

	def test_a_submitters_timeline_no_longer_lists_files_from_internal_notes(self):
		farmer = a_user_with_role("Grievance Submitter", "farmer")
		profile = frappe.get_doc(
			{
				"doctype": "Grievance Submitter Profile",
				"submitter_name": "Registered Farmer",
				"submitter_type": a_submitter_type(),
				"contact_mobile": "+251911234567",
				"user": farmer,
			}
		).insert(ignore_permissions=True)
		case = self.an_open_case(submitter=profile.name)
		public, hidden = self.with_evidence(case)

		frappe.set_user(farmer)
		try:
			as_submitter = grievance_api.timeline(ticket_number=case.ticket_number)
		finally:
			frappe.set_user("Administrator")
		as_staff = grievance_api.timeline(ticket_number=case.ticket_number)

		self.assertEqual([a["file_name"] for a in as_submitter["data"]["attachments"]], [public])
		self.assertEqual(
			sorted(a["file_name"] for a in as_staff["data"]["attachments"]), sorted([public, hidden])
		)

	def test_the_guest_view_names_its_fields_as_the_timeline_does(self):
		case = self.an_open_case()
		self.with_evidence(case)
		GrievanceTimeline.record(
			grievance=case.name,
			entry_type="message",
			is_internal=False,
			body="We are looking into it.",
			author_user=self.focal_officer,
			author_type="officer",
		)
		token, pin = self.issued(case)
		guest = self.verify(case, token, pin)[1]["data"]
		staff = grievance_api.timeline(ticket_number=case.ticket_number)["data"]

		self.assertLessEqual(set(guest["attachments"][0]), set(staff["attachments"][0]))

		def reply(view):
			return next(e for e in view["timeline"] if e["body"] == "We are looking into it.")

		self.assertLessEqual(set(reply(guest)) - {"attachments"}, set(reply(staff)))
		for field in ("ticket_number", "status", "description", "location", "grievance_type_name"):
			self.assertEqual(guest[field], staff[field], field)


class TestBackfill(GuestAccessFixture):
	def test_ac20_a_dry_run_counts_and_creates_nothing(self):
		case = self.an_open_case()
		with _only_candidates(case.name):
			result = guest_access.backfill_existing(dry_run=True)
		self.assertEqual((result["dry_run"], result["matching"]), (True, 1))
		self.assertIsNone(self.access(case))
		self.assertEqual(self.link_rows(case), [])

	def test_ac18_the_backfill_issues_open_cases_once_and_leaves_closed_ones(self):
		open_case = self.an_open_case()
		closed = self.an_open_case()
		closed.db_set("workflow_state", C.STATE_CLOSED, update_modified=False)
		with (
			_no_commit(),
			_only_candidates(open_case.name, closed.name),
			patch.object(frappe, "enqueue") as enqueue,
		):
			result = guest_access.backfill_existing(dry_run=False)
		self.assertEqual((result["matching"], result["issued"], result["failed"]), (1, 1, 0))
		self.assertEqual(self.access(open_case).source, "Backfill")
		self.assertIsNone(self.access(closed))
		rows = self.link_rows(open_case)
		self.assertEqual(
			[(row.event, row.status) for row in rows], [(C.EVENT_GUEST_ACCESS_BACKFILL, "Queued")]
		)
		self.assertIn("You can now view your grievance", rows[0].message)
		# Left to the notification sweep, which paces a large batch: nothing is dispatched now.
		enqueue.assert_not_called()

	def test_ac19_running_the_backfill_again_sends_nothing_new(self):
		case = self.an_open_case()
		with _no_commit(), _only_candidates(case.name):
			first = guest_access.backfill_existing(dry_run=False)
			second = guest_access.backfill_existing(dry_run=False)
		self.assertEqual(first["issued"], 1)
		self.assertEqual((second["matching"], second["issued"]), (0, 0))
		self.assertEqual(len(self.link_rows(case)), 1)
