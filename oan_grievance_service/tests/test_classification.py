"""Sensitive and urgent classification (STG-514): season windows, woreda overrides, Belg, flags."""

import unittest
from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.services import classification
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.classification import month_day_in_window
from oan_grievance_service.tests.fixtures import (
	a_leaf_area,
	a_service_category,
	an_administrative_area,
)

MEHER = ((5, 15), (8, 31))
BELG = ((2, 1), (4, 30))
DRY = ((10, 1), (4, 30))
HARVEST = ((10, 1), (12, 31))


class TestWindowBoundaries(unittest.TestCase):
	"""Every window: the day before, the first day, a day inside, the last day, the day after."""

	def check(self, window, expected):
		for day, inside in expected:
			with self.subTest(day=day.isoformat()):
				self.assertEqual(month_day_in_window(day, *window), inside)

	def test_meher_input_season(self):
		self.check(
			MEHER,
			[
				(date(2026, 5, 14), False),
				(date(2026, 5, 15), True),
				(date(2026, 7, 1), True),
				(date(2026, 8, 31), True),
				(date(2026, 9, 1), False),
			],
		)

	def test_belg_window_for_meher_input(self):
		self.check(
			BELG,
			[
				(date(2026, 1, 31), False),
				(date(2026, 2, 1), True),
				(date(2026, 3, 15), True),
				(date(2026, 4, 30), True),
				(date(2026, 5, 1), False),
			],
		)

	def test_october_to_april_wraps_the_year_end(self):
		self.check(
			DRY,
			[
				(date(2026, 9, 30), False),
				(date(2026, 10, 1), True),
				(date(2026, 12, 15), True),
				(date(2026, 12, 31), True),
				# The year transition: the window is still open on 1 January.
				(date(2027, 1, 1), True),
				(date(2027, 2, 14), True),
				(date(2027, 4, 30), True),
				(date(2027, 5, 1), False),
				(date(2027, 7, 1), False),
			],
		)

	def test_leap_day_is_inside_a_window_that_spans_february(self):
		self.assertTrue(month_day_in_window(date(2028, 2, 29), *DRY))
		self.assertTrue(month_day_in_window(date(2028, 2, 29), *BELG))
		self.assertFalse(month_day_in_window(date(2028, 2, 29), *MEHER))

	def test_harvest_marketing_peak(self):
		self.check(
			HARVEST,
			[
				(date(2026, 9, 30), False),
				(date(2026, 10, 1), True),
				(date(2026, 11, 15), True),
				(date(2026, 12, 31), True),
				(date(2027, 1, 1), False),
			],
		)

	def test_a_one_day_window(self):
		self.assertTrue(month_day_in_window(date(2026, 6, 1), (6, 1), (6, 1)))
		self.assertFalse(month_day_in_window(date(2026, 6, 2), (6, 1), (6, 1)))


def _window(applies_to, window, area=None):
	(start_month, start_day), (end_month, end_day) = window
	return {
		"applies_to": applies_to,
		"administrative_area": area,
		"start_month": start_month,
		"start_day": start_day,
		"end_month": end_month,
		"end_day": end_day,
	}


def _unique(label):
	return f"{label} {frappe.generate_hash(length=6)}"


def a_season(label, windows=(), **values):
	doc = frappe.get_doc(
		{
			"doctype": "Grievance Urgency Season",
			"season_name": _unique(label),
			"is_active": 1,
			"handling_class": C.HANDLING_URGENT,
			"priority": C.PRIORITY_URGENT,
			"windows": list(windows),
			**values,
		}
	)
	return doc.insert(ignore_permissions=True).name


def a_type(season=None, urgent=0, sensitive=0):
	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": _unique("Type"),
				"service_category": a_service_category(),
				"is_active": 1,
				"is_urgent": urgent,
				"is_sensitive": sensitive,
				"urgency_season": season,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


