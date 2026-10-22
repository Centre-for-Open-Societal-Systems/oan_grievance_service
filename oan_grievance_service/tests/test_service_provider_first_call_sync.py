# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""The first call to the search endpoint fills a never-synced directory before it answers.

Only the A2C HTTP layer is mocked. The sources of earlier tests and of the dev site are switched
off in setUp, so each test sees just its own source.
"""

from datetime import timedelta
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from oan_grievance_service.api.v1.service_provider import get_service_provider, list_service_providers
from oan_grievance_service.services import service_provider_sync as sync
from oan_grievance_service.services.service_provider_adapters import REQUEST_TIMEOUT
from oan_grievance_service.tests.fixtures import a_service_provider, a_service_provider_source
from oan_grievance_service.tests.test_category_assignment import _user
from oan_grievance_service.tests.test_service_provider_sync import HTTP, FakeA2C, FakeResponse, bank

PROVIDER = "Grievance Service Provider"
SOURCE = "Grievance Service Provider Source"


class TestServiceProviderFirstCallSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		# A user who can read providers and nothing else: the first-call sync writes, so it must not
		# depend on the caller being allowed to.
		cls.reader = _user(
			f"first-call-{frappe.generate_hash(length=6)}@example.com",
			"First Caller",
			role="Grievance Submitter",
		)

	def setUp(self):
		frappe.set_user("Administrator")
		# The first-call sync commits, as a GET request must. A real commit here would write these
		# fixtures into the site's database and fire the "source enabled" job for fake sources, so
		# no test in this class may reach a real commit.
		commit = mock.patch.object(frappe.db, "commit")
		self.commit = commit.start()
		self.addCleanup(commit.stop)
		frappe.db.set_value(SOURCE, {"is_enabled": 1}, "is_enabled", 0)
		self.source = a_service_provider_source(f"First {frappe.generate_hash(length=6)}")
		self.fake = FakeA2C([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])

	def tearDown(self):
		frappe.set_user("Administrator")

	def _call(self, fake=None, **params):
		frappe.set_user(self.reader)
		with mock.patch(HTTP, fake or self.fake):
			result = list_service_providers(**params)
		frappe.set_user("Administrator")
		return result

	def _rows(self):
		return frappe.get_all(PROVIDER, filters={"source": self.source}, fields=["provider_name", "owner"])

	def _status(self):
		return frappe.db.get_value(SOURCE, self.source, "last_sync_status")

	# --- what the first caller gets -------------------------------------------------------------

	def test_the_first_call_fills_the_directory_and_returns_the_list_in_the_same_response(self):
		result = self._call(search="Bank")

		self.assertEqual(result["status"], "success", msg=result)
		returned = {item["name"] for item in result["data"]["items"]}
		self.assertTrue({"Alpha Bank", "Beta Bank"} <= returned, returned)
		self.assertEqual({r.provider_name for r in self._rows()}, {"Alpha Bank", "Beta Bank"})
		self.assertEqual(self._status(), "Success")

	def test_a_caller_who_may_only_read_still_gets_the_data_and_keeps_their_own_session(self):
		frappe.set_user(self.reader)
		with mock.patch(HTTP, self.fake):
			list_service_providers()
		self.assertEqual(frappe.session.user, self.reader, "the caller is restored after the sync")
		frappe.set_user("Administrator")

		self.assertEqual({r.owner for r in self._rows()}, {"Administrator"}, "the sync writes as the system")

	def test_it_commits_once_because_a_get_request_does_not_commit_by_itself(self):
		self._call()

		self.commit.assert_called_once()

	def test_a_call_that_syncs_nothing_does_not_commit(self):
		self._call()
		self.commit.reset_mock()

		self._call()

		self.commit.assert_not_called()

	# --- when it must not run -------------------------------------------------------------------

	def test_the_second_call_reads_the_table_and_does_not_sync_again(self):
		self._call()
		calls = len(self.fake.calls)

		self._call()

		self.assertEqual(len(self.fake.calls), calls)

	def test_a_source_that_has_already_synced_is_left_alone(self):
		for status in ("Success", "Partial"):
			with self.subTest(status=status):
				frappe.db.set_value(
					SOURCE, self.source, {"last_sync_status": status, "last_synced_at": now_datetime()}
				)
				self._call()
				self.assertEqual(self.fake.calls, [])

	def test_a_disabled_source_is_not_synced(self):
		frappe.db.set_value(SOURCE, self.source, "is_enabled", 0)

		self._call()

		self.assertEqual(self.fake.calls, [])

	def test_the_single_lookup_never_triggers_a_sync(self):
		provider = a_service_provider("Hand made")
		frappe.set_user(self.reader)
		with mock.patch(HTTP, self.fake):
			get_service_provider(provider)
		frappe.set_user("Administrator")

		self.assertEqual(self.fake.calls, [])

	# --- when the source is down ----------------------------------------------------------------

	def test_a_failing_source_still_gets_a_normal_answer_and_is_not_retried_during_the_cooldown(self):
		down = FakeA2C([], login=FakeResponse(503, {}))

		result = self._call(fake=down)

		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(self._rows(), [])
		self.assertEqual(self._status(), "Failed")
		self.assertEqual(len(down.calls), 1)

		self._call(fake=down)
		self.assertEqual(len(down.calls), 1, "a failure is not retried by every request")

		frappe.db.set_value(SOURCE, self.source, "last_synced_at", now_datetime() - timedelta(minutes=6))
		self._call(fake=down)
		self.assertEqual(len(down.calls), 2, "after the cooldown it tries again")

	def test_a_crash_in_the_sync_never_breaks_the_request(self):
		frappe.set_user(self.reader)
		with mock.patch.object(sync, "run_sync", side_effect=RuntimeError("boom")):
			with mock.patch.object(frappe, "log_error") as log:
				result = list_service_providers()
		self.assertEqual(frappe.session.user, self.reader)
		frappe.set_user("Administrator")

		self.assertEqual(result["status"], "success", msg=result)
		self.assertIn(self.source, log.call_args.kwargs["title"])

	# --- one run at a time ----------------------------------------------------------------------

	def _hold_lock(self):
		lock = frappe.cache.lock(frappe.cache.make_key(f"grievance_provider_sync:{self.source}"), timeout=30)
		self.assertTrue(lock.acquire(blocking=False))
		self.addCleanup(lock.release)

	def test_while_another_run_holds_the_lock_the_caller_gets_the_list_as_it_is(self):
		self._hold_lock()

		result = self._call()

		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(self.fake.calls, [])

	def test_the_background_job_skips_a_source_the_first_call_is_syncing(self):
		self._hold_lock()

		outcome = sync.sync_source(self.source)

		self.assertEqual(outcome["status"], "Skipped")
		self.assertEqual(self.fake.calls, [])

	def test_a_request_that_waited_for_the_lock_looks_again_before_syncing(self):
		"""Another request finished while this one waited: the fresh read says there is nothing to do."""
		with mock.patch.object(sync, "_awaiting_first_sync", side_effect=[[self.source], []]):
			with mock.patch(HTTP, self.fake):
				synced = sync.ensure_first_sync()

		self.assertEqual(synced, [])
		self.assertEqual(self.fake.calls, [])

	def test_it_reports_the_sources_it_synced(self):
		with mock.patch(HTTP, self.fake):
			self.assertEqual(sync.ensure_first_sync(), [self.source])
			self.assertEqual(sync.ensure_first_sync(), [])

	# --- timeouts -------------------------------------------------------------------------------

	def test_the_waiting_caller_gets_short_timeouts_and_the_background_run_the_long_ones(self):
		self._call()
		self.assertTrue(self.fake.calls)
		for call in self.fake.calls:
			self.assertEqual(call.kwargs["timeout"], sync.INLINE_TIMEOUT)
		self.assertLess(sync.INLINE_TIMEOUT[1], REQUEST_TIMEOUT[1])

		background = FakeA2C([bank("PB-1", "Alpha Bank")])
		with mock.patch(HTTP, background):
			sync.run_sync(self.source)
		self.assertEqual({c.kwargs["timeout"] for c in background.calls}, {REQUEST_TIMEOUT})
