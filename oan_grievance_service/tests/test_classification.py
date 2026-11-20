"""Sensitive and urgent classification (STG-514): the SLA rules, their windows and what a case carries.

The SLA rules are the one source of SLA days: every category with an SLA has a base rule (no type,
area or window), and narrower rules override it where they match.
"""

import secrets
import unittest
from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.grievance_sla.doctype.grievance_sla_configuration.grievance_sla_configuration import (
	save_sla_configuration,
)
from oan_grievance_service.services import classification, sla
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.classification import month_day_in_window
from oan_grievance_service.tests.fixtures import a_leaf_area, an_administrative_area

MEHER = ((5, 15), (8, 31))
FEB_TO_APR = ((2, 1), (4, 30))
DRY = ((10, 1), (4, 30))
HARVEST = ((10, 1), (12, 31))
BASE_SLA_DAYS = 15
RULE = "Grievance SLA Rule"


def _unique(label):
	return f"{label} {frappe.generate_hash(length=6)}"


def a_rule(
	window=None, *, area=None, grievance_type=None, urgent=False, priority=None, sla_days=BASE_SLA_DAYS
):
	"""A rule row; `window` is ((start month, day), (end month, day)) or None for all year.

	Every rule carries SLA days, urgency and priority; the priority defaults to Urgent for an urgent
	rule and Normal otherwise, as a convenience of this helper only.
	"""
	(start_month, start_day), (end_month, end_day) = window or ((None, None), (None, None))
	return {
		"rule_label": _unique("Rule"),
		"grievance_type": grievance_type,
		"administrative_area": area,
		"start_month": start_month,
		"start_day": start_day,
		"end_month": end_month,
		"end_day": end_day,
		"is_urgent": 1 if urgent else 0,
		"priority": priority or (C.PRIORITY_URGENT if urgent else C.PRIORITY_NORMAL),
		"sla_days": sla_days,
	}


def a_free_category_code():
	from oan_grievance_service.services.ticket_number import ALPHABET, CATEGORY_WIDTH

	taken = set(frappe.get_all("Grievance Service Category", pluck="code"))
	while True:
		code = "".join(secrets.choice(ALPHABET) for _ in range(CATEGORY_WIDTH))
		# Leave the matrix's own codes (C01 to C19) to the seed.
		if code not in taken and not (code[0] == "C" and code[1:].isdigit()):
			return code


def a_bare_category():
	"""A new category with no SLA configuration and no rule."""
	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Service Category",
				"category_name": _unique("Category"),
				"code": a_free_category_code(),
				"is_active": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def a_category(rules=(), sla_days=BASE_SLA_DAYS):
	"""A new category with an active SLA configuration, its base rule of `sla_days`, and `rules`.

	A rule in `rules` with no type, area or window replaces the base rule's values.
	"""
	category = a_bare_category()
	save_sla_configuration(category, sla_days)
	add_rules(category, rules)
	return category


def add_rules(category, rules):
	"""Add each rule to the category, in order; a base-scope rule updates the base rule. Returns the names."""
	names = []
	for rule in rules:
		if classification.is_base(frappe._dict(rule)):
			doc = frappe.get_doc(RULE, {"service_category": category, "is_base": 1})
			doc.update(rule)
			doc.save(ignore_permissions=True)
		else:
			doc = frappe.get_doc({"doctype": RULE, "service_category": category, **rule})
			doc.insert(ignore_permissions=True)
		names.append(doc.name)
	return names


def set_rules(category, rules):
	"""Make `rules` the category's rules: the others go and the base rule is reset to Standard,
	Normal and the base SLA days, unless `rules` sets it. Returns the names of `rules`."""
	for name in frappe.get_all(RULE, filters={"service_category": category, "is_base": 0}, pluck="name"):
		frappe.delete_doc(RULE, name, ignore_permissions=True, force=True)
	add_rules(category, [a_rule()])
	return add_rules(category, rules)


