"""The configuration API of sensitive, urgent and seasonal handling (STG-514).

Sensitive is a flag of the grievance type. SLA days and urgency (priority follows it) are the category's SLA
rules, one record each, at /sla-rules.
"""

from datetime import date
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.sla_rules import (
	create_rule,
	delete_rule,
	get_rule,
	list_rules,
	update_rule,
)
from oan_grievance_service.api.v1.taxonomy import (
	create_grievance_type,
	list_grievance_types,
	update_grievance_type,
)
from oan_grievance_service.services import classification
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_user_with_role, an_administrative_area
from oan_grievance_service.tests.test_category_assignment import _keep_transaction
from oan_grievance_service.tests.test_classification import (
	BASE_SLA_DAYS,
	a_bare_category,
	a_category,
	a_type,
)

IRRIGATION = {"start_month": 10, "start_day": 1, "end_month": 4, "end_day": 30}
MARCH_TO_MAY = {"start_month": 3, "start_day": 1, "end_month": 5, "end_day": 31}
# A window that shares no day with IRRIGATION (October to April).
MARCH_TO_MAY_ELSEWHERE = {"start_month": 5, "start_day": 1, "end_month": 6, "end_day": 30}
RULE = "Grievance SLA Rule"


class ApiFixture(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.savepoint = "sla_rules_api"
		frappe.db.savepoint(self.savepoint)
		self.category = a_category()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point=self.savepoint)
		super().tearDown()


