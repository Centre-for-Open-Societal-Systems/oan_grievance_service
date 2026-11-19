"""Sensitive and urgent classification of a grievance (STG-514).

This module is the only place that decides what a case is: Sensitive or not, Urgent or
Standard handling, its priority, and the SLA days a seasonal rule gives it. Everything else
reads the answer from the case (`is_sensitive`, `handling_class`, `priority`, `sla_days`), which
`classify_grievance` and the SLA clock write at filing, and again if the case moves to another
type or category. The UI never works a season out for itself.

- Sensitive: the type's `is_sensitive` flag.
- Urgent, priority and SLA days: the rules (`sla_rules`, Grievance SLA Rule) on the active
  Grievance SLA Configuration of the case's category. A rule may name a grievance type, an
  administrative area (which covers the areas inside it) and a recurring window of the year,
  and sets any of `sla_days`, `is_urgent` and `priority`.

A rule matches a case when everything it names matches: the case's type, an area that contains
the case's area, and a filing date inside its window. Of the matching rules the most specific
wins: one that names the type before one for the whole category, then the narrowest area (no
area is the widest), then a windowed rule before an all-year one. Two windowed rules, or two
all-year rules, for the same type and area may not share a day, so the winner is never a tie.

A rule that sets nothing (no SLA days, not urgent, no priority) still wins where it matches: it
says "standard here". That is how a woreda gets its own window instead of the category's: a
windowed urgent rule for the woreda, and an all-year rule for it that sets nothing.

A window is a start and end month and day, the same every year. A start after the end wraps the
year end (October to April). Only month and day are compared, so the year never matters, any
calendar's season can be written in it, and 29 February needs no special case.

The date is the filing date, so a case keeps the class it was filed with when the season ends.
"""

import secrets
from dataclasses import dataclass
from datetime import date

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from oan_grievance_service.services import constants as C

PIN_LENGTH = 6
SLA_CONFIGURATION = "Grievance SLA Configuration"
RULE = "Grievance SLA Rule"
RULE_FIELDS = (
	"idx",
	"rule_label",
	"grievance_type",
	"administrative_area",
	"start_month",
	"start_day",
	"end_month",
	"end_day",
	"sla_days",
	"is_urgent",
	"priority",
)
WINDOW_FIELDS = ("start_month", "start_day", "end_month", "end_day")
# Window days are checked against a leap year, so 29 February is a valid day of a window.
_LEAP_YEAR = 2024


@dataclass(frozen=True, slots=True)
class Classification:
	"""What a case is, and which rule said so (for logs and tests)."""

	is_sensitive: bool
	handling_class: str
	priority: str
	sla_days: int | None = None
	rule: str | None = None

	def as_fields(self) -> dict:
		return {
			"is_sensitive": 1 if self.is_sensitive else 0,
			"handling_class": self.handling_class,
			"priority": self.priority,
		}


STANDARD = Classification(False, C.HANDLING_STANDARD, C.PRIORITY_NORMAL)


# Windows
# -------


def window_of(rule) -> tuple[tuple[int, int], tuple[int, int]] | None:
	"""The rule's window as ((start month, day), (end month, day)), or None for all year."""
	values = [rule.get(field) for field in WINDOW_FIELDS]
	if not any(values):
		return None
	start_month, start_day, end_month, end_day = (int(value or 0) for value in values)
	return (start_month, start_day), (end_month, end_day)