def a_type(category, sensitive=0):
	return (
		frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": _unique("Type"),
				"service_category": category,
				"is_active": 1,
				"is_sensitive": sensitive,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


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

	def test_february_to_april(self):
		self.check(
			FEB_TO_APR,
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
				(date(2026, 12, 31), True),
				# The year transition: the window is still open on 1 January.
				(date(2027, 1, 1), True),
				(date(2027, 4, 30), True),
				(date(2027, 5, 1), False),
			],
		)

	def test_leap_day_is_inside_a_window_that_spans_february(self):
		self.assertTrue(month_day_in_window(date(2028, 2, 29), *DRY))
		self.assertTrue(month_day_in_window(date(2028, 2, 29), *FEB_TO_APR))
		self.assertFalse(month_day_in_window(date(2028, 2, 29), *MEHER))

	def test_harvest_marketing_peak(self):
		self.check(
			HARVEST,
			[
				(date(2026, 9, 30), False),
				(date(2026, 10, 1), True),
				(date(2026, 12, 31), True),
				(date(2027, 1, 1), False),
			],
		)

	def test_a_one_day_window(self):
		self.assertTrue(month_day_in_window(date(2026, 6, 1), (6, 1), (6, 1)))
		self.assertFalse(month_day_in_window(date(2026, 6, 2), (6, 1), (6, 1)))


class TestRuleShape(unittest.TestCase):
	"""What makes a window a window, which rules would tie, and which rule is a base rule. No database."""

	def rule(self, window=None, **values):
		return frappe._dict(a_rule(window, **values))

	def test_all_four_window_fields_or_none(self):
		self.assertIsNone(classification.window_error(self.rule()))
		self.assertIsNone(classification.window_error(self.rule(MEHER)))
		half = self.rule(MEHER)
		half.end_day = None
		self.assertIsNotNone(classification.window_error(half))

	def test_a_window_names_real_days(self):
		self.assertIsNotNone(classification.window_error(self.rule(((13, 1), (2, 1)))))
		self.assertIsNotNone(classification.window_error(self.rule(((2, 30), (3, 1)))))
		self.assertIsNotNone(classification.window_error(self.rule(((4, 1), (4, 31)))))
		# 29 February is a day of the year, every year: the window is checked against a leap year.
		self.assertIsNone(classification.window_error(self.rule(((2, 29), (3, 1)))))

	def test_two_windows_for_the_same_scope_that_share_a_day_tie(self):
		# October to April and March to May share March and April, across the year end.
		self.assertIsNotNone(classification.overlapping_rules([self.rule(DRY), self.rule(((3, 1), (5, 31)))]))
		self.assertIsNotNone(classification.overlapping_rules([self.rule(), self.rule()]))

	def test_windows_that_do_not_share_a_day_do_not_tie(self):
		self.assertIsNone(classification.overlapping_rules([self.rule(FEB_TO_APR), self.rule(MEHER)]))

	def test_a_window_and_an_all_year_rule_for_one_scope_do_not_tie(self):
		self.assertIsNone(classification.overlapping_rules([self.rule(DRY), self.rule()]))

	def test_rules_for_other_scopes_do_not_tie(self):
		self.assertIsNone(
			classification.overlapping_rules([self.rule(DRY), self.rule(DRY, area="Some woreda")])
		)
		self.assertIsNone(
			classification.overlapping_rules([self.rule(DRY), self.rule(DRY, grievance_type="T-1")])
		)

	def test_only_a_rule_that_names_nothing_is_a_base_rule(self):
		self.assertTrue(classification.is_base(self.rule(urgent=True, sla_days=3)))
		for narrower in (self.rule(DRY), self.rule(area="Some woreda"), self.rule(grievance_type="T-1")):
			self.assertFalse(classification.is_base(narrower))


