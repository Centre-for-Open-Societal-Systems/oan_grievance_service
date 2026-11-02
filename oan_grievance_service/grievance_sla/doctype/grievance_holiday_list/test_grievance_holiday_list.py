# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from oan_grievance_service.services import sla


class TestGrievanceHolidayList(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.rollback()
		self.list_name = f"Test Holiday List {frappe.generate_hash(length=6)}"
		self.holiday_list = frappe.get_doc(
			{
				"doctype": "Grievance Holiday List",
				"holiday_list_name": self.list_name,
				"from_date": "2026-01-01",
				"to_date": "2026-01-31",
				"weekly_off": "Sunday",
				"is_default": 0,
			}
		)
		self.holiday_list.append(
			"holidays",
			{
				"holiday_date": "2026-01-07",
				"description": "Christmas",
				"weekly_off": 0,
			},
		)
		self.holiday_list.insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def test_holiday_list_validation_and_weekly_off_population(self):
		self.assertEqual(self.holiday_list.total_holidays, len(self.holiday_list.holidays))
		# January 2026 has Sundays on 4, 11, 18, 25 + 1 Christmas = 5 holidays
		self.assertEqual(self.holiday_list.total_holidays, 5)

		# Check is_holiday helper
		self.assertTrue(sla.is_holiday("2026-01-07", self.list_name))  # Christmas
		self.assertTrue(sla.is_holiday("2026-01-04", self.list_name))  # Sunday
		self.assertFalse(sla.is_holiday("2026-01-05", self.list_name))  # Monday

	def test_from_date_after_to_date_fails(self):
		bad_doc = frappe.get_doc(
			{
				"doctype": "Grievance Holiday List",
				"holiday_list_name": "Bad Dates",
				"from_date": "2026-02-01",
				"to_date": "2026-01-01",
				"weekly_off": "Sunday",
			}
		)
		self.assertRaises(frappe.ValidationError, bad_doc.insert)

	def test_working_deadline_skips_holidays_and_weekly_offs(self):
		# Start on Friday 2026-01-02 10:00:00
		# sla_days = 3
		# Day 1: Sat 2026-01-03 (Working)
		# Sun 2026-01-04 is Weekly Off (Skipped)
		# Day 2: Mon 2026-01-05 (Working)
		# Day 3: Tue 2026-01-06 (Working)
		# Target deadline: 2026-01-06 10:00:00
		start = "2026-01-02 10:00:00"
		deadline = sla.calculate_working_deadline(start, 3, holiday_list_name=self.list_name)
		self.assertEqual(deadline.strftime("%Y-%m-%d %H:%M:%S"), "2026-01-06 10:00:00")

		# If spanning across Christmas 2026-01-07 (Wed):
		# sla_days = 4
		# Day 4: Thu 2026-01-08 (Skipped Wed 01-07 holiday)
		deadline_4 = sla.calculate_working_deadline(start, 4, holiday_list_name=self.list_name)
		self.assertEqual(deadline_4.strftime("%Y-%m-%d %H:%M:%S"), "2026-01-08 10:00:00")
