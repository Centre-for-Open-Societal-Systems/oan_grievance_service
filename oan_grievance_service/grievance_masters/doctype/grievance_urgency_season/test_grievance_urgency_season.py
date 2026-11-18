# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_service_category, an_administrative_area


def _row(applies_to=C.WINDOW_DEFAULT, **values):
	return {
		"applies_to": applies_to,
		"start_month": 10,
		"start_day": 1,
		"end_month": 4,
		"end_day": 30,
		**values,
	}


def _season(**values):
	return frappe.get_doc(
		{
			"doctype": "Grievance Urgency Season",
			"season_name": f"Season {frappe.generate_hash(length=6)}",
			"handling_class": C.HANDLING_URGENT,
			"priority": C.PRIORITY_URGENT,
			"is_active": 1,
			"windows": [_row()],
			**values,
		}
	)


class TestGrievanceUrgencySeason(FrappeTestCase):
	def test_a_season_with_a_default_window_saves(self):
		self.assertTrue(_season().insert(ignore_permissions=True).name)

	def test_a_window_may_wrap_the_year_end(self):
		season = _season().insert(ignore_permissions=True)
		self.assertEqual((season.windows[0].start_month, season.windows[0].end_month), (10, 4))

	def test_an_all_year_season_needs_no_window(self):
		self.assertTrue(_season(all_year=1, windows=[]).insert(ignore_permissions=True).name)

	def test_an_all_year_season_refuses_windows(self):
		with self.assertRaises(frappe.ValidationError):
			_season(all_year=1).insert(ignore_permissions=True)

	def test_a_season_needs_exactly_one_default_window(self):
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[]).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(), _row()]).insert(ignore_permissions=True)

	def test_a_season_has_at_most_one_belg_window(self):
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(), _row(C.WINDOW_BELG), _row(C.WINDOW_BELG)]).insert(
				ignore_permissions=True
			)

	def test_a_specific_area_window_names_an_area_once(self):
		area = an_administrative_area(f"Woreda {frappe.generate_hash(length=6)}", None, "Woreda")
		ok = _season(windows=[_row(), _row(C.WINDOW_AREA, administrative_area=area)])
		self.assertTrue(ok.insert(ignore_permissions=True).name)
		with self.assertRaises(frappe.ValidationError):
			_season(
				windows=[
					_row(),
					_row(C.WINDOW_AREA, administrative_area=area),
					_row(C.WINDOW_AREA, administrative_area=area),
				]
			).insert(ignore_permissions=True)

	def test_only_a_specific_area_window_names_an_area(self):
		area = an_administrative_area(f"Woreda {frappe.generate_hash(length=6)}", None, "Woreda")
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(administrative_area=area)]).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(), _row(C.WINDOW_AREA)]).insert(ignore_permissions=True)

	def test_months_and_days_must_exist(self):
		for bad in (
			{"start_month": 13},
			{"end_month": 0},
			{"start_day": 0},
			{"end_day": 31, "end_month": 4},
			{"start_day": 30, "start_month": 2},
		):
			with self.subTest(bad=bad), self.assertRaises(frappe.ValidationError):
				_season(windows=[_row(**bad)]).insert(ignore_permissions=True)

	def test_the_leap_day_is_a_valid_day(self):
		self.assertTrue(
			_season(windows=[_row(start_month=2, start_day=29, end_month=3, end_day=1)])
			.insert(ignore_permissions=True)
			.name
		)

	def test_the_ethiopian_dates_are_all_or_nothing(self):
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(eth_start_month="Tikimt", eth_start_day=1)]).insert(ignore_permissions=True)
		season = _season(
			windows=[
				_row(
					eth_start_month="Tikimt",
					eth_start_day=1,
					eth_end_month="Miyazya",
					eth_end_day=30,
				)
			]
		).insert(ignore_permissions=True)
		self.assertEqual(season.windows[0].eth_end_month, "Miyazya")

	def test_pagume_has_at_most_six_days(self):
		window = {
			"eth_start_month": "Tikimt",
			"eth_start_day": 1,
			"eth_end_month": "Pagume",
			"eth_end_day": 7,
		}
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(**window)]).insert(ignore_permissions=True)
		window["eth_end_day"] = 6
		self.assertTrue(_season(windows=[_row(**window)]).insert(ignore_permissions=True).name)

	def test_an_ethiopian_month_has_at_most_thirty_days(self):
		window = {
			"eth_start_month": "Tikimt",
			"eth_start_day": 31,
			"eth_end_month": "Miyazya",
			"eth_end_day": 30,
		}
		with self.assertRaises(frappe.ValidationError):
			_season(windows=[_row(**window)]).insert(ignore_permissions=True)

	def test_a_season_used_by_an_urgent_type_cannot_be_deactivated(self):
		season = _season().insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": f"Urgent {frappe.generate_hash(length=6)}",
				"service_category": a_service_category(),
				"is_urgent": 1,
				"urgency_season": season.name,
			}
		).insert(ignore_permissions=True)
		season.is_active = 0
		with self.assertRaises(frappe.ValidationError):
			season.save(ignore_permissions=True)

	def test_an_urgent_type_needs_an_active_urgent_season(self):
		def urgent_type(**values):
			return frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": f"Urgent {frappe.generate_hash(length=6)}",
					"service_category": a_service_category(),
					"is_urgent": 1,
					**values,
				}
			)

		with self.assertRaises(frappe.ValidationError):
			urgent_type().insert(ignore_permissions=True)
		inactive = _season(is_active=0).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			urgent_type(urgency_season=inactive.name).insert(ignore_permissions=True)
		priority_only = _season(handling_class=C.HANDLING_STANDARD, priority=C.PRIORITY_HIGH).insert(
			ignore_permissions=True
		)
		with self.assertRaises(frappe.ValidationError):
			urgent_type(urgency_season=priority_only.name).insert(ignore_permissions=True)
		live = _season().insert(ignore_permissions=True)
		self.assertTrue(urgent_type(urgency_season=live.name).insert(ignore_permissions=True).name)

	def test_a_type_that_is_not_urgent_may_follow_a_priority_only_season(self):
		season = _season(handling_class=C.HANDLING_STANDARD, priority=C.PRIORITY_HIGH).insert(
			ignore_permissions=True
		)
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": f"Harvest {frappe.generate_hash(length=6)}",
				"service_category": a_service_category(),
				"urgency_season": season.name,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.is_urgent, 0)