class TestGrievanceTypeSensitiveFlag(ApiFixture):
	def create(self, **extra):
		return create_grievance_type(
			service_category=self.category, type_name=f"Flags {frappe.generate_hash(length=6)}", **extra
		)

	def test_an_administrator_creates_a_sensitive_type(self):
		result = self.create(is_sensitive=True)
		self.assertEqual(result["status"], "success", msg=result)
		record = result["data"]["grievance_type"]
		self.assertTrue(record["is_sensitive"])
		# Urgency is not a type field.
		self.assertNotIn("is_urgent", record)
		self.assertNotIn("urgency_season", record)

	def test_a_type_is_updated_to_sensitive_and_back(self):
		kind = self.create()["data"]["grievance_type"]["grievance_type_id"]
		on = update_grievance_type(kind, is_sensitive=True)
		self.assertTrue(on["data"]["grievance_type"]["is_sensitive"], msg=on)
		off = update_grievance_type(kind, is_sensitive=False)
		self.assertFalse(off["data"]["grievance_type"]["is_sensitive"], msg=off)

	def test_null_clears_the_flag(self):
		kind = self.create(is_sensitive=True)["data"]["grievance_type"]["grievance_type_id"]
		cleared = update_grievance_type(kind, is_sensitive=None)
		self.assertEqual(cleared["status"], "success", msg=cleared)
		self.assertFalse(cleared["data"]["grievance_type"]["is_sensitive"])

	def test_the_list_shows_the_flag(self):
		self.create(is_sensitive=True)
		listed = list_grievance_types(service_category=self.category, page_size=100)["data"][
			"grievance_types"
		]
		self.assertTrue(any(row["is_sensitive"] for row in listed))

	def test_only_an_administrator_sets_the_flag(self):
		kind = self.create()["data"]["grievance_type"]["grievance_type_id"]
		with (
			_keep_transaction(),
			mock.patch("oan_grievance_service.api.v1.taxonomy.is_unrestricted", return_value=False),
		):
			for result in (
				update_grievance_type(kind, is_sensitive=True),
				update_grievance_type(kind, is_sensitive=None),
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


class TestSlaRulesApi(ApiFixture):
	def create(self, **fields):
		fields = {"sla_days": 3, **fields}
		result = create_rule(service_category=self.category, **fields)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["rule"]

	def base(self):
		return list_rules(service_category=self.category, is_base=True)["data"]["rules"][0]

	def refused(self, call, code="VALIDATION_ERROR"):
		with _keep_transaction():
			result = call()
		self.assertEqual(result.get("code"), code, msg=result)

	def test_a_rule_is_created_and_read_back(self):
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		kind = a_type(self.category)
		rule = self.create(
			rule_label="Dry season",
			grievance_type=kind,
			administrative_area=woreda,
			**IRRIGATION,
			sla_days=3,
			is_urgent=True,
		)
		self.assertEqual(
			{
				key: rule[key]
				for key in ("service_category", "rule_label", "grievance_type", "administrative_area")
			},
			{
				"service_category": self.category,
				"rule_label": "Dry season",
				"grievance_type": kind,
				"administrative_area": woreda,
			},
		)
		self.assertEqual(
			(rule["start_month"], rule["end_day"], rule["sla_days"], rule["is_urgent"], rule["priority"]),
			(10, 30, 3, True, "Urgent"),
		)
		self.assertFalse(rule["is_base"])
		self.assertEqual(get_rule(rule["name"])["data"]["rule"], rule)

	def test_the_category_has_its_base_rule(self):
		base = self.base()
		self.assertTrue(base["is_base"])
		self.assertEqual(
			(base["sla_days"], base["is_urgent"], base["priority"], base["start_month"]),
			(BASE_SLA_DAYS, False, None, None),
		)

	def test_sla_days_are_required_and_neither_they_nor_urgency_can_be_cleared(self):
		self.refused(lambda: create_rule(service_category=self.category, **IRRIGATION))
		rule = self.create(**IRRIGATION)
		for field in ("sla_days", "is_urgent"):
			with self.subTest(field=field):
				self.refused(lambda field=field: update_rule(rule["name"], **{field: None}))

	def test_priority_is_urgent_for_an_urgent_rule_and_null_otherwise(self):
		urgent = self.create(**IRRIGATION, is_urgent=True)
		plain = self.create(**MARCH_TO_MAY_ELSEWHERE)
		self.assertEqual((urgent["is_urgent"], urgent["priority"]), (True, "Urgent"))
		self.assertEqual((plain["is_urgent"], plain["priority"]), (False, None))
		# It follows the urgency when that changes.
		self.assertIsNone(update_rule(urgent["name"], is_urgent=False)["data"]["rule"]["priority"])
		self.assertEqual(update_rule(plain["name"], is_urgent=True)["data"]["rule"]["priority"], "Urgent")
		self.assertEqual(frappe.db.get_value(RULE, plain["name"], ["is_urgent", "priority"]), (1, "Urgent"))

	def test_priority_is_not_an_input(self):
		# It has one value and follows is_urgent, so a client never sends it, whatever the value.
		for value in ("Urgent", "Normal", "High", None):
			with self.subTest(value=value):
				self.refused(
					lambda value=value: create_rule(
						service_category=self.category, **IRRIGATION, sla_days=3, priority=value
					)
				)
		rule = self.create(**IRRIGATION)
		self.refused(lambda: update_rule(rule["name"], priority="Urgent"))

	def test_the_category_may_be_named_by_its_code(self):
		code = frappe.db.get_value("Grievance Service Category", self.category, "code")
		result = create_rule(service_category=code, **IRRIGATION, sla_days=3)
		self.assertEqual(result["data"]["rule"]["service_category"], self.category, msg=result)

	def test_the_list_filters_by_category_urgency_and_base(self):
		base = self.base()
		urgent = self.create(**IRRIGATION, is_urgent=True)
		high = self.create(start_month=5, start_day=1, end_month=6, end_day=30)
		elsewhere = create_rule(service_category=a_category(), **IRRIGATION, sla_days=3)["data"]["rule"]

		listed = list_rules(service_category=self.category)["data"]
		self.assertEqual(
			[row["name"] for row in listed["rules"]], [base["name"], urgent["name"], high["name"]]
		)
		self.assertEqual(listed["pagination"]["total_count"], 3)
		only_urgent = list_rules(service_category=self.category, is_urgent=True)["data"]["rules"]
		self.assertEqual([row["name"] for row in only_urgent], [urgent["name"]])
		narrower = list_rules(service_category=self.category, is_base=False)["data"]["rules"]
		self.assertEqual([row["name"] for row in narrower], [urgent["name"], high["name"]])
		self.assertNotIn(elsewhere["name"], [row["name"] for row in listed["rules"]])

	def test_a_patch_changes_only_what_it_sends(self):
		rule = self.create(rule_label="Dry", **IRRIGATION, is_urgent=True)
		updated = update_rule(rule["name"], sla_days=4)["data"]["rule"]
		self.assertEqual((updated["sla_days"], updated["rule_label"], updated["is_urgent"]), (4, "Dry", True))
		self.assertEqual((updated["start_month"], updated["priority"]), (10, "Urgent"))

	def test_null_clears_a_scope_field_and_a_null_window_is_all_year(self):
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		rule = self.create(rule_label="Dry", administrative_area=woreda, **IRRIGATION)
		result = update_rule(rule["name"], start_month=None, start_day=None, end_month=None, end_day=None)
		updated = result["data"]["rule"]
		self.assertEqual(
			[updated[key] for key in ("start_month", "start_day", "end_month", "end_day")],
			[None] * 4,
			msg=result,
		)
		self.assertFalse(updated["is_base"])

	def test_the_category_of_a_rule_is_fixed(self):
		rule = self.create(**IRRIGATION)
		self.refused(lambda: update_rule(rule["name"], service_category=a_category()))
		self.refused(lambda: update_rule(rule["name"]))

	def test_a_rule_is_deleted(self):
		rule = self.create(**IRRIGATION)
		result = delete_rule(rule["name"])
		self.assertEqual(result["data"]["rule"]["name"], rule["name"], msg=result)
		self.assertFalse(frappe.db.exists(RULE, rule["name"]))
		self.refused(lambda: get_rule(rule["name"]), "NOT_FOUND")
		self.refused(lambda: delete_rule(rule["name"]), "NOT_FOUND")
		self.refused(lambda: update_rule(rule["name"], sla_days=2), "NOT_FOUND")

	def test_the_base_rule_is_kept(self):
		base = self.base()
		self.refused(lambda: delete_rule(base["name"]))
		self.refused(lambda: update_rule(base["name"], **IRRIGATION))
		# Its values change like any rule's.
		updated = update_rule(base["name"], sla_days=9, is_urgent=True)["data"]["rule"]
		self.assertEqual(
			(updated["sla_days"], updated["is_urgent"], updated["priority"], updated["is_base"]),
			(9, True, "Urgent", True),
		)

	def test_a_category_needs_its_base_rule_before_a_narrower_one(self):
		bare = a_bare_category()
		self.refused(lambda: create_rule(service_category=bare, **IRRIGATION, sla_days=3))
		# Its base rule first, then the narrower one.
		self.assertEqual(create_rule(service_category=bare, sla_days=7)["status"], "success")
		self.assertEqual(
			create_rule(service_category=bare, **IRRIGATION, sla_days=3)["status"],
			"success",
		)

	def test_each_change_applies_to_the_next_case(self):
		kind = a_type(self.category)
		rule = self.create(**IRRIGATION, is_urgent=True, sla_days=3)
		inside = classification.classify(kind, None, date(2026, 11, 15))
		self.assertEqual((inside.handling_class, inside.sla_days), (C.HANDLING_URGENT, 3))
		outside = classification.classify(kind, None, date(2026, 6, 15))
		self.assertEqual((outside.handling_class, outside.sla_days), (C.HANDLING_STANDARD, BASE_SLA_DAYS))

		update_rule(rule["name"], sla_days=5)
		self.assertEqual(classification.classify(kind, None, date(2026, 11, 15)).sla_days, 5)
		delete_rule(rule["name"])
		self.assertEqual(
			classification.classify(kind, None, date(2026, 11, 15)).handling_class, C.HANDLING_STANDARD
		)

	def test_an_invalid_rule_is_a_validation_error(self):
		for fields in (
			{"start_month": 2, "start_day": 30, "end_month": 3, "end_day": 1},
			{"start_month": 2, "start_day": 1},
			{"start_month": 13, "start_day": 1, "end_month": 3, "end_day": 1},
			{**IRRIGATION, "sla_days": 0},
			{"administrative_area": "No such area"},
		):
			with self.subTest(fields=fields):
				self.refused(
					lambda fields=fields: create_rule(
						service_category=self.category, **{"sla_days": 3, **fields}
					)
				)
		self.refused(lambda: create_rule(service_category="No such category", sla_days=3))
		self.refused(lambda: create_rule(sla_days=3))

	def test_a_rule_that_would_tie_is_a_validation_error(self):
		self.create(**IRRIGATION, is_urgent=True)
		self.refused(lambda: create_rule(service_category=self.category, **MARCH_TO_MAY, sla_days=3))
		# A second base rule ties with the first.
		self.refused(lambda: create_rule(service_category=self.category, sla_days=3))
		# Moving a rule onto another's days is refused too.
		later = self.create(start_month=6, start_day=1, end_month=8, end_day=31)
		self.refused(lambda: update_rule(later["name"], start_month=3))

	def test_a_type_of_another_category_is_a_validation_error(self):
		other = a_type(a_category())
		self.refused(lambda: create_rule(service_category=self.category, grievance_type=other, sla_days=3))

	def test_an_officer_neither_reads_nor_writes_rules(self):
		rule = self.create(**IRRIGATION)
		frappe.set_user(a_user_with_role("Grievance Officer", "slarules"))
		for call in (
			lambda: list_rules(),
			lambda: get_rule(rule["name"]),
			lambda: create_rule(service_category=self.category, **MARCH_TO_MAY, sla_days=3),
			lambda: update_rule(rule["name"], sla_days=2),
			lambda: delete_rule(rule["name"]),
		):
			self.refused(call, "PERMISSION_DENIED")

	def test_a_review_officer_reads_but_does_not_write(self):
		rule = self.create(**IRRIGATION)
		frappe.set_user(a_user_with_role(C.ROLE_REVIEW_OFFICER, "slarules"))
		self.assertEqual(get_rule(rule["name"])["status"], "success")
		self.refused(lambda: update_rule(rule["name"], sla_days=2), "PERMISSION_DENIED")
		self.refused(lambda: delete_rule(rule["name"]), "PERMISSION_DENIED")
