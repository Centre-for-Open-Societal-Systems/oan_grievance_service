"""The Routing Matrix's sensitive and urgent columns, checked against the workbook itself.

The data in `setup/urgency_matrix.py` is read from the matrix. When the workbook is on the
machine (it is not in this repo), the tests read the Sensitive Flag and Urgent Flag columns from it
and compare, so a drift between the data and the matrix fails here and not in production.
"""

import os
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.patches import apply_sensitive_urgent_matrix
from oan_grievance_service.services import constants as C
from oan_grievance_service.setup import urgency_matrix as matrix
from oan_grievance_service.tests.fixtures import a_service_category

WORKBOOK = Path(
	os.environ.get(
		"OAN_ROUTING_MATRIX",
		Path.home() / "frappe16-bench" / "RefferDocs" / "OAN_Grievance_Routing_Matrix_D2.xlsx",
	)
)


class TestMatrixData(unittest.TestCase):
	def test_sensitive_is_exactly_the_eight_flagged_sub_types(self):
		self.assertEqual(
			set(matrix.SENSITIVE_TYPE_CODES),
			{"C13-01", "C13-02", "C13-03", "C13-04", "C15-01", "C15-02", "C15-03", "C16-01"},
		)

	def test_no_c04_or_c16_02_sub_type_is_sensitive(self):
		for code in matrix.SENSITIVE_TYPE_CODES:
			self.assertFalse(code.startswith("C04"), code)
		self.assertNotIn("C16-02", matrix.SENSITIVE_TYPE_CODES)

	def test_urgent_is_c01_c02_c07_and_c17_only(self):
		self.assertEqual(
			{code.split("-")[0] for code in matrix.URGENT_TYPE_CODES}, {"C01", "C02", "C07", "C17"}
		)
		self.assertEqual(len(matrix.URGENT_TYPE_CODES), 8 + 5 + 3 + 4)

	def test_c09_follows_its_season_without_being_urgent(self):
		c09 = {code: row for code, row in matrix.TYPE_SEASONS.items() if code.startswith("C09")}
		self.assertEqual(len(c09), 5)
		self.assertTrue(all(urgent == 0 for urgent, _season in c09.values()))
		self.assertFalse(any(code.startswith("C09") for code in matrix.URGENT_TYPE_CODES))

	def test_each_season_matches_the_seasonal_calendar(self):
		seasons = {season["season_name"]: season for season in matrix.SEASONS}
		meher = seasons["Meher input season"]
		default, belg = meher["windows"]
		self.assertEqual(
			(default["start_month"], default["start_day"], default["end_month"], default["end_day"]),
			(5, 15, 8, 31),
		)
		self.assertEqual(
			(
				default["eth_start_month"],
				default["eth_start_day"],
				default["eth_end_month"],
				default["eth_end_day"],
			),
			("Ginbot", 7, "Nehase", 25),
		)
		self.assertEqual((belg["start_month"], belg["end_month"]), (2, 4))
		self.assertEqual((belg["eth_start_month"], belg["eth_end_month"]), ("Tir", "Miyazya"))
		for name in ("Irrigation season (dry season)", "Dry / irrigation season"):
			window = seasons[name]["windows"][0]
			self.assertEqual((window["start_month"], window["end_month"]), (10, 4))
			self.assertEqual((window["eth_start_month"], window["eth_end_month"]), ("Tikimt", "Miyazya"))
		harvest = seasons["Harvest marketing peak"]
		self.assertEqual(
			(harvest["handling_class"], harvest["priority"]), (C.HANDLING_STANDARD, C.PRIORITY_HIGH)
		)
		self.assertEqual(
			(harvest["windows"][0]["eth_start_month"], harvest["windows"][0]["eth_end_month"]),
			("Tikimt", "Tahsas"),
		)
		pest = seasons["Pest and disease outbreak (all year)"]
		self.assertEqual((pest["all_year"], len(pest["windows"])), (1, 0))

	def test_every_season_a_type_follows_exists(self):
		names = {season["season_name"] for season in matrix.SEASONS}
		self.assertTrue(all(season in names for _urgent, season in matrix.TYPE_SEASONS.values()))