class TestClassification(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		region = frappe.db.get_value("Grievance Administrative Area", {"level_name": "Region"}, "name") or (
			an_administrative_area(_unique("Region"), None, "Region")
		)
		# Two woredas, each with a kebele; one woreda is Belg, one carries a window override.
		cls.woreda = an_administrative_area(_unique("Woreda"), region, "Woreda")
		cls.kebele = an_administrative_area(_unique("Kebele"), cls.woreda, "Kebele")
		cls.belg_woreda = an_administrative_area(_unique("Belg woreda"), region, "Woreda", is_belg=1)
		cls.belg_kebele = an_administrative_area(_unique("Belg kebele"), cls.belg_woreda, "Kebele")
		cls.override_woreda = an_administrative_area(_unique("Override woreda"), region, "Woreda")
		cls.override_kebele = an_administrative_area(
			_unique("Override kebele"), cls.override_woreda, "Kebele"
		)

		cls.meher = a_season(
			"Meher",
			[_window(C.WINDOW_DEFAULT, MEHER), _window(C.WINDOW_BELG, BELG)],
		)
		cls.c01 = a_type(cls.meher, urgent=1)
		# C02 and C07 follow October to April, and a woreda may override it. Two seasons, one window.
		cls.irrigation = a_season(
			"Irrigation",
			[
				_window(C.WINDOW_DEFAULT, DRY),
				_window(C.WINDOW_AREA, ((11, 1), (3, 31)), cls.override_woreda),
			],
		)
		cls.c02 = a_type(cls.irrigation, urgent=1)
		cls.dry = a_season(
			"Dry",
			[
				_window(C.WINDOW_DEFAULT, DRY),
				_window(C.WINDOW_AREA, ((12, 1), (2, 28)), cls.override_woreda),
			],
		)
		cls.c07 = a_type(cls.dry, urgent=1)
		cls.harvest = a_season(
			"Harvest",
			[_window(C.WINDOW_DEFAULT, HARVEST)],
			handling_class=C.HANDLING_STANDARD,
			priority=C.PRIORITY_HIGH,
		)
		cls.c09 = a_type(cls.harvest, urgent=0)
		cls.pest = a_season("Pest", all_year=1)
		cls.c17 = a_type(cls.pest, urgent=1)

	def classify(self, grievance_type, area, on):
		return classification.classify(grievance_type, area, on)

	def assertUrgent(self, result, urgent):
		self.assertEqual(result.handling_class, C.HANDLING_URGENT if urgent else C.HANDLING_STANDARD)
		self.assertEqual(result.priority, C.PRIORITY_URGENT if urgent else C.PRIORITY_NORMAL)

	# C01
	def test_c01_follows_the_meher_window_in_a_normal_woreda(self):
		for day, urgent in [
			(date(2026, 5, 14), False),
			(date(2026, 5, 15), True),
			(date(2026, 7, 10), True),
			(date(2026, 8, 31), True),
			(date(2026, 9, 1), False),
			# February to April is not Meher: a normal woreda is Standard then.
			(date(2026, 3, 1), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c01, self.woreda, day), urgent)

	def test_c01_in_a_belg_woreda_uses_february_to_april(self):
		for day, urgent in [
			(date(2026, 1, 31), False),
			(date(2026, 2, 1), True),
			(date(2026, 3, 15), True),
			(date(2026, 4, 30), True),
			(date(2026, 5, 1), False),
			# The Meher window does not apply to a Belg woreda.
			(date(2026, 6, 15), False),
			(date(2026, 8, 31), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c01, self.belg_woreda, day), urgent)

	def test_a_kebele_inside_a_belg_woreda_is_belg(self):
		self.assertUrgent(self.classify(self.c01, self.belg_kebele, date(2026, 3, 1)), True)
		self.assertUrgent(self.classify(self.c01, self.belg_kebele, date(2026, 7, 1)), False)
		self.assertUrgent(self.classify(self.c01, self.kebele, date(2026, 3, 1)), False)

	def test_no_area_uses_the_default_window(self):
		self.assertUrgent(self.classify(self.c01, None, date(2026, 7, 1)), True)
		self.assertUrgent(self.classify(self.c01, None, date(2026, 3, 1)), False)

	def test_marking_a_woreda_belg_changes_its_classification(self):
		frappe.db.set_value("Grievance Administrative Area", self.woreda, "is_belg", 1)
		self.addCleanup(frappe.db.set_value, "Grievance Administrative Area", self.woreda, "is_belg", 0)
		self.assertUrgent(self.classify(self.c01, self.kebele, date(2026, 3, 1)), True)

	# C02 and C07
	def test_c02_default_window_with_the_year_transition(self):
		for day, urgent in [
			(date(2026, 9, 30), False),
			(date(2026, 10, 1), True),
			(date(2026, 12, 31), True),
			(date(2027, 1, 1), True),
			(date(2027, 4, 30), True),
			(date(2027, 5, 1), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c02, self.woreda, day), urgent)

	def test_c02_woreda_override_replaces_the_default(self):
		for day, urgent in [
			(date(2026, 10, 15), False),  # default says yes, the override starts 1 November
			(date(2026, 10, 31), False),
			(date(2026, 11, 1), True),
			(date(2027, 3, 31), True),
			(date(2027, 4, 1), False),  # default says yes, the override ended 31 March
			(date(2027, 4, 30), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c02, self.override_woreda, day), urgent)
		# A kebele inside the overriding woreda follows the override too.
		self.assertUrgent(self.classify(self.c02, self.override_kebele, date(2026, 10, 15)), False)
		self.assertUrgent(self.classify(self.c02, self.override_kebele, date(2026, 12, 1)), True)

	def test_c07_default_window_and_woreda_override(self):
		self.assertUrgent(self.classify(self.c07, self.woreda, date(2026, 10, 1)), True)
		self.assertUrgent(self.classify(self.c07, self.woreda, date(2027, 4, 30)), True)
		self.assertUrgent(self.classify(self.c07, self.woreda, date(2027, 5, 1)), False)
		for day, urgent in [
			(date(2026, 11, 30), False),
			(date(2026, 12, 1), True),
			(date(2027, 2, 28), True),
			(date(2027, 3, 1), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c07, self.override_woreda, day), urgent)

	def test_an_override_is_per_season(self):
		"""C02's override for a woreda says nothing about C07's: each season has its own."""
		self.assertUrgent(self.classify(self.c02, self.override_woreda, date(2026, 11, 15)), True)
		self.assertUrgent(self.classify(self.c07, self.override_woreda, date(2026, 11, 15)), False)

	def test_the_narrowest_override_wins(self):
		inner = an_administrative_area(_unique("Inner"), self.override_woreda, "Kebele")
		season = a_season(
			"Nested",
			[
				_window(C.WINDOW_DEFAULT, DRY),
				_window(C.WINDOW_AREA, ((11, 1), (3, 31)), self.override_woreda),
				_window(C.WINDOW_AREA, ((1, 1), (1, 31)), inner),
			],
		)
		urgent_type = a_type(season, urgent=1)
		self.assertUrgent(self.classify(urgent_type, inner, date(2026, 11, 15)), False)
		self.assertUrgent(self.classify(urgent_type, inner, date(2027, 1, 15)), True)
		self.assertUrgent(self.classify(urgent_type, self.override_kebele, date(2026, 11, 15)), True)

	# C17
	def test_c17_is_urgent_every_day_of_the_year(self):
		for month in range(1, 13):
			with self.subTest(month=month):
				self.assertUrgent(self.classify(self.c17, self.woreda, date(2026, month, 1)), True)
		self.assertUrgent(self.classify(self.c17, self.woreda, date(2026, 12, 31)), True)
		self.assertUrgent(self.classify(self.c17, self.belg_woreda, date(2028, 2, 29)), True)

	# C09
	def test_c09_raises_priority_in_season_and_never_becomes_urgent(self):
		for day, priority in [
			(date(2026, 9, 30), C.PRIORITY_NORMAL),
			(date(2026, 10, 1), C.PRIORITY_HIGH),
			(date(2026, 11, 20), C.PRIORITY_HIGH),
			(date(2026, 12, 31), C.PRIORITY_HIGH),
			(date(2027, 1, 1), C.PRIORITY_NORMAL),
			(date(2027, 6, 1), C.PRIORITY_NORMAL),
		]:
			with self.subTest(day=day.isoformat()):
				result = self.classify(self.c09, self.woreda, day)
				self.assertEqual(result.handling_class, C.HANDLING_STANDARD)
				self.assertEqual(result.priority, priority)

	def test_a_season_that_is_urgent_does_not_make_a_type_that_is_not_urgent_urgent(self):
		not_urgent = a_type(self.meher, urgent=0)
		result = self.classify(not_urgent, self.woreda, date(2026, 7, 1))
		self.assertEqual(result.handling_class, C.HANDLING_STANDARD)
		# The season still raises its priority, as it does for harvest marketing.
		self.assertEqual(result.priority, C.PRIORITY_URGENT)

	# Flags and inactive seasons
	def test_a_type_with_no_season_is_standard_and_normal(self):
		plain = a_type()
		self.assertEqual(self.classify(plain, self.woreda, date(2026, 7, 1)), classification.STANDARD)

	def test_sensitive_is_the_types_flag_whatever_the_date(self):
		sensitive = a_type(sensitive=1)
		for day in (date(2026, 1, 1), date(2026, 7, 1)):
			result = self.classify(sensitive, self.woreda, day)
			self.assertTrue(result.is_sensitive)
			self.assertEqual(result.handling_class, C.HANDLING_STANDARD)
		self.assertFalse(self.classify(self.c01, self.woreda, date(2026, 7, 1)).is_sensitive)

	def test_an_inactive_season_is_no_season(self):
		season = a_season("Retired", [_window(C.WINDOW_DEFAULT, MEHER)])
		urgent_type = a_type(season, urgent=1)
		self.assertUrgent(self.classify(urgent_type, self.woreda, date(2026, 7, 1)), True)
		# Retiring it is refused while an urgent type uses it, so the type is retired first.
		frappe.db.set_value("Grievance Type", urgent_type, "is_active", 0)
		frappe.db.set_value("Grievance Urgency Season", season, "is_active", 0)
		self.assertUrgent(self.classify(urgent_type, self.woreda, date(2026, 7, 1)), False)

	def test_the_filing_date_defaults_to_today(self):
		today = frappe.utils.getdate()
		self.assertEqual(
			self.classify(self.c17, self.woreda, None), self.classify(self.c17, self.woreda, today)
		)

	def test_the_result_says_which_window_applied(self):
		self.assertEqual(self.classify(self.c01, self.woreda, date(2026, 7, 1)).window, C.WINDOW_DEFAULT)
		self.assertEqual(self.classify(self.c01, self.belg_woreda, date(2026, 3, 1)).window, C.WINDOW_BELG)
		self.assertEqual(
			self.classify(self.c02, self.override_woreda, date(2026, 12, 1)).window,
			f"{C.WINDOW_AREA}: {self.override_woreda}",
		)
		self.assertEqual(self.classify(self.c17, self.woreda, date(2026, 5, 1)).window, "All year")


class TestGrievanceClassification(FrappeTestCase):
	"""The case carries what the classifier decided, fixed at filing."""

	def a_case(self, grievance_type, **values):
		from oan_grievance_service.tests.fixtures import a_grievance

		return a_grievance(grievance_type=grievance_type, **values)

	def test_a_sensitive_case_is_marked_and_gets_a_pin(self):
		case = self.a_case(a_type(sensitive=1))
		self.assertEqual(case.is_sensitive, 1)
		pin = classification.pin_of(case)
		self.assertRegex(pin, r"^\d{6}$")
		# Encrypted at rest: the column holds no PIN.
		self.assertNotIn(pin, frappe.db.get_value("Grievance", case.name, "status_pin") or "")

	def test_a_case_that_is_not_sensitive_has_no_pin(self):
		case = self.a_case(a_type())
		self.assertEqual(case.is_sensitive, 0)
		self.assertIsNone(classification.pin_of(case))

	def test_a_case_is_standard_and_normal_by_default(self):
		case = self.a_case(a_type())
		self.assertEqual((case.handling_class, case.priority), (C.HANDLING_STANDARD, C.PRIORITY_NORMAL))

	def test_the_filing_date_picks_the_season(self):
		season = a_season("Always", all_year=1)
		urgent_type = a_type(season, urgent=1)
		case = self.a_case(urgent_type)
		self.assertEqual((case.handling_class, case.priority), (C.HANDLING_URGENT, C.PRIORITY_URGENT))

	def test_a_case_keeps_its_class_when_the_season_changes(self):
		season = a_season("Changing", all_year=1)
		urgent_type = a_type(season, urgent=1)
		case = self.a_case(urgent_type)
		frappe.db.set_value("Grievance Urgency Season", season, "handling_class", C.HANDLING_STANDARD)
		case.reload()
		case.closure_reason = "Saved again with the season changed."
		case.save(ignore_permissions=True)
		self.assertEqual(case.handling_class, C.HANDLING_URGENT)

	def test_the_derived_fields_cannot_be_edited(self):
		case = self.a_case(a_type(sensitive=1))
		case.is_sensitive = 0
		with self.assertRaises(frappe.ValidationError):
			case.save(ignore_permissions=True)

	def test_moving_a_case_to_a_sensitive_type_reclassifies_it(self):
		case = self.a_case(a_type())
		sensitive_type = a_type(sensitive=1)
		case.grievance_type = sensitive_type
		case.flags.change_request = "test-change-request"
		case.save(ignore_permissions=True)
		self.assertEqual(case.is_sensitive, 1)
		self.assertRegex(classification.pin_of(case), r"^\d{6}$")

	def test_a_new_pin_is_random_and_six_digits(self):
		pins = {classification.new_pin() for _ in range(20)}
		self.assertGreater(len(pins), 15)
		self.assertTrue(all(len(pin) == 6 and pin.isdigit() for pin in pins))

	def test_classify_grievance_reads_the_documents_creation_date(self):
		season = a_season("Filed in July", [_window(C.WINDOW_DEFAULT, MEHER)])
		urgent_type = a_type(season, urgent=1)
		doc = frappe.new_doc("Grievance")
		doc.grievance_type = urgent_type
		doc.administrative_area = a_leaf_area()
		doc.creation = "2026-07-01 09:00:00"
		self.assertEqual(classification.classify_grievance(doc).handling_class, C.HANDLING_URGENT)
		doc.creation = "2026-10-01 09:00:00"
		self.assertEqual(classification.classify_grievance(doc).handling_class, C.HANDLING_STANDARD)