class TestRuleValidation(FrappeTestCase):
	"""A rule that is not a window, that would tie with another of its category, or that leaves the
	category without its base rule, is refused."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.category = a_category()

	def refused(self, rules):
		with self.assertRaises(frappe.ValidationError):
			set_rules(self.category, rules)

	def base(self, category=None):
		return frappe.get_doc(RULE, {"service_category": category or self.category, "is_base": 1})

	def test_a_half_window_is_refused(self):
		rule = a_rule(MEHER)
		rule["end_day"] = None
		self.refused([rule])

	def test_a_day_that_does_not_exist_is_refused(self):
		self.refused([a_rule(((2, 30), (3, 1)))])

	def test_a_type_of_another_category_is_refused(self):
		other = a_type(a_category())
		self.refused([a_rule(grievance_type=other, urgent=True)])

	def test_two_rules_that_would_tie_are_refused(self):
		woreda = an_administrative_area(_unique("Woreda"), None, "Woreda")
		self.refused([a_rule(DRY, urgent=True), a_rule(((3, 1), (5, 31)), priority=C.PRIORITY_HIGH)])
		self.refused([a_rule(area=woreda, urgent=True), a_rule(area=woreda, sla_days=3)])

	def test_sla_days_are_required_and_positive(self):
		for days in (None, 0, -1):
			with self.subTest(days=days):
				self.refused([a_rule(DRY, sla_days=days)])

	def test_a_rule_is_not_tied_with_itself_when_it_is_saved_again(self):
		(name,) = add_rules(self.category, [a_rule(DRY, urgent=True)])
		rule = frappe.get_doc(RULE, name)
		rule.sla_days = 3
		rule.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(RULE, name, "sla_days"), 3)

	def test_a_rule_moved_onto_anothers_days_is_refused(self):
		add_rules(self.category, [a_rule(DRY, urgent=True)])
		(name,) = add_rules(self.category, [a_rule(MEHER, urgent=True)])
		rule = frappe.get_doc(RULE, name)
		rule.start_month, rule.start_day = 3, 1
		with self.assertRaises(frappe.ValidationError):
			rule.save(ignore_permissions=True)

	def test_rules_of_other_categories_never_tie(self):
		add_rules(self.category, [a_rule(DRY, urgent=True)])
		add_rules(a_category(), [a_rule(DRY, urgent=True)])

	def test_a_valid_set_of_rules_is_saved(self):
		woreda = an_administrative_area(_unique("Woreda"), None, "Woreda")
		kind = a_type(self.category)
		names = set_rules(
			self.category,
			[
				a_rule(FEB_TO_APR, urgent=True),
				a_rule(MEHER, urgent=True, sla_days=3),
				a_rule(urgent=True, grievance_type=kind),
				a_rule(DRY, area=woreda, urgent=True),
				a_rule(area=woreda),
				a_rule(((2, 29), (2, 29)), area=woreda, grievance_type=kind, priority=C.PRIORITY_HIGH),
			],
		)
		self.assertEqual(len(names), 6)
		# The six, and the base rule.
		self.assertEqual(frappe.db.count(RULE, {"service_category": self.category}), 7)

	def test_a_category_with_an_sla_has_its_base_rule(self):
		base = self.base()
		self.assertEqual((base.is_base, base.sla_days, base.is_urgent), (1, BASE_SLA_DAYS, 0))
		self.assertEqual(base.priority, C.PRIORITY_NORMAL)

	def test_saving_the_sla_again_changes_the_base_rule_not_adds_one(self):
		save_sla_configuration(self.category, 9)
		self.assertEqual(frappe.db.count(RULE, {"service_category": self.category}), 1)
		self.assertEqual(self.base().sla_days, 9)

	def test_a_second_base_rule_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": RULE, "service_category": self.category, **a_rule(sla_days=3)}).insert(
				ignore_permissions=True
			)

	def test_a_narrower_rule_needs_the_base_rule_first(self):
		bare = a_bare_category()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": RULE, "service_category": bare, **a_rule(DRY, urgent=True)}).insert(
				ignore_permissions=True
			)

	def test_the_base_rule_cannot_be_deleted(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(RULE, self.base().name, ignore_permissions=True)

	def test_the_base_rule_cannot_be_narrowed(self):
		base = self.base()
		base.start_month, base.start_day, base.end_month, base.end_day = 10, 1, 4, 30
		with self.assertRaises(frappe.ValidationError):
			base.save(ignore_permissions=True)

	def test_an_sla_configuration_needs_the_base_rule(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Grievance SLA Configuration", "service_category": a_bare_category(), "active": 1}
			).insert(ignore_permissions=True)


class TestClassification(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		region = an_administrative_area(_unique("Region"), None, "Region")
		cls.woreda = an_administrative_area(_unique("Woreda"), region, "Woreda")
		cls.kebele = an_administrative_area(_unique("Kebele"), cls.woreda, "Kebele")
		# A woreda with its own early season (what a Belg woreda is), and one whose irrigation
		# season is its own instead of the category's.
		cls.early_woreda = an_administrative_area(_unique("Early woreda"), region, "Woreda")
		cls.early_kebele = an_administrative_area(_unique("Early kebele"), cls.early_woreda, "Kebele")
		cls.own_woreda = an_administrative_area(_unique("Own woreda"), region, "Woreda")
		cls.own_kebele = an_administrative_area(_unique("Own kebele"), cls.own_woreda, "Kebele")

		# C01: Meher everywhere, 3 days in season; February to April as well in the early woreda.
		cls.inputs = a_category(
			[
				a_rule(MEHER, urgent=True, sla_days=3),
				a_rule(FEB_TO_APR, area=cls.early_woreda, urgent=True),
			]
		)
		cls.c01 = a_type(cls.inputs)
		# C02: October to April, but the own woreda follows November to March instead: its own
		# season, and its own all-year row for the rest of the year.
		cls.irrigation = a_category(
			[
				a_rule(DRY, urgent=True),
				a_rule(((11, 1), (3, 31)), area=cls.own_woreda, urgent=True),
				a_rule(area=cls.own_woreda),
			]
		)
		cls.c02 = a_type(cls.irrigation)
		cls.markets = a_category([a_rule(HARVEST, priority=C.PRIORITY_HIGH)])
		cls.c09 = a_type(cls.markets)
		# C17: urgent all year, so its base rule is urgent.
		cls.pests = a_category([a_rule(urgent=True)])
		cls.c17 = a_type(cls.pests)
		cls.plain = a_category()
		cls.plain_type = a_type(cls.plain)

	def classify(self, grievance_type, area, on):
		return classification.classify(grievance_type, area, on)

	def assertUrgent(self, result, urgent):
		self.assertEqual(result.handling_class, C.HANDLING_URGENT if urgent else C.HANDLING_STANDARD)
		self.assertEqual(result.priority, C.PRIORITY_URGENT if urgent else C.PRIORITY_NORMAL)

	def test_c01_follows_the_meher_window(self):
		for day, urgent in [
			(date(2026, 5, 14), False),
			(date(2026, 5, 15), True),
			(date(2026, 7, 10), True),
			(date(2026, 8, 31), True),
			(date(2026, 9, 1), False),
			(date(2026, 3, 1), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c01, self.woreda, day), urgent)

	def test_an_area_rule_adds_its_window_for_that_woreda_and_the_kebeles_inside(self):
		for area in (self.early_woreda, self.early_kebele):
			with self.subTest(area=area):
				self.assertUrgent(self.classify(self.c01, area, date(2026, 1, 31)), False)
				self.assertUrgent(self.classify(self.c01, area, date(2026, 2, 1)), True)
				self.assertUrgent(self.classify(self.c01, area, date(2026, 4, 30)), True)
				self.assertUrgent(self.classify(self.c01, area, date(2026, 5, 1)), False)
				# Outside its own window the woreda still follows the category's rule.
				self.assertUrgent(self.classify(self.c01, area, date(2026, 7, 1)), True)
		self.assertUrgent(self.classify(self.c01, self.kebele, date(2026, 3, 1)), False)

	def test_no_area_matches_only_the_rules_without_one(self):
		self.assertUrgent(self.classify(self.c01, None, date(2026, 7, 1)), True)
		self.assertUrgent(self.classify(self.c01, None, date(2026, 3, 1)), False)

	def test_c02_with_the_year_transition(self):
		for day, urgent in [
			(date(2026, 9, 30), False),
			(date(2026, 10, 1), True),
			(date(2027, 1, 1), True),
			(date(2027, 4, 30), True),
			(date(2027, 5, 1), False),
		]:
			with self.subTest(day=day.isoformat()):
				self.assertUrgent(self.classify(self.c02, self.woreda, day), urgent)

	def test_a_woreda_with_its_own_rows_replaces_the_categorys_season(self):
		for area in (self.own_woreda, self.own_kebele):
			for day, urgent in [
				(date(2026, 10, 15), False),  # the category says yes; the woreda's season starts in November
				(date(2026, 11, 1), True),
				(date(2027, 3, 31), True),
				(date(2027, 4, 1), False),  # the category says yes; the woreda's ended in March
				(date(2027, 7, 1), False),
			]:
				with self.subTest(area=area, day=day.isoformat()):
					self.assertUrgent(self.classify(self.c02, area, day), urgent)

	def test_the_narrowest_area_wins(self):
		inner = an_administrative_area(_unique("Inner"), self.own_woreda, "Kebele")
		category = a_category(
			[
				a_rule(DRY, urgent=True),
				a_rule(((11, 1), (3, 31)), area=self.own_woreda, urgent=True),
				a_rule(area=self.own_woreda),
				a_rule(((1, 1), (1, 31)), area=inner, urgent=True),
				a_rule(area=inner),
			]
		)
		kind = a_type(category)
		self.assertUrgent(self.classify(kind, inner, date(2026, 11, 15)), False)
		self.assertUrgent(self.classify(kind, inner, date(2027, 1, 15)), True)
		self.assertUrgent(self.classify(kind, self.own_kebele, date(2026, 11, 15)), True)

	def test_a_types_own_rule_wins_over_the_categorys(self):
		category = a_category([a_rule(urgent=True, sla_days=5)])
		quiet, loud = a_type(category), a_type(category)
		add_rules(category, [a_rule(grievance_type=quiet, sla_days=12)])
		self.assertUrgent(self.classify(quiet, self.woreda, date(2026, 6, 1)), False)
		self.assertEqual(self.classify(quiet, self.woreda, date(2026, 6, 1)).sla_days, 12)
		self.assertUrgent(self.classify(loud, self.woreda, date(2026, 6, 1)), True)
		self.assertEqual(self.classify(loud, self.woreda, date(2026, 6, 1)).sla_days, 5)

	def test_c17_is_urgent_every_day_of_the_year(self):
		for month in range(1, 13):
			with self.subTest(month=month):
				self.assertUrgent(self.classify(self.c17, self.woreda, date(2026, month, 1)), True)
		self.assertUrgent(self.classify(self.c17, self.early_woreda, date(2028, 2, 29)), True)

	def test_c09_raises_priority_in_season_and_never_becomes_urgent(self):
		for day, priority in [
			(date(2026, 9, 30), C.PRIORITY_NORMAL),
			(date(2026, 10, 1), C.PRIORITY_HIGH),
			(date(2026, 12, 31), C.PRIORITY_HIGH),
			(date(2027, 1, 1), C.PRIORITY_NORMAL),
		]:
			with self.subTest(day=day.isoformat()):
				result = self.classify(self.c09, self.woreda, day)
				self.assertEqual(result.handling_class, C.HANDLING_STANDARD)
				self.assertEqual(result.priority, priority)

	def test_the_sla_days_are_the_matching_rules(self):
		self.assertEqual(self.classify(self.c01, self.woreda, date(2026, 7, 1)).sla_days, 3)
		# Outside the window, the base rule's.
		self.assertEqual(self.classify(self.c01, self.woreda, date(2026, 10, 1)).sla_days, BASE_SLA_DAYS)
		self.assertEqual(self.classify(self.c17, self.woreda, date(2026, 10, 1)).sla_days, BASE_SLA_DAYS)

	def test_a_category_with_only_its_base_rule_is_standard_and_normal(self):
		result = self.classify(self.plain_type, self.woreda, date(2026, 7, 1))
		self.assertUrgent(result, False)
		self.assertEqual(result.sla_days, BASE_SLA_DAYS)

	def test_a_category_with_no_rule_has_no_sla(self):
		bare = a_bare_category()
		result = self.classify(a_type(bare), self.woreda, date(2026, 7, 1))
		self.assertUrgent(result, False)
		self.assertIsNone(result.sla_days)
		self.assertIsNone(classification.sla_days_for(bare))

	def test_a_case_with_no_type_gets_the_rules_for_the_whole_category(self):
		case = frappe._dict(
			grievance_type=None, administrative_area=self.woreda, creation="2026-07-01 09:00:00"
		)
		self.assertEqual(classification.sla_days_for(self.inputs, case), 3)
		case.creation = "2026-10-01 09:00:00"
		self.assertEqual(classification.sla_days_for(self.inputs, case), BASE_SLA_DAYS)
		self.assertEqual(classification.sla_days_for(self.inputs), BASE_SLA_DAYS)

	def test_a_type_that_does_not_exist_is_standard_not_an_error(self):
		self.assertEqual(
			self.classify("No such type " + frappe.generate_hash(length=6), self.woreda, None),
			classification.STANDARD,
		)

	def test_the_rules_belong_to_the_category_not_to_its_sla_configuration(self):
		frappe.db.set_value("Grievance SLA Configuration", {"service_category": self.pests}, "active", 0)
		self.addCleanup(
			frappe.db.set_value, "Grievance SLA Configuration", {"service_category": self.pests}, "active", 1
		)
		self.assertUrgent(self.classify(self.c17, self.woreda, date(2026, 6, 1)), True)

	def test_sensitive_is_the_types_flag_whatever_the_rules(self):
		sensitive = a_type(self.inputs, sensitive=1)
		for day in (date(2026, 1, 1), date(2026, 7, 1)):
			self.assertTrue(self.classify(sensitive, self.woreda, day).is_sensitive)
		self.assertFalse(self.classify(self.c01, self.woreda, date(2026, 7, 1)).is_sensitive)

	def test_the_filing_date_defaults_to_today(self):
		today = frappe.utils.getdate()
		self.assertEqual(
			self.classify(self.c17, self.woreda, None), self.classify(self.c17, self.woreda, today)
		)

	def test_the_result_names_the_rule(self):
		meher = frappe.db.get_value(RULE, {"service_category": self.inputs, "start_month": 5}, "rule_label")
		base = frappe.db.get_value(RULE, {"service_category": self.inputs, "is_base": 1}, "rule_label")
		self.assertEqual(self.classify(self.c01, self.woreda, date(2026, 7, 1)).rule, meher)
		self.assertEqual(self.classify(self.c01, self.woreda, date(2026, 10, 1)).rule, base)


class TestGrievanceClassification(FrappeTestCase):
	"""The case carries what the classifier decided, fixed at filing, and its SLA follows the rule."""

	def setUp(self):
		frappe.set_user("Administrator")

	def a_case(self, grievance_type, **values):
		from oan_grievance_service.tests.fixtures import a_grievance

		category = frappe.db.get_value("Grievance Type", grievance_type, "service_category")
		return a_grievance(grievance_type=grievance_type, service_category=category, **values)

	def test_a_sensitive_case_is_marked_and_gets_a_pin(self):
		case = self.a_case(a_type(a_category(), sensitive=1))
		self.assertEqual(case.is_sensitive, 1)
		pin = classification.pin_of(case)
		self.assertRegex(pin, r"^\d{6}$")
		# Encrypted at rest: the column holds no PIN.
		self.assertNotIn(pin, frappe.db.get_value("Grievance", case.name, "status_pin") or "")

	def test_a_case_that_is_not_sensitive_has_no_pin(self):
		case = self.a_case(a_type(a_category()))
		self.assertEqual(case.is_sensitive, 0)
		self.assertIsNone(classification.pin_of(case))

	def test_a_case_is_standard_and_normal_by_default(self):
		case = self.a_case(a_type(a_category()))
		self.assertEqual((case.handling_class, case.priority), (C.HANDLING_STANDARD, C.PRIORITY_NORMAL))

	def test_an_urgent_rule_makes_the_case_urgent(self):
		case = self.a_case(a_type(a_category([a_rule(urgent=True)])))
		self.assertEqual((case.handling_class, case.priority), (C.HANDLING_URGENT, C.PRIORITY_URGENT))

	def test_the_rules_sla_days_set_the_cases_deadline(self):
		case = self.a_case(a_type(a_category([a_rule(urgent=True, sla_days=3)])))
		policy = sla.resolve_policy(case.service_category, case)
		self.assertEqual(policy.sla_days, 3)
		sla.start_clock(case)
		case.reload()
		self.assertEqual(case.sla_days, 3)
		expected = sla.calculate_working_deadline(case.sla_start_at, 3, holiday_list_name=policy.holiday_list)
		self.assertEqual(frappe.utils.get_datetime(case.sla_due_date), expected)

	def test_outside_the_window_the_base_rules_sla_days_apply(self):
		category = a_category([a_rule(MEHER, urgent=True, sla_days=3)])
		case = self.a_case(a_type(category))
		# The rule is read at the filing date: one in season, one out of it.
		case.creation = frappe.utils.get_datetime("2026-07-01 09:00:00")
		self.assertEqual(sla.resolve_policy(category, case).sla_days, 3)
		case.creation = frappe.utils.get_datetime("2026-10-01 09:00:00")
		self.assertEqual(sla.resolve_policy(category, case).sla_days, BASE_SLA_DAYS)
		self.assertEqual(sla.resolve_policy(category).sla_days, BASE_SLA_DAYS)

	def test_a_category_with_a_configuration_but_no_rule_has_no_deadline(self):
		category = a_category()
		frappe.db.delete(RULE, {"service_category": category})
		self.assertEqual(sla.resolve_policy(category).sla_days, 0)

	def test_a_case_keeps_its_class_when_the_rules_change(self):
		category = a_category([a_rule(urgent=True)])
		case = self.a_case(a_type(category))
		set_rules(category, [])
		case.reload()
		case.closure_reason = "Saved again with the rules changed."
		case.save(ignore_permissions=True)
		self.assertEqual(case.handling_class, C.HANDLING_URGENT)

	def test_the_derived_fields_cannot_be_edited(self):
		case = self.a_case(a_type(a_category(), sensitive=1))
		case.is_sensitive = 0
		with self.assertRaises(frappe.ValidationError):
			case.save(ignore_permissions=True)

	def test_moving_a_case_to_a_sensitive_type_reclassifies_it(self):
		category = a_category()
		case = self.a_case(a_type(category))
		case.grievance_type = a_type(category, sensitive=1)
		case.flags.change_request = "test-change-request"
		case.save(ignore_permissions=True)
		self.assertEqual(case.is_sensitive, 1)
		self.assertRegex(classification.pin_of(case), r"^\d{6}$")

	def test_moving_a_case_to_a_type_with_its_own_rule_moves_its_deadline(self):
		category = a_category()
		before, after = a_type(category), a_type(category)
		add_rules(category, [a_rule(grievance_type=after, urgent=True, sla_days=2)])
		case = self.a_case(before)
		sla.start_clock(case)
		case.reload()
		self.assertEqual(case.sla_days, BASE_SLA_DAYS)
		case.grievance_type = after
		case.flags.change_request = "test-change-request"
		case.save(ignore_permissions=True)
		case.reload()
		self.assertEqual((case.sla_days, case.handling_class), (2, C.HANDLING_URGENT))

	def test_moving_a_case_between_types_with_the_same_sla_leaves_its_reminders(self):
		category = a_category()
		before, after = a_type(category), a_type(category)
		case = self.a_case(before)
		sla.start_clock(case)
		case.db_set("reminder_50_sent", 1)
		case.reload()
		case.grievance_type = after
		case.flags.change_request = "test-change-request"
		case.save(ignore_permissions=True)
		case.reload()
		self.assertEqual((case.sla_days, case.reminder_50_sent), (BASE_SLA_DAYS, 1))

	def test_a_new_pin_is_random_and_six_digits(self):
		pins = {classification.new_pin() for _ in range(20)}
		self.assertGreater(len(pins), 15)
		self.assertTrue(all(len(pin) == 6 and pin.isdigit() for pin in pins))

	def test_classify_grievance_reads_the_documents_creation_date(self):
		category = a_category([a_rule(MEHER, urgent=True)])
		doc = frappe.new_doc("Grievance")
		doc.grievance_type = a_type(category)
		doc.service_category = category
		doc.administrative_area = a_leaf_area()
		doc.creation = "2026-07-01 09:00:00"
		self.assertEqual(classification.classify_grievance(doc).handling_class, C.HANDLING_URGENT)
		doc.creation = "2026-10-01 09:00:00"
		self.assertEqual(classification.classify_grievance(doc).handling_class, C.HANDLING_STANDARD)