@unittest.skipUnless(WORKBOOK.exists(), "the Routing Matrix workbook is not on this machine")
class TestMatrixAgainstTheWorkbook(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		import openpyxl

		sheet = openpyxl.load_workbook(WORKBOOK, data_only=True)["Grievance Routing"]
		header = [cell for cell in next(sheet.iter_rows(values_only=True))]
		column = {
			name: header.index(name) for name in ("Code", "Sensitive Flag", "Urgent Flag", "Urgent Season")
		}
		cls.rows = {
			row[column["Code"]]: {name: row[index] for name, index in column.items()}
			for row in sheet.iter_rows(min_row=2, values_only=True)
			if row[column["Code"]] and re.match(r"^C\d\d-\d\d$", row[column["Code"]])
		}

	def test_the_sensitive_column_is_the_sensitive_list(self):
		flagged = {code for code, row in self.rows.items() if row["Sensitive Flag"] == "Yes"}
		self.assertEqual(flagged, set(matrix.SENSITIVE_TYPE_CODES))

	def test_the_urgent_column_is_the_urgent_list(self):
		flagged = {code for code, row in self.rows.items() if row["Urgent Flag"] == "Yes"}
		self.assertEqual(flagged, set(matrix.URGENT_TYPE_CODES))

	def test_every_urgent_row_names_a_season(self):
		for code in matrix.URGENT_TYPE_CODES:
			self.assertNotIn(self.rows[code]["Urgent Season"], (None, "—"), code)


class TestApplyMatrixPatch(FrappeTestCase):
	"""The patch fills the columns on types a site already holds, and nothing else."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.category = a_service_category()
		# A type code is unique, so any the site already holds (a seeded one) is set aside by
		# the class-level rollback; the types here use codes that cannot be seeded.
		self.saved = {}

	def a_type(self, code):
		existing = frappe.db.get_value("Grievance Type", {"type_code": code})
		if existing:
			frappe.db.set_value(
				"Grievance Type", existing, "type_code", f"X{frappe.generate_hash(length=8)}"[:16]
			)
		return (
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": f"{code} {frappe.generate_hash(length=6)}",
					"service_category": self.category,
					"type_code": code,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def values(self, name):
		return frappe.db.get_value(
			"Grievance Type", name, ["is_sensitive", "is_urgent", "urgency_season"], as_dict=True
		)

	def test_the_patch_flags_existing_types_and_creates_the_seasons(self):
		sensitive, urgent, harvest, plain = (self.a_type(c) for c in ("C13-03", "C17-02", "C09-01", "C03-01"))
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(self.values(sensitive).is_sensitive, 1)
		self.assertEqual(self.values(urgent).is_urgent, 1)
		self.assertEqual(self.values(urgent).urgency_season, "Pest and disease outbreak (all year)")
		self.assertEqual(
			(self.values(harvest).is_urgent, self.values(harvest).urgency_season),
			(0, "Harvest marketing peak"),
		)
		self.assertEqual(
			(
				self.values(plain).is_sensitive,
				self.values(plain).is_urgent,
				self.values(plain).urgency_season,
			),
			(0, 0, None),
		)
		for season in matrix.SEASONS:
			self.assertTrue(frappe.db.exists("Grievance Urgency Season", season["season_name"]))

	def test_the_patch_leaves_c16_02_and_c04_alone(self):
		wrong_data, imposed = self.a_type("C16-02"), self.a_type("C04-05")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(self.values(wrong_data).is_sensitive, 0)
		self.assertEqual(self.values(imposed).is_sensitive, 0)

	def test_the_patch_does_not_overwrite_an_administrators_season(self):
		custom = frappe.get_doc(
			{
				"doctype": "Grievance Urgency Season",
				"season_name": f"Custom {frappe.generate_hash(length=6)}",
				"all_year": 1,
				"handling_class": C.HANDLING_URGENT,
				"priority": C.PRIORITY_HIGH,
			}
		).insert(ignore_permissions=True)
		name = self.a_type("C01-03")
		frappe.db.set_value("Grievance Type", name, {"urgency_season": custom.name, "is_urgent": 1})
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(self.values(name).urgency_season, custom.name)

	def test_the_patch_is_safe_to_run_twice(self):
		self.a_type("C15-01")
		apply_sensitive_urgent_matrix.execute()
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(
			frappe.db.count("Grievance Urgency Season", {"season_name": "Meher input season"}), 1
		)


ETHIOPIAN_EPOCH = 1724221  # Julian day number of Meskerem 1, year 1 (Amete Mihret)


def ethiopian_to_gregorian(year, month, day):
	"""A Gregorian date from an Ethiopian one. Only for checking the seeded dates."""
	jdn = ETHIOPIAN_EPOCH + 365 * (year - 1) + year // 4 + 30 * C.ETHIOPIAN_MONTHS.index(month) + day - 1
	return date.fromordinal(jdn - 1721425)


class TestEthiopianDates(unittest.TestCase):
	def test_the_converter_is_anchored_on_known_new_years(self):
		self.assertEqual(ethiopian_to_gregorian(2018, "Meskerem", 1), date(2025, 9, 11))
		self.assertEqual(ethiopian_to_gregorian(2016, "Meskerem", 1), date(2023, 9, 12))
		self.assertEqual(ethiopian_to_gregorian(2015, "Pagume", 6), date(2023, 9, 11))

	def test_both_c01_windows_name_the_same_days_in_both_calendars(self):
		"""Meher and Belg are dated to the day; Ethiopian year 2018 is an ordinary one."""
		meher = next(s for s in matrix.SEASONS if s["season_name"] == "Meher input season")
		for window in meher["windows"]:
			with self.subTest(window=window["applies_to"]):
				start = ethiopian_to_gregorian(2018, window["eth_start_month"], window["eth_start_day"])
				end = ethiopian_to_gregorian(2018, window["eth_end_month"], window["eth_end_day"])
				self.assertEqual((start.month, start.day), (window["start_month"], window["start_day"]))
				self.assertEqual((end.month, end.day), (window["end_month"], window["end_day"]))

	def test_the_belg_window_is_tir_24_to_miyazya_22(self):
		meher = next(s for s in matrix.SEASONS if s["season_name"] == "Meher input season")
		belg = next(w for w in meher["windows"] if w["applies_to"] == C.WINDOW_BELG)
		self.assertEqual(
			(belg["eth_start_month"], belg["eth_start_day"], belg["eth_end_month"], belg["eth_end_day"]),
			("Tir", 24, "Miyazya", 22),
		)


class TestApplyMatrixPatchValidates(FrappeTestCase):
	"""The patch saves through the type, so validation and history run."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.savepoint = "matrix_patch_validates"
		frappe.db.savepoint(self.savepoint)
		apply_sensitive_urgent_matrix.execute()  # makes the seasons when they are missing

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)

	def a_type(self, code):
		existing = frappe.db.get_value("Grievance Type", {"type_code": code})
		if existing:
			frappe.db.set_value("Grievance Type", existing, "type_code", f"X{frappe.generate_hash(length=8)}")
		return (
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": f"{code} {frappe.generate_hash(length=6)}",
					"service_category": a_service_category(),
					"type_code": code,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def test_a_type_is_not_linked_to_a_season_an_administrator_retired(self):
		frappe.db.set_value("Grievance Urgency Season", "Meher input season", "is_active", 0)
		name = self.a_type("C01-04")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(
			frappe.db.get_value("Grievance Type", name, ["is_urgent", "urgency_season"]), (0, None)
		)

	def test_a_type_is_not_made_urgent_by_a_priority_only_season(self):
		frappe.db.set_value(
			"Grievance Urgency Season", "Irrigation season (dry season)", "handling_class", "Standard"
		)
		name = self.a_type("C02-02")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(frappe.db.get_value("Grievance Type", name, "is_urgent"), 0)

	def test_the_other_types_are_still_applied_when_one_is_refused(self):
		frappe.db.set_value("Grievance Urgency Season", "Meher input season", "is_active", 0)
		refused, sensitive = self.a_type("C01-05"), self.a_type("C15-02")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(frappe.db.get_value("Grievance Type", refused, "is_urgent"), 0)
		self.assertEqual(frappe.db.get_value("Grievance Type", sensitive, "is_sensitive"), 1)

	def test_the_change_is_in_the_types_history(self):
		name = self.a_type("C13-02")
		# Frappe writes no Version while `in_test` is set; a migration's save does.
		with mock.patch.object(frappe, "in_test", False):
			apply_sensitive_urgent_matrix.execute()
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": "Grievance Type", "docname": name}))
