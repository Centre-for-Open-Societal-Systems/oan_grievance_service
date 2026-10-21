# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""Service provider sync: copy a source's providers into the directory, and the admin endpoints.

Only the external HTTP layer is mocked (the A2C host is not called); everything else runs
against the real database.
"""

from types import SimpleNamespace
from unittest import mock

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.service_provider_source import (
	list_service_provider_sources,
	sync_service_provider_source,
)
from oan_grievance_service.services import service_provider_sync as sync
from oan_grievance_service.setup.install import seed_service_provider_sources
from oan_grievance_service.tests.fixtures import (
	a_service_provider,
	a_service_provider_source,
	an_administrative_area,
)
from oan_grievance_service.tests.test_category_assignment import _keep_transaction, _user

PROVIDER = "Grievance Service Provider"
SOURCE = "Grievance Service Provider Source"
HTTP = "oan_grievance_service.services.service_provider_adapters.requests.request"
TOKEN = "tok-for-one-sync"
PASSWORD = "not-a-real-password"


class FakeResponse:
	def __init__(self, status=200, payload=None, not_json=False):
		self.status_code = status
		self._payload = payload
		self._not_json = not_json

	def json(self):
		if self._not_json:
			raise ValueError("not json")
		return self._payload


class FakeA2C:
	"""Stands in for requests.request: answers the login and the list call, and records them."""

	def __init__(self, banks, login=None, listing=None, raises=None):
		self.calls = []
		self.login = login or FakeResponse(200, {"data": {"token": TOKEN}})
		self.listing = listing or FakeResponse(200, {"data": {"banks": banks}})
		self.raises = raises

	def __call__(self, method, url, **kwargs):
		self.calls.append(SimpleNamespace(method=method, url=url, kwargs=kwargs))
		if self.raises:
			raise self.raises
		return self.login if url.endswith("/v1/auth/login") else self.listing


def bank(ref, name, region=None, logo=None):
	"""One item of A2C's data.banks, with the keys A2C uses."""
	return {"name": ref, "bank_name": name, "logo": logo, "registered_region": region}


class TestServiceProviderSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.tag = frappe.generate_hash(length=6)
		country = an_administrative_area(f"Syncland {cls.tag}", None, "Country")
		cls.region_name = f"Region {cls.tag}"
		cls.region = an_administrative_area(cls.region_name, country, "Region")
		cls.other_region_name = f"Other {cls.tag}"
		cls.other_region = an_administrative_area(cls.other_region_name, country, "Region")
		cls.admin = _user(f"sp-sync-{cls.tag}-admin@example.com", "Sync Admin", role="Grievance Admin")
		cls.submitter = _user(
			f"sp-sync-{cls.tag}-sub@example.com", "Sync Submitter", role="Grievance Submitter"
		)

	def setUp(self):
		self.source = a_service_provider_source(f"A2C {frappe.generate_hash(length=6)}")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _run(self, banks=None, source=None, **fake):
		fake = FakeA2C(banks if banks is not None else [], **fake)
		with mock.patch(HTTP, fake):
			result = sync.run_sync(source or self.source)
		return result, fake

	def _providers(self, source=None):
		rows = frappe.get_all(
			PROVIDER,
			filters={"source": source or self.source},
			fields=[
				"name",
				"external_ref",
				"provider_name",
				"provider_type",
				"administrative_area",
				"registered_region",
				"logo",
				"is_active",
			],
		)
		return {row.external_ref: row for row in rows}

	def _status(self, source=None):
		return frappe.db.get_value(
			SOURCE, source or self.source, ["last_sync_status", "last_sync_message"], as_dict=True
		)

	# --- the happy path --------------------------------------------------------------------

	def test_a_first_sync_creates_the_providers_with_type_source_and_area(self):
		result, fake = self._run(
			[bank("PB-1", "Alpha Bank", self.region_name), bank("PB-2", "Beta Bank", self.other_region_name)]
		)

		self.assertEqual((result["status"], result["created"]), ("Success", 2))
		providers = self._providers()
		self.assertEqual(set(providers), {"PB-1", "PB-2"})
		self.assertEqual(providers["PB-1"].provider_name, "Alpha Bank")
		self.assertEqual(providers["PB-1"].provider_type, "Bank")
		self.assertEqual(providers["PB-1"].administrative_area, self.region)
		self.assertEqual(providers["PB-2"].administrative_area, self.other_region)
		self.assertTrue(all(p.is_active for p in providers.values()))
		self.assertRegex(providers["PB-1"].name, r"^SP-\d{5}$")

		status = self._status()
		self.assertEqual(status.last_sync_status, "Success")
		self.assertIn("2 created, 0 updated, 0 unchanged, 0 removed, 0 deactivated", status.last_sync_message)
		self.assertTrue(frappe.db.get_value(SOURCE, self.source, "last_synced_at"))

	def test_the_sync_logs_in_then_reads_the_list_with_that_token(self):
		_, fake = self._run([bank("PB-1", "Alpha Bank")])

		login, listing = fake.calls
		self.assertEqual((login.method, login.url), ("POST", "https://a2c.example.test/api/v1/auth/login"))
		self.assertEqual(login.kwargs["json"], {"usr": "sync-user", "pwd": PASSWORD})
		self.assertEqual(
			(listing.method, listing.url), ("GET", "https://a2c.example.test/api/v1/catalog/facets")
		)
		self.assertEqual(listing.kwargs["headers"], {"Authorization": f"Bearer {TOKEN}"})
		self.assertTrue(login.kwargs["timeout"] and listing.kwargs["timeout"])

	def test_a_stored_token_is_used_without_a_login(self):
		source = a_service_provider_source(
			f"Token {self.tag}",
			auth_mode="Token",
			api_token="stored-long-lived",
			api_username="",
			api_password="",
		)
		result, fake = self._run([bank("PB-1", "Alpha Bank")], source=source)

		self.assertEqual(result["status"], "Success")
		self.assertEqual(len(fake.calls), 1)
		self.assertEqual(fake.calls[0].kwargs["headers"], {"Authorization": "Bearer stored-long-lived"})

	def test_syncing_again_changes_nothing(self):
		banks = [bank("PB-1", "Alpha Bank", self.region_name)]
		self._run(banks)
		ids = {r: p.name for r, p in self._providers().items()}

		result, _ = self._run(banks)

		self.assertEqual((result["created"], result["updated"], result["unchanged"]), (0, 0, 1))
		self.assertEqual({r: p.name for r, p in self._providers().items()}, ids)

	def test_a_renamed_or_moved_provider_keeps_its_id(self):
		self._run([bank("PB-1", "Alpha Bank", self.region_name)])
		before = self._providers()["PB-1"].name

		result, _ = self._run([bank("PB-1", "Alpha Bank Share Co.", self.other_region_name)])

		self.assertEqual((result["created"], result["updated"]), (0, 1))
		after = self._providers()["PB-1"]
		self.assertEqual(after.name, before)
		self.assertEqual(after.provider_name, "Alpha Bank Share Co.")
		self.assertEqual(after.administrative_area, self.other_region)

	def test_the_id_region_and_logo_are_stored_exactly_as_a2c_sent_them(self):
		# Three of the real A2C dev banks: a region that matches an area, a logo, and a region that matches nothing.
		self._run(
			[
				bank("PB-0241", "Test Bank 9711", self.region_name),
				bank("PB-0308", "coopbank", "region", logo="/files/product-imaged05e46.jpg"),
				bank("PB-0452", "HDFC Bank", "Test4"),
			]
		)

		providers = self._providers()
		self.assertEqual(
			(providers["PB-0241"].registered_region, providers["PB-0241"].administrative_area),
			(self.region_name, self.region),
		)
		self.assertEqual(providers["PB-0308"].logo, "/files/product-imaged05e46.jpg")
		# The text survives although it matches no area, so nothing A2C sent is lost.
		self.assertEqual(
			(
				providers["PB-0452"].registered_region,
				providers["PB-0452"].administrative_area,
				providers["PB-0452"].logo,
			),
			("Test4", None, None),
		)

	def test_a_changed_or_removed_logo_updates_the_provider_in_place(self):
		self._run([bank("PB-1", "Alpha Bank", logo="/files/a.png")])
		before = self._providers()["PB-1"].name

		changed, _ = self._run([bank("PB-1", "Alpha Bank", logo="/files/b.png")])
		self.assertEqual((changed["updated"], self._providers()["PB-1"].logo), (1, "/files/b.png"))

		same, _ = self._run([bank("PB-1", "Alpha Bank", logo="/files/b.png")])
		self.assertEqual((same["updated"], same["unchanged"]), (0, 1))

		cleared, _ = self._run([bank("PB-1", "Alpha Bank")])
		self.assertEqual((cleared["updated"], self._providers()["PB-1"].logo), (1, None))
		self.assertEqual(self._providers()["PB-1"].name, before)

	def test_a_source_with_no_logo_key_stores_no_logo(self):
		frappe.db.set_value(SOURCE, self.source, "map_logo", "")

		self._run([bank("PB-1", "Alpha Bank", logo="/files/a.png")])

		self.assertIsNone(self._providers()["PB-1"].logo)

	# --- providers that leave and return --------------------------------------------------------

	def test_a_provider_the_source_drops_is_removed_and_the_survivors_keep_their_ids(self):
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])
		ids = {ref: p.name for ref, p in self._providers().items()}

		result, _ = self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual((result["removed"], result["deactivated"]), (1, 0))
		self.assertEqual(set(self._providers()), {"PB-1"})
		self.assertFalse(frappe.db.exists(PROVIDER, ids["PB-2"]))
		self.assertEqual(self._providers()["PB-1"].name, ids["PB-1"])
		self.assertIn("1 removed", self._status().last_sync_message)

	def test_a_removed_provider_is_archived_so_it_can_be_restored(self):
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])
		gone = self._providers()["PB-2"].name

		self._run([bank("PB-1", "Alpha Bank")])

		self.assertTrue(
			frappe.db.exists("Deleted Document", {"deleted_doctype": PROVIDER, "deleted_name": gone})
		)

	def test_each_run_leaves_exactly_what_the_source_holds(self):
		self._run([bank("PB-1", "One"), bank("PB-2", "Two"), bank("PB-3", "Three")])
		ids = {ref: p.name for ref, p in self._providers().items()}

		result, _ = self._run([bank("PB-2", "Two"), bank("PB-3", "Three"), bank("PB-4", "Four")])

		self.assertEqual((result["created"], result["removed"], result["unchanged"]), (1, 1, 2))
		providers = self._providers()
		self.assertEqual(set(providers), {"PB-2", "PB-3", "PB-4"})
		self.assertEqual((providers["PB-2"].name, providers["PB-3"].name), (ids["PB-2"], ids["PB-3"]))
		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source}), 3)

	def test_a_provider_that_is_still_in_use_is_deactivated_instead_of_removed(self):
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])
		head_office = self._providers()["PB-2"].name
		branch = a_service_provider(f"Branch {self.tag}", provider_type="Bank", parent_provider=head_office)

		result, _ = self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual((result["removed"], result["deactivated"]), (0, 1))
		self.assertEqual(self._providers()["PB-2"].is_active, 0)
		self.assertEqual(frappe.db.get_value(PROVIDER, branch, "parent_provider"), head_office)
		self.assertIn("1 deactivated", self._status().last_sync_message)

	def test_a_provider_the_source_dropped_and_brings_back_is_added_again(self):
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])
		self._run([bank("PB-1", "Alpha Bank")])

		result, _ = self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])

		self.assertEqual((result["created"], result["unchanged"]), (1, 1))
		self.assertEqual(self._providers()["PB-2"].is_active, 1)
		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source}), 2)

	def test_a_provider_an_admin_deactivated_and_the_source_dropped_is_removed_too(self):
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])
		frappe.db.set_value(PROVIDER, self._providers()["PB-2"].name, "is_active", 0)

		self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual(set(self._providers()), {"PB-1"})

	def test_a_provider_the_source_still_lists_is_active_again_after_the_next_sync(self):
		self._run([bank("PB-1", "Alpha Bank")])
		frappe.db.set_value(PROVIDER, self._providers()["PB-1"].name, "is_active", 0)

		result, _ = self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual(result["updated"], 1)
		self.assertEqual(self._providers()["PB-1"].is_active, 1)

	def test_the_sync_leaves_other_sources_and_hand_made_providers_alone(self):
		other = a_service_provider_source(f"Other {self.source}")
		theirs = a_service_provider("Theirs", provider_type="Bank", source=other, external_ref="PB-1")
		manual = a_service_provider(f"Manual {self.tag}")
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])

		self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual(frappe.db.get_value(PROVIDER, theirs, ["is_active", "provider_name"]), (1, "Theirs"))
		self.assertEqual(frappe.db.get_value(PROVIDER, manual, "is_active"), 1)
		self.assertEqual(self._providers(other)["PB-1"].name, theirs)

	def test_an_empty_list_changes_nothing_and_fails_the_run(self):
		self._run([bank("PB-1", "Alpha Bank")])

		result, _ = self._run([])

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(self._providers()["PB-1"].is_active, 1)
		self.assertIn("no providers", self._status().last_sync_message)

	# --- what the source gets wrong -----------------------------------------------------------

	def test_an_unknown_region_leaves_the_area_empty_and_only_warns(self):
		result, _ = self._run([bank("PB-1", "Alpha Bank", "Test4"), bank("PB-2", "Beta Bank", "region")])

		self.assertEqual(result["status"], "Success")
		self.assertTrue(all(p.administrative_area is None for p in self._providers().values()))
		message = self._status().last_sync_message
		self.assertIn("Warnings (1)", message)
		self.assertIn("Test4", message)
		self.assertIn("region", message)

	def test_items_without_an_id_or_name_or_repeating_an_id_are_skipped_with_a_warning(self):
		items = [
			bank("PB-1", "Alpha Bank"),
			bank("", "No ID"),
			bank("PB-3", ""),
			bank("PB-1", "Alpha Again"),
			"not an object",
		]
		result, _ = self._run(items)

		self.assertEqual((result["status"], result["created"]), ("Success", 1))
		self.assertEqual(self._providers()["PB-1"].provider_name, "Alpha Bank")
		self.assertIn("Warnings (4)", self._status().last_sync_message)

	def test_one_bad_provider_makes_the_run_partial_and_does_not_stop_the_others(self):
		self._run([bank("PB-9", "Old Name")])

		result, _ = self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "x" * 200), bank("PB-9", "Old Name")])

		self.assertEqual(result["status"], "Partial")
		self.assertEqual(set(self._providers()), {"PB-1", "PB-9"})
		message = self._status().last_sync_message
		self.assertIn("Errors (1)", message)
		self.assertIn("PB-2", message)

	def test_a_provider_that_failed_to_save_is_not_removed_for_it(self):
		self._run([bank("PB-1", "Alpha Bank")])

		result, _ = self._run([bank("PB-1", "y" * 200)])

		self.assertEqual(result["status"], "Partial")
		self.assertEqual(self._providers()["PB-1"].is_active, 1)
		self.assertEqual((result["removed"], result["deactivated"]), (0, 0))

	# --- the source cannot be read ------------------------------------------------------------

	def test_a_refused_login_fails_the_run_without_leaking_secrets_or_changing_data(self):
		self._run([bank("PB-1", "Alpha Bank")])

		result, _ = self._run([], login=FakeResponse(401, {"error": "Unauthorized", "message": TOKEN}))

		self.assertEqual(result["status"], "Failed")
		message = self._status().last_sync_message
		self.assertIn("HTTP 401", message)
		for secret in (TOKEN, PASSWORD, "sync-user"):
			self.assertNotIn(secret, message)
		self.assertEqual(self._providers()["PB-1"].is_active, 1)

	def test_an_unreachable_source_fails_the_run(self):
		result, _ = self._run([], raises=requests.ConnectionError("dns: a2c.example.test"))

		self.assertEqual(result["status"], "Failed")
		message = self._status().last_sync_message
		self.assertIn("ConnectionError", message)
		self.assertNotIn("dns:", message)

	def test_answers_that_are_not_usable_fail_the_run(self):
		cases = {
			"no token": {"login": FakeResponse(200, {"data": {}})},
			"login not json": {"login": FakeResponse(200, not_json=True)},
			"list refused": {"listing": FakeResponse(403, {})},
			"list not json": {"listing": FakeResponse(200, not_json=True)},
			"wrong list key": {"listing": FakeResponse(200, {"data": {"other": []}})},
			"list is not a list": {"listing": FakeResponse(200, {"data": {"banks": "nope"}})},
		}
		for label, fake in cases.items():
			with self.subTest(label):
				result, _ = self._run([], **fake)
				self.assertEqual(result["status"], "Failed", msg=result)
				self.assertEqual(self._providers(), {})

	def test_a_crash_while_saving_rolls_everything_back(self):
		def crash(source, rows, warnings):
			a_service_provider("Half done", source=source.name, external_ref="PB-1", provider_type="Bank")
			raise RuntimeError("database went away")

		with mock.patch.object(sync, "_apply", crash), mock.patch.object(frappe, "log_error") as log:
			result, _ = self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(self._providers(), {})
		self.assertIn("Error Log", self._status().last_sync_message)
		self.assertNotIn("database went away", self._status().last_sync_message)
		log.assert_called_once()

	def test_a_disabled_source_is_not_synced(self):
		frappe.db.set_value(SOURCE, self.source, "is_enabled", 0)

		result, fake = self._run([bank("PB-1", "Alpha Bank")])

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(fake.calls, [])

	# --- queueing -----------------------------------------------------------------------------

	def test_enqueue_queues_one_deduplicated_job_per_source(self):
		job = SimpleNamespace(id="site:job-1")
		with mock.patch.object(frappe, "enqueue", return_value=job) as enqueue:
			job_id = sync.enqueue_sync(self.source)

		self.assertEqual(job_id, "site:job-1")
		_, kwargs = enqueue.call_args
		self.assertEqual(enqueue.call_args.args, (sync.JOB_METHOD,))
		self.assertEqual(kwargs["queue"], "long")
		self.assertEqual(kwargs["job_id"], f"grievance_service_provider_sync:{self.source}")
		self.assertIs(kwargs["deduplicate"], True)
		self.assertEqual(kwargs["source_name"], self.source)

	def test_enqueue_refuses_when_a_sync_is_already_running(self):
		with mock.patch.object(frappe, "enqueue", return_value=None):
			with self.assertRaises(sync.SyncInProgress) as raised:
				sync.enqueue_sync(self.source)
		self.assertEqual(raised.exception.http_status_code, 409)

	# --- the schedule -------------------------------------------------------------------------

	def test_the_daily_job_queues_every_enabled_source_and_no_disabled_one(self):
		disabled = a_service_provider_source(f"Off {self.tag}", is_enabled=0)
		with mock.patch.object(frappe, "enqueue", return_value=SimpleNamespace(id="j")) as enqueue:
			queued = sync.enqueue_scheduled_syncs()

		self.assertIn(self.source, queued)
		self.assertNotIn(disabled, queued)
		self.assertEqual(sorted(c.kwargs["source_name"] for c in enqueue.call_args_list), sorted(queued))

	def test_a_source_that_is_still_syncing_is_skipped_and_the_others_still_queue(self):
		busy = a_service_provider_source(f"Busy {self.tag}")

		def enqueue(method, **kwargs):
			return None if kwargs["source_name"] == busy else SimpleNamespace(id="j")

		with mock.patch.object(frappe, "enqueue", side_effect=enqueue):
			queued = sync.enqueue_scheduled_syncs()

		self.assertIn(self.source, queued)
		self.assertNotIn(busy, queued)

	def test_one_source_failing_to_queue_is_logged_and_does_not_stop_the_others(self):
		broken = a_service_provider_source(f"Broken {self.tag}")

		def enqueue(method, **kwargs):
			if kwargs["source_name"] == broken:
				raise ConnectionError("redis is down")
			return SimpleNamespace(id="j")

		with mock.patch.object(frappe, "enqueue", side_effect=enqueue):
			with mock.patch.object(frappe, "log_error") as log:
				queued = sync.enqueue_scheduled_syncs()

		self.assertIn(self.source, queued)
		self.assertNotIn(broken, queued)
		self.assertIn(broken, log.call_args.kwargs["title"])

	def test_the_scheduler_runs_the_job_daily(self):
		from oan_grievance_service import tasks

		hooks = frappe.get_hooks("scheduler_events", app_name="oan_grievance_service")
		self.assertIn("oan_grievance_service.tasks.enqueue_service_provider_syncs", hooks["daily"])
		with mock.patch.object(sync, "enqueue_scheduled_syncs", return_value=["x"]) as job:
			self.assertEqual(tasks.enqueue_service_provider_syncs(), ["x"])
		job.assert_called_once_with()

	# --- no duplicates ------------------------------------------------------------------------

	def test_running_the_job_again_and_again_never_adds_a_second_row_for_a_bank(self):
		banks = [bank("PB-1", "Alpha Bank", self.region_name), bank("PB-2", "Beta Bank")]
		for _run in range(4):
			self._run(banks)

		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source}), 2)
		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source, "external_ref": "PB-1"}), 1)

	def test_an_id_that_differs_only_in_case_or_padding_is_the_same_bank(self):
		self._run([bank("PB-1", "Alpha Bank")])
		before = self._providers()["PB-1"].name

		result, _ = self._run([bank("  pb-1 ", "Alpha Bank")])

		self.assertEqual((result["status"], result["created"], result["unchanged"]), ("Success", 0, 1))
		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source}), 1)
		self.assertEqual(self._providers()["PB-1"].name, before)

	def test_one_answer_listing_the_same_id_in_two_cases_stores_it_once(self):
		result, _ = self._run([bank("PB-1", "Alpha Bank"), bank("pb-1", "Alpha Again")])

		self.assertEqual((result["status"], result["created"]), ("Success", 1))
		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source}), 1)
		self.assertIn("appears twice", self._status().last_sync_message)

	def test_two_different_ids_with_the_same_name_are_two_providers(self):
		"""The ID is the identity, not the name: A2C dev has two banks both named "legel entity"."""
		self._run([bank("PB-0271", "legel entity"), bank("PB-0170", "legel entity")])

		self.assertEqual(set(self._providers()), {"PB-0271", "PB-0170"})

	def test_the_same_id_in_two_sources_is_kept_per_source(self):
		other = a_service_provider_source(f"Other {self.source}")
		self._run([bank("PB-1", "Alpha Bank")])
		self._run([bank("PB-1", "Alpha Bank")], source=other)

		self.assertEqual(frappe.db.count(PROVIDER, {"source": self.source, "external_ref": "PB-1"}), 1)
		self.assertEqual(frappe.db.count(PROVIDER, {"source": other, "external_ref": "PB-1"}), 1)

	def test_the_job_entry_point_runs_the_sync(self):
		with mock.patch(HTTP, FakeA2C([bank("PB-1", "Alpha Bank")])):
			result = sync.sync_source(self.source)
		self.assertEqual((result["status"], result["created"]), ("Success", 1))

	# --- endpoints ----------------------------------------------------------------------------

	def test_the_source_list_shows_status_and_counts_but_never_a_secret(self):
		self._run([bank("PB-1", "Alpha Bank"), bank("PB-2", "Beta Bank")])
		# A branch under PB-2 keeps it in use, so dropping it deactivates instead of removing.
		a_service_provider(
			f"Branch {self.tag}", provider_type="Bank", parent_provider=self._providers()["PB-2"].name
		)
		self._run([bank("PB-1", "Alpha Bank")])
		frappe.set_user(self.admin)

		result = list_service_provider_sources()

		self.assertEqual(result["status"], "success", msg=result)
		item = next(i for i in result["data"]["items"] if i["name"] == self.source)
		self.assertEqual(item["api_base_url"], "https://a2c.example.test/api")
		self.assertIs(item["credentials_configured"], True)
		self.assertEqual((item["provider_count"], item["active_provider_count"]), (2, 1))
		self.assertEqual(item["last_sync_status"], "Success")
		self.assertTrue(item["last_synced_at"])
		everything = repr(result)
		for secret in (PASSWORD, "sync-user", "api_password", "api_token"):
			self.assertNotIn(secret, everything)

	def test_the_seeded_source_is_listed_as_not_configured(self):
		seed_service_provider_sources()
		frappe.set_user(self.admin)
		items = {i["name"]: i for i in list_service_provider_sources()["data"]["items"]}
		self.assertIs(items["A2C Banks"]["credentials_configured"], False)

	def test_an_admin_can_queue_a_sync(self):
		frappe.set_user(self.admin)
		with mock.patch.object(frappe, "enqueue", return_value=SimpleNamespace(id="site:job-2")):
			result = sync_service_provider_source(self.source)

		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"], {"source": self.source, "job_id": "site:job-2", "queued": True})

	def test_queueing_a_sync_that_is_already_running_is_a_conflict(self):
		frappe.set_user(self.admin)
		with mock.patch.object(frappe, "enqueue", return_value=None):
			result = self._error(sync_service_provider_source, self.source)
		self.assertEqual(result["code"], "DUPLICATE_ENTRY", msg=result)
		self.assertIn("already running", result["message"])

	def test_an_unknown_or_disabled_source_cannot_be_synced(self):
		frappe.set_user(self.admin)
		unknown = self._error(sync_service_provider_source, f"No such source {self.tag}")
		self.assertEqual(unknown["code"], "NOT_FOUND", msg=unknown)

		frappe.db.set_value(SOURCE, self.source, "is_enabled", 0)
		disabled = self._error(sync_service_provider_source, self.source)
		self.assertEqual(disabled["code"], "VALIDATION_ERROR", msg=disabled)

	def test_only_admins_may_list_or_sync_sources(self):
		frappe.set_user(self.submitter)
		for result in (
			self._error(list_service_provider_sources),
			self._error(sync_service_provider_source, self.source),
		):
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def _error(self, fn, *args, **kwargs):
		with _keep_transaction():
			return fn(*args, **kwargs)
