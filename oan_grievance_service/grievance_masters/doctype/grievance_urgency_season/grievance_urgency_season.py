# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import calendar

import frappe
from frappe import _
from frappe.model.document import Document

from oan_grievance_service.services import constants as C

# A leap year, so 29 February is a valid day of a window.
_LEAP_YEAR = 2024
_PAGUME_DAYS = 6
_ETHIOPIAN_MONTH_DAYS = 30


class GrievanceUrgencySeason(Document):
	"""A named urgency window shared by the grievance types that follow it.

	The Gregorian window decides whether a case falls inside the season; the Ethiopian window
	is recorded beside it so officers and administrators read the same season in either
	calendar. `services/classification.py` is the only reader of these rows.
	"""

	def validate(self):
		self.season_name = (self.season_name or "").strip()
		self.validate_windows()
		self.validate_urgent_types_keep_their_season()

	def validate_windows(self):
		if self.all_year:
			if self.windows:
				frappe.throw(
					_("An all-year season has no windows. Remove them or clear All Year."),
					frappe.ValidationError,
				)
			return

		defaults = [row for row in self.windows if row.applies_to == C.WINDOW_DEFAULT]
		belg = [row for row in self.windows if row.applies_to == C.WINDOW_BELG]
		if len(defaults) != 1:
			frappe.throw(_("A season needs exactly one Default window, or All Year."), frappe.ValidationError)
		if len(belg) > 1:
			frappe.throw(_("A season has at most one Belg Woreda window."), frappe.ValidationError)

		seen_areas = set()
		for row in self.windows:
			if row.applies_to == C.WINDOW_AREA:
				if not row.administrative_area:
					frappe.throw(
						_("Row {0}: a Specific Area window needs an administrative area.").format(row.idx),
						frappe.ValidationError,
					)
				if row.administrative_area in seen_areas:
					frappe.throw(
						_("Row {0}: {1} already has a window in this season.").format(
							row.idx, row.administrative_area
						),
						frappe.ValidationError,
					)
				seen_areas.add(row.administrative_area)
			elif row.administrative_area:
				frappe.throw(
					_("Row {0}: only a Specific Area window names an administrative area.").format(row.idx),
					frappe.ValidationError,
				)
			self.validate_gregorian(row)
			self.validate_ethiopian(row)

	@staticmethod
	def validate_gregorian(row):
		for month_field, day_field in (("start_month", "start_day"), ("end_month", "end_day")):
			month, day = row.get(month_field), row.get(day_field)
			if not month or not 1 <= int(month) <= 12:
				frappe.throw(
					_("Row {0}: month must be between 1 and 12.").format(row.idx), frappe.ValidationError
				)
			if not day or not 1 <= int(day) <= calendar.monthrange(_LEAP_YEAR, int(month))[1]:
				frappe.throw(
					_("Row {0}: day {1} does not exist in month {2}.").format(row.idx, day, month),
					frappe.ValidationError,
				)

	@staticmethod
	def validate_ethiopian(row):
		"""The Ethiopian dates are optional, but whole: a month without its day is no date."""
		fields = ("eth_start_month", "eth_start_day", "eth_end_month", "eth_end_day")
		given = [bool(row.get(field)) for field in fields]
		if not any(given):
			return
		if not all(given):
			frappe.throw(
				_("Row {0}: give all four Ethiopian dates or none.").format(row.idx), frappe.ValidationError
			)
		for month_field, day_field in (
			("eth_start_month", "eth_start_day"),
			("eth_end_month", "eth_end_day"),
		):
			limit = _PAGUME_DAYS if row.get(month_field) == "Pagume" else _ETHIOPIAN_MONTH_DAYS
			if not 1 <= int(row.get(day_field)) <= limit:
				frappe.throw(
					_("Row {0}: {1} has {2} days at most.").format(row.idx, row.get(month_field), limit),
					frappe.ValidationError,
				)

	def validate_urgent_types_keep_their_season(self):
		"""An active Urgent type needs its season active and Urgent in the window.

		Deactivating the season, or turning it into a priority-only (Standard) season, would leave
		such a type flagged Urgent with no case ever classed Urgent, and refuse its next save. The
		types are moved off the season first.
		"""
		if self.is_new():
			return
		deactivated = not self.is_active and self.has_value_changed("is_active")
		made_standard = self.handling_class != C.HANDLING_URGENT and self.has_value_changed("handling_class")
		if not (deactivated or made_standard):
			return
		in_use = frappe.db.get_value(
			"Grievance Type",
			{"urgency_season": self.name, "is_urgent": 1, "is_active": 1},
			"name",
		)
		if not in_use:
			return
		if deactivated:
			message = _("Season {0} is used by the urgent grievance type {1}, so it cannot be deactivated.")
		else:
			message = _(
				"Season {0} is used by the urgent grievance type {1}, so its handling class must stay Urgent."
			)
		frappe.throw(message.format(self.name, in_use), frappe.ValidationError)
