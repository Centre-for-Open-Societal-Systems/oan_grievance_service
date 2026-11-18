"""Sensitive and urgent classification of a grievance (STG-514).

This module is the only place that decides what a case is: Sensitive or not, Urgent or
Standard handling, and its priority. Everything else reads the answer from the case
(`is_sensitive`, `handling_class`, `priority`), which `classify_grievance` writes at filing and
again if an officer moves the case to another type. The UI never works a season out for itself.

The rules, all driven by the Grievance Type and its Grievance Urgency Season:

- Sensitive: the type's `is_sensitive` flag.
- Urgent: the type is `is_urgent`, its season is active, and the filing date falls inside the
  season's window for the case's area. Outside the window the case is Standard.
- Priority: the season's `priority` inside its window (Urgent for an urgent season, High for
  harvest marketing), otherwise Normal. A season whose handling class is Standard (harvest
  marketing) therefore raises priority without ever making a case Urgent.

The window is the Gregorian one: the season's Ethiopian dates are recorded and shown, but
the Gregorian month and day decide. Which window applies to an area, narrowest first:

1. a Specific Area override whose area contains the case's area (a woreda override);
2. the Belg Woreda window, when the case's area is inside an area marked `is_belg`;
3. the Default window.

The date is the filing date, so a case keeps the class it was filed with when the season ends.
"""

import secrets
from dataclasses import dataclass
from datetime import date

import frappe
from frappe.utils import getdate, now_datetime

from oan_grievance_service.grievance_masters.doctype.grievance_administrative_area.grievance_administrative_area import (
	get_area_bounds,
)
from oan_grievance_service.services import constants as C

PIN_LENGTH = 6


@dataclass(frozen=True, slots=True)
class Classification:
	"""What a case is, and which season rule said so (for the officer view and for tests)."""

	is_sensitive: bool
	handling_class: str
	priority: str
	season: str | None = None
	in_window: bool = False
	window: str | None = None

	def as_fields(self) -> dict:
		return {
			"is_sensitive": 1 if self.is_sensitive else 0,
			"handling_class": self.handling_class,
			"priority": self.priority,
		}


STANDARD = Classification(False, C.HANDLING_STANDARD, C.PRIORITY_NORMAL)


def month_day_in_window(on: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
	"""Whether (month, day) of `on` is inside [start, end], both ends included.

	A window whose start is after its end wraps the year (October to April). Only month and day
	are compared, so the year never matters, and 29 February needs no special case.
	"""
	today = (on.month, on.day)
	if start <= end:
		return start <= today <= end
	return today >= start or today <= end


def _window_bounds(row) -> tuple[tuple[int, int], tuple[int, int]]:
	return (int(row.start_month), int(row.start_day)), (int(row.end_month), int(row.end_day))


def _is_belg(lft: int, rgt: int) -> bool:
	"""Whether the area, or one that contains it, is marked Belg."""
	return bool(
		frappe.db.exists(
			"Grievance Administrative Area", {"is_belg": 1, "lft": ["<=", lft], "rgt": [">=", rgt]}
		)
	)


def window_row_for(season, area: str | None):
	"""The season's window that applies to `area`, as (row, label), or (None, None) for none.

	Narrowest first: a Specific Area override, then the Belg window, then the Default one.
	"""
	rows = list(season.windows)
	default = next((row for row in rows if row.applies_to == C.WINDOW_DEFAULT), None)
	lft, rgt = get_area_bounds(area) if area else (None, None)
	if lft is not None:
		containing = []
		for row in rows:
			if row.applies_to != C.WINDOW_AREA or not row.administrative_area:
				continue
			row_lft, row_rgt = get_area_bounds(row.administrative_area)
			if row_lft is not None and row_lft <= lft and row_rgt >= rgt:
				containing.append((row_rgt - row_lft, row))
		if containing:
			row = min(containing, key=lambda pair: pair[0])[1]
			return row, f"{C.WINDOW_AREA}: {row.administrative_area}"
		belg = next((row for row in rows if row.applies_to == C.WINDOW_BELG), None)
		if belg and _is_belg(lft, rgt):
			return belg, C.WINDOW_BELG
	return (default, C.WINDOW_DEFAULT) if default else (None, None)


def season_state(season_name: str, area: str | None, on: date) -> tuple[bool, str | None, str, str]:
	"""(in_window, window label, handling class, priority) of a season for a case.

	An inactive or missing season is none: not in window, Standard, Normal.
	"""
	season = frappe.get_doc("Grievance Urgency Season", season_name) if season_name else None
	if not season or not season.is_active:
		return False, None, C.HANDLING_STANDARD, C.PRIORITY_NORMAL
	if season.all_year:
		inside, label = True, "All year"
	else:
		row, label = window_row_for(season, area)
		inside = bool(row) and month_day_in_window(on, *_window_bounds(row))
	if not inside:
		return False, label, C.HANDLING_STANDARD, C.PRIORITY_NORMAL
	return True, label, season.handling_class, season.priority


def classify(grievance_type: str | None, area: str | None = None, on: date | str | None = None):
	"""The classification of a case of `grievance_type` in `area`, filed on `on` (default today)."""
	if not grievance_type:
		return STANDARD
	flags = frappe.db.get_value(
		"Grievance Type",
		grievance_type,
		["is_sensitive", "is_urgent", "urgency_season"],
		as_dict=True,
	)
	if not flags:
		return STANDARD

	on = getdate(on) if on else getdate(now_datetime())
	season = flags.urgency_season
	handling, priority, inside, label = C.HANDLING_STANDARD, C.PRIORITY_NORMAL, False, None
	if season:
		inside, label, season_handling, season_priority = season_state(season, area, on)
		if inside:
			priority = season_priority
			if flags.is_urgent and season_handling == C.HANDLING_URGENT:
				handling = C.HANDLING_URGENT
	return Classification(
		is_sensitive=bool(flags.is_sensitive),
		handling_class=handling,
		priority=priority,
		season=season,
		in_window=inside,
		window=label,
	)


def classify_grievance(doc) -> Classification:
	"""Classify a Grievance document and write the result onto it.

	The filing date is the document's `creation` once it has one (a case loaded from history
	keeps the season it was filed in), otherwise now. Does not save: the caller is inside
	`validate`, or saves the case itself.
	"""
	result = classify(doc.grievance_type, doc.administrative_area, doc.get("creation"))
	for fieldname, value in result.as_fields().items():
		doc.set(fieldname, value)
	if result.is_sensitive:
		issue_pin(doc)
	return result


def new_pin() -> str:
	"""A random numeric PIN. `secrets`, not `random`: it is a credential."""
	return "".join(secrets.choice("0123456789") for _ in range(PIN_LENGTH))


def issue_pin(doc) -> None:
	"""Give a sensitive case its status PIN unless it already has one. Stored encrypted."""
	if doc.is_new():
		doc.status_pin = new_pin()
	elif not pin_of(doc):
		doc.status_pin = new_pin()


def pin_of(doc) -> str | None:
	"""The case's status PIN, decrypted, or None. Only a sensitive case has one."""
	if not doc.get("is_sensitive"):
		return None
	if doc.is_new():
		raw = doc.get("status_pin")
		return raw if raw and set(raw) != {"*"} else None
	return doc.get_password("status_pin", raise_exception=False) or None
