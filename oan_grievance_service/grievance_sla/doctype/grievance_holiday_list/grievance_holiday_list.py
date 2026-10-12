# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

CACHE_KEY_HOLIDAYS_PREFIX = "grievance_holidays_"
CACHE_KEY_DEFAULT_HOLIDAY_LIST = "grievance_default_holiday_list"

WEEKDAYS = {
	"Monday": 0,
	"Tuesday": 1,
	"Wednesday": 2,
	"Thursday": 3,
	"Friday": 4,
	"Saturday": 5,
	"Sunday": 6,
}


class GrievanceHolidayList(Document):
	def validate(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date."))

		if self.weekly_off:
			self.populate_weekly_offs()

		self.total_holidays = len(self.holidays or [])
		self.ensure_single_default()

	def ensure_single_default(self):
		if self.is_default:
			frappe.db.sql(
				"""
				UPDATE `tabGrievance Holiday List`
				SET is_default = 0
				WHERE name != %s AND is_default = 1
				""",
				(self.name,),
			)

	def on_update(self):
		clear_holiday_cache(self.name)

	def on_trash(self):
		clear_holiday_cache(self.name)

	def get_weekly_off_weekdays(self) -> set[int]:
		"""Return set of 0-indexed weekdays (Monday=0, Sunday=6) for weekly off."""
		if not self.weekly_off:
			return set()
		if self.weekly_off == "Saturday and Sunday":
			return {5, 6}
		if self.weekly_off in WEEKDAYS:
			return {WEEKDAYS[self.weekly_off]}
		return set()

	@frappe.whitelist()
	def populate_weekly_offs(self):
		"""Auto-generate weekly offs between from_date and to_date."""
		weekdays = self.get_weekly_off_weekdays()
		if not weekdays or not self.from_date or not self.to_date:
			return

		existing_dates = {getdate(row.holiday_date) for row in (self.holidays or [])}
		cur_date = getdate(self.from_date)
		end_date = getdate(self.to_date)

		while cur_date <= end_date:
			if cur_date.weekday() in weekdays and cur_date not in existing_dates:
				self.append(
					"holidays",
					{
						"holiday_date": cur_date,
						"description": self.weekly_off,
						"weekly_off": 1,
						"is_half_day": 0,
					},
				)
				existing_dates.add(cur_date)
			cur_date += timedelta(days=1)

		self.total_holidays = len(self.holidays or [])


def clear_holiday_cache(holiday_list_name=None):
	frappe.cache.delete_value(CACHE_KEY_DEFAULT_HOLIDAY_LIST)
	if holiday_list_name:
		frappe.cache.delete_keys(f"{CACHE_KEY_HOLIDAYS_PREFIX}{holiday_list_name}")
	else:
		frappe.cache.delete_keys(f"{CACHE_KEY_HOLIDAYS_PREFIX}*")