def month_day_in_window(on: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
	"""Whether (month, day) of `on` is inside [start, end], both ends included.

	A window whose start is after its end wraps the year (October to April).
	"""
	today = (on.month, on.day)
	if start <= end:
		return start <= today <= end
	return today >= start or today <= end


def _is_day(month: int, day: int) -> bool:
	try:
		date(_LEAP_YEAR, month, day)
	except (TypeError, ValueError):
		return False
	return True


def window_error(rule) -> str | None:
	"""Why the rule's window is not a window, or None when it is (or the rule has none)."""
	values = [rule.get(field) for field in WINDOW_FIELDS]
	if not any(values):
		return None
	if not all(values):
		return _("Set the start month and day and the end month and day, or none of them for all year.")
	(start_month, start_day), (end_month, end_day) = window_of(rule)
	for month, day in ((start_month, start_day), (end_month, end_day)):
		if not 1 <= month <= 12:
			return _("{0} is not a month: use 1 to 12.").format(month)
		if not _is_day(month, day):
			return _("Month {0} has no day {1}.").format(month, day)
	return None


def _days_of(window) -> frozenset[tuple[int, int]]:
	"""Every (month, day) of a leap year inside `window`; every day for None (all year)."""
	days = []
	for month in range(1, 13):
		for day in range(1, 32):
			if _is_day(month, day):
				days.append((month, day))
	if window is None:
		return frozenset(days)
	start, end = window
	on = [(m, d) for m, d in days if month_day_in_window(date(_LEAP_YEAR, m, d), start, end)]
	return frozenset(on)


def overlapping_rules(rules) -> tuple | None:
	"""The first two rules that would tie for a case, or None.

	Two windowed rules for the same type and area tie on a day they share; two all-year rules for
	the same type and area tie every day. A windowed rule and an all-year one for the same type
	and area do not: the windowed one wins inside its window, the all-year one outside it.
	"""
	seen: dict[tuple[str, str, bool], list] = {}
	for rule in rules:
		window = window_of(rule)
		key = (
			rule.get("grievance_type") or "",
			rule.get("administrative_area") or "",
			window is not None,
		)
		days = _days_of(window)
		for other, other_days in seen.get(key, []):
			if days & other_days:
				return other, rule
		seen.setdefault(key, []).append((rule, days))
	return None


# Matching
# --------


def rules_of(service_category: str | None) -> list:
	"""The rules of the category's active SLA configuration, in table order. Empty when none."""
	if not service_category:
		return []
	config = frappe.db.get_value(
		SLA_CONFIGURATION,
		{"service_category": service_category, "active": 1},
		"name",
		order_by="modified desc",
	)
	if not config:
		return []
	return frappe.get_all(
		RULE,
		filters={"parent": config, "parenttype": SLA_CONFIGURATION, "parentfield": "sla_rules"},
		fields=list(RULE_FIELDS),
		order_by="idx asc",
	)


def _area_bounds(areas) -> dict[str, tuple[int, int]]:
	names = [area for area in set(areas) if area]
	if not names:
		return {}
	return {
		row.name: (row.lft, row.rgt)
		for row in frappe.get_all(
			"Grievance Administrative Area",
			filters={"name": ["in", names]},
			fields=["name", "lft", "rgt"],
		)
		if row.lft is not None and row.rgt is not None
	}


def match_rule(rules, grievance_type: str | None, area: str | None, on: date):
	"""The most specific of `rules` that matches a case of this type and area filed `on`, or None."""
	bounds = _area_bounds([area, *(rule.get("administrative_area") for rule in rules)])
	case_bounds = bounds.get(area) if area else None
	best, best_key = None, None
	for rule in rules:
		if rule.get("grievance_type") and rule.grievance_type != grievance_type:
			continue
		span = float("inf")
		if rule.get("administrative_area"):
			rule_bounds = bounds.get(rule.administrative_area)
			if not case_bounds or not rule_bounds:
				continue
			if not (rule_bounds[0] <= case_bounds[0] and case_bounds[1] <= rule_bounds[1]):
				continue
			span = rule_bounds[1] - rule_bounds[0]
		window = window_of(rule)
		if window and (window_error(rule) or not month_day_in_window(on, *window)):
			continue
		# Larger is more specific: the type's own rule, then the narrowest area, then a window.
		key = (1 if rule.get("grievance_type") else 0, -span, 1 if window else 0, -(rule.get("idx") or 0))
		if best_key is None or key > best_key:
			best, best_key = rule, key
	return best


def classify(
	grievance_type: str | None,
	area: str | None = None,
	on: date | str | None = None,
	service_category: str | None = None,
) -> Classification:
	"""The classification of a case of `grievance_type` in `area`, filed on `on` (default today).

	The rules are the category's: `service_category` when given (the case's), else the type's own.
	A type that does not exist is Standard, Normal and not sensitive, never an error.
	"""
	if not grievance_type:
		return STANDARD
	flags = frappe.db.get_value(
		"Grievance Type", grievance_type, ["is_sensitive", "service_category"], as_dict=True
	)
	if not flags:
		return STANDARD
	on = getdate(on) if on else getdate(now_datetime())
	rule = match_rule(rules_of(service_category or flags.service_category), grievance_type, area, on)
	if not rule:
		return Classification(bool(flags.is_sensitive), C.HANDLING_STANDARD, C.PRIORITY_NORMAL)
	urgent = bool(rule.is_urgent)
	return Classification(
		is_sensitive=bool(flags.is_sensitive),
		handling_class=C.HANDLING_URGENT if urgent else C.HANDLING_STANDARD,
		priority=rule.priority or (C.PRIORITY_URGENT if urgent else C.PRIORITY_NORMAL),
		sla_days=rule.sla_days or None,
		rule=rule.rule_label or f"#{rule.idx}",
	)


def classify_grievance(doc) -> Classification:
	"""Classify a Grievance document and write the result onto it.

	The filing date is the document's `creation` once it has one (a case loaded from history
	keeps the season it was filed in), otherwise now. Does not save: the caller is inside
	`validate`, or saves the case itself.
	"""
	result = classify(
		doc.grievance_type, doc.administrative_area, doc.get("creation"), doc.get("service_category")
	)
	for fieldname, value in result.as_fields().items():
		doc.set(fieldname, value)
	if result.is_sensitive:
		issue_pin(doc)
	return result


def sla_days_for(grievance, service_category: str | None = None) -> int | None:
	"""The SLA days a rule gives this case, or None to keep the category's own `sla_days`.

	Read at the filing date, like the rest of the classification.
	"""
	return classify(
		grievance.get("grievance_type"),
		grievance.get("administrative_area"),
		grievance.get("creation"),
		service_category or grievance.get("service_category"),
	).sla_days


# PIN
# ---


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
