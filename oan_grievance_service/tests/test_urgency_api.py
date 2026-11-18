"""The configuration API of sensitive, urgent and seasonal handling (STG-514)."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.taxonomy import (
	create_grievance_type,
	list_grievance_types,
	list_urgency_seasons,
	set_belg_area,
	update_grievance_type,
	update_urgency_season,
)
from oan_grievance_service.services import classification
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_service_category, an_administrative_area
from oan_grievance_service.tests.test_category_assignment import _keep_transaction
from oan_grievance_service.tests.test_classification import a_season, a_type

DEFAULT_WINDOW = {"applies_to": "Default", "start_month": 10, "start_day": 1, "end_month": 4, "end_day": 30}


class UrgencyApiFixture(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.savepoint = "urgency_api"
		frappe.db.savepoint(self.savepoint)
		self.category = a_service_category()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point=self.savepoint)
		super().tearDown()

	def a_season(self, **values):
		return a_season("Api", [{**DEFAULT_WINDOW}], **values)


class TestGrievanceTypeFlags(UrgencyApiFixture):
	def create(self, **extra):
		return create_grievance_type(
			service_category=self.category, type_name=f"Flags {frappe.generate_hash(length=6)}", **extra
		)

	def test_an_administrator_creates_a_sensitive_type(self):
		result = self.create(is_sensitive=True)
		self.assertEqual(result["status"], "success", msg=result)
		record = result["data"]["grievance_type"]
		self.assertEqual(
			(record["is_sensitive"], record["is_urgent"], record["urgency_season"]), (True, False, None)
		)

	def test_an_administrator_creates_an_urgent_type_with_its_season(self):
		season = self.a_season()
		record = self.create(is_urgent=True, urgency_season=season)["data"]["grievance_type"]
		self.assertEqual((record["is_urgent"], record["urgency_season"]), (True, season))

	def test_an_urgent_type_without_a_season_is_a_validation_error(self):
		with _keep_transaction():
			result = self.create(is_urgent=True)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_a_type_is_updated_to_sensitive_and_back(self):
		kind = self.create()["data"]["grievance_type"]["grievance_type_id"]
		on = update_grievance_type(kind, is_sensitive=True)
		self.assertTrue(on["data"]["grievance_type"]["is_sensitive"], msg=on)
		off = update_grievance_type(kind, is_sensitive=False)
		self.assertFalse(off["data"]["grievance_type"]["is_sensitive"], msg=off)

	def test_a_season_is_cleared_with_null(self):
		season = self.a_season()
		kind = self.create(urgency_season=season)["data"]["grievance_type"]["grievance_type_id"]
		cleared = update_grievance_type(kind, urgency_season=None, is_urgent=False)
		self.assertIsNone(cleared["data"]["grievance_type"]["urgency_season"], msg=cleared)

	def test_the_list_shows_the_flags(self):
		season = self.a_season()
		self.create(is_sensitive=True, is_urgent=True, urgency_season=season)
		listed = list_grievance_types(service_category=self.category, page_size=100)["data"][
			"grievance_types"
		]
		self.assertTrue(
			any(r["is_sensitive"] and r["is_urgent"] and r["urgency_season"] == season for r in listed)
		)

	def test_only_an_administrator_sets_the_flags(self):
		kind = self.create()["data"]["grievance_type"]["grievance_type_id"]
		with (
			_keep_transaction(),
			mock.patch("oan_grievance_service.api.v1.taxonomy.is_unrestricted", return_value=False),
		):
			for result in (
				update_grievance_type(kind, is_sensitive=True),
				update_grievance_type(kind, is_urgent=False),
				update_grievance_type(kind, urgency_season=None),
				self.create(is_sensitive=True),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
			# The name stays with the category head.
			self.assertEqual(update_grievance_type(kind, type_name="Renamed by head")["status"], "success")

	def test_a_flag_set_through_the_api_changes_how_the_next_case_is_classified(self):
		kind = self.create()["data"]["grievance_type"]["grievance_type_id"]
		self.assertFalse(classification.classify(kind).is_sensitive)
		update_grievance_type(kind, is_sensitive=True)
		self.assertTrue(classification.classify(kind).is_sensitive)


class TestUrgencySeasonApi(UrgencyApiFixture):
	def test_the_list_shows_the_windows_in_both_calendars(self):
		name = a_season(
			"Both",
			[
				{
					**DEFAULT_WINDOW,
					"eth_start_month": "Tikimt",
					"eth_start_day": 1,
					"eth_end_month": "Miyazya",
					"eth_end_day": 30,
				}
			],
		)
		kind = a_type(name, urgent=1)
		seasons = {s["season_name"]: s for s in list_urgency_seasons()["data"]["urgency_seasons"]}
		season = seasons[name]
		self.assertEqual(season["windows"][0]["start_month"], 10)
		self.assertEqual(season["windows"][0]["eth_start_month"], "Tikimt")
		self.assertEqual(season["windows"][0]["eth_end_month"], "Miyazya")
		self.assertEqual(
			(season["handling_class"], season["priority"]), (C.HANDLING_URGENT, C.PRIORITY_URGENT)
		)
		self.assertEqual(season["grievance_type_count"], 1)
		self.assertTrue(frappe.db.exists("Grievance Type", kind))

	def test_the_list_shows_an_all_year_season(self):
		name = a_season("Year", all_year=1)
		season = {s["season_name"]: s for s in list_urgency_seasons()["data"]["urgency_seasons"]}[name]
		self.assertEqual((season["all_year"], season["windows"]), (True, []))

	def test_a_window_is_changed_without_code(self):
		name = self.a_season()
		new_window = {
			"applies_to": "Default",
			"start_month": 11,
			"start_day": 15,
			"end_month": 3,
			"end_day": 31,
		}
		result = update_urgency_season(name, windows=[new_window])
		self.assertEqual(result["status"], "success", msg=result)
		window = result["data"]["urgency_season"]["windows"][0]
		self.assertEqual((window["start_month"], window["start_day"], window["end_month"]), (11, 15, 3))
		# The classifier reads the new window for the next case.
		kind = a_type(name, urgent=1)
		from datetime import date

		self.assertEqual(
			classification.classify(kind, None, date(2026, 10, 20)).handling_class, C.HANDLING_STANDARD
		)
		self.assertEqual(
			classification.classify(kind, None, date(2026, 11, 15)).handling_class, C.HANDLING_URGENT
		)

	def test_a_woreda_override_is_added_through_the_api(self):
		name = self.a_season()
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		override = {
			"applies_to": "Specific Area",
			"administrative_area": woreda,
			"start_month": 12,
			"start_day": 1,
			"end_month": 2,
			"end_day": 28,
		}
		result = update_urgency_season(name, windows=[DEFAULT_WINDOW, override])
		self.assertEqual(result["status"], "success", msg=result)
		rows = result["data"]["urgency_season"]["windows"]
		self.assertEqual([row["applies_to"] for row in rows], ["Default", "Specific Area"])
		self.assertEqual(rows[1]["administrative_area"], woreda)

	def test_handling_class_priority_and_active_flag_are_updated(self):
		name = self.a_season()
		result = update_urgency_season(name, handling_class="Standard", priority="High")
		season = result["data"]["urgency_season"]
		self.assertEqual((season["handling_class"], season["priority"]), ("Standard", "High"))

	def test_an_invalid_window_is_a_validation_error(self):
		name = self.a_season()
		with _keep_transaction():
			result = update_urgency_season(
				name,
				windows=[
					{"applies_to": "Default", "start_month": 2, "start_day": 30, "end_month": 3, "end_day": 1}
				],
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_an_update_with_nothing_to_change_is_refused(self):
		name = self.a_season()
		with _keep_transaction():
			self.assertEqual(update_urgency_season(name)["code"], "VALIDATION_ERROR")

	def test_a_season_in_use_by_an_urgent_type_cannot_be_deactivated(self):
		name = self.a_season()
		a_type(name, urgent=1)
		with _keep_transaction():
			result = update_urgency_season(name, is_active=False)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_an_officer_cannot_change_a_season(self):
		name = self.a_season()
		officer = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"o-{frappe.generate_hash(length=5)}@test.local",
				"first_name": "o",
				"send_welcome_email": 0,
				"roles": [{"role": "Grievance Officer"}],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(officer.name)
		with _keep_transaction():
			self.assertEqual(update_urgency_season(name, priority="High")["code"], "PERMISSION_DENIED")


class TestBelgAreaApi(UrgencyApiFixture):
	def test_a_woreda_is_marked_belg_and_unmarked(self):
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		on = set_belg_area(woreda, is_belg=True)
		self.assertEqual(on["status"], "success", msg=on)
		self.assertTrue(on["data"]["is_belg"])
		self.assertEqual(frappe.db.get_value("Grievance Administrative Area", woreda, "is_belg"), 1)
		off = set_belg_area(woreda, is_belg=False)
		self.assertFalse(off["data"]["is_belg"])

	def test_a_kebele_cannot_be_marked_belg(self):
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		kebele = an_administrative_area(f"Kebele {frappe.generate_hash(length=5)}", woreda, "Kebele")
		with _keep_transaction():
			result = set_belg_area(kebele, is_belg=True)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_marking_a_woreda_belg_through_the_api_switches_the_c01_window(self):
		from datetime import date

		meher = a_season(
			"Meher",
			[
				{**DEFAULT_WINDOW, "start_month": 5, "start_day": 15, "end_month": 8, "end_day": 31},
				{
					"applies_to": "Belg Woreda",
					"start_month": 2,
					"start_day": 1,
					"end_month": 4,
					"end_day": 30,
				},
			],
		)
		c01 = a_type(meher, urgent=1)
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		self.assertEqual(
			classification.classify(c01, woreda, date(2026, 3, 1)).handling_class, C.HANDLING_STANDARD
		)
		set_belg_area(woreda, is_belg=True)
		self.assertEqual(
			classification.classify(c01, woreda, date(2026, 3, 1)).handling_class, C.HANDLING_URGENT
		)
		self.assertEqual(
			classification.classify(c01, woreda, date(2026, 7, 1)).handling_class, C.HANDLING_STANDARD
		)

	def test_a_new_area_is_not_belg(self):
		"""No official Belg list exists, so nothing is preset: a woreda is Belg only when marked."""
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		self.assertEqual(frappe.db.get_value("Grievance Administrative Area", woreda, "is_belg"), 0)


class TestHandlingFieldsOnEveryWritePath(UrgencyApiFixture):
	"""Only an administrator sets a type's handling fields, through the Desk and /api/resource too."""

	def a_desk_head(self):
		return (
			frappe.get_doc(
				{
					"doctype": "User",
					"email": f"head-{frappe.generate_hash(length=5)}@test.local",
					"first_name": "head",
					"send_welcome_email": 0,
					"roles": [{"role": "Grievance Desk Head"}, {"role": "Grievance Officer"}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def a_sensitive_type(self):
		return a_type(sensitive=1)

	def test_a_desk_head_cannot_unflag_a_sensitive_type(self):
		kind = frappe.get_doc("Grievance Type", self.a_sensitive_type())
		frappe.set_user(self.a_desk_head())
		kind.is_sensitive = 0
		# Past the DocPerm and hook checks, as a Desk or /api/resource save of a head's own type is.
		with self.assertRaises(frappe.PermissionError):
			kind.save(ignore_permissions=True)

	def test_a_desk_head_cannot_make_a_type_urgent_or_change_its_season(self):
		season = self.a_season()
		head = self.a_desk_head()
		for change in ({"is_urgent": 1, "urgency_season": season}, {"urgency_season": season}):
			kind = frappe.get_doc("Grievance Type", a_type())
			frappe.set_user(head)
			kind.update(change)
			with self.subTest(change=change), self.assertRaises(frappe.PermissionError):
				kind.save(ignore_permissions=True)
			frappe.set_user("Administrator")

	def test_a_desk_head_cannot_create_a_sensitive_type(self):
		frappe.set_user(self.a_desk_head())
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": f"Head {frappe.generate_hash(length=6)}",
					"service_category": self.category,
					"is_sensitive": 1,
				}
			).insert(ignore_permissions=True)

	def test_a_desk_head_still_renames_a_sensitive_type(self):
		kind = frappe.get_doc("Grievance Type", self.a_sensitive_type())
		frappe.set_user(self.a_desk_head())
		kind.type_name = f"Renamed {frappe.generate_hash(length=6)}"
		kind.save(ignore_permissions=True)
		self.assertEqual(kind.is_sensitive, 1)

	def test_an_administrator_changes_the_flags_and_it_is_in_the_history(self):
		kind = frappe.get_doc("Grievance Type", self.a_sensitive_type())
		kind.is_sensitive = 0
		# Frappe writes no Version while `in_test` is set; a real save does.
		with mock.patch.object(frappe, "in_test", False):
			kind.save(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": "Grievance Type", "docname": kind.name}))


class TestUrgencySeasonApiGuards(UrgencyApiFixture):
	def test_a_season_used_by_an_urgent_type_cannot_be_made_standard(self):
		name = self.a_season()
		a_type(name, urgent=1)
		with _keep_transaction():
			result = update_urgency_season(name, handling_class="Standard")
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_the_season_list_is_paged(self):
		for _ in range(3):
			self.a_season()
		total = frappe.db.count("Grievance Urgency Season")
		first = list_urgency_seasons(page_size=2)["data"]
		self.assertEqual(len(first["urgency_seasons"]), 2)
		self.assertEqual(first["pagination"]["total_count"], total)
		names = [s["season_name"] for s in first["urgency_seasons"]]
		second = list_urgency_seasons(page=2, page_size=2)["data"]["urgency_seasons"]
		self.assertFalse(set(names) & {s["season_name"] for s in second})

	def test_the_season_list_filters_on_active(self):
		retired = self.a_season(is_active=0)
		active = list_urgency_seasons(is_active=True, page_size=100)["data"]["urgency_seasons"]
		self.assertNotIn(retired, {s["season_name"] for s in active})
