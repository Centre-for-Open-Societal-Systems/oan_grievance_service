"""The configuration API of sensitive, urgent and seasonal handling (STG-514).

Sensitive is a flag of the grievance type. Urgency, priority and seasonal SLA days are rules on the
category's SLA configuration (`rules` of /sla-configurations).
"""

from datetime import date
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.sla_settings import list_sla_configurations, update_sla_configuration
from oan_grievance_service.api.v1.taxonomy import (
	create_grievance_type,
	list_grievance_types,
	update_grievance_type,
)
from oan_grievance_service.services import classification
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_user_with_role, an_administrative_area
from oan_grievance_service.tests.test_category_assignment import _keep_transaction
from oan_grievance_service.tests.test_classification import a_category, a_type

IRRIGATION = {"start_month": 10, "start_day": 1, "end_month": 4, "end_day": 30}


class ApiFixture(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.savepoint = "sla_rules_api"
		frappe.db.savepoint(self.savepoint)
		self.category = a_category()
		self.config = frappe.db.get_value(
			"Grievance SLA Configuration", {"service_category": self.category, "active": 1}
		)

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
		# Urgency is not a type field any more.
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
	def configuration(self):
		listed = list_sla_configurations(service_category=self.category)["data"]["sla_configurations"]
		return next(row for row in listed if row["name"] == self.config)

	def test_a_configuration_with_no_rules_lists_none(self):
		self.assertEqual(self.configuration()["rules"], [])

	def test_rules_are_set_and_listed(self):
		woreda = an_administrative_area(f"Woreda {frappe.generate_hash(length=5)}", None, "Woreda")
		kind = a_type(self.category)
		result = update_sla_configuration(
			self.config,
			rules=[
				{"rule_label": "Dry season", **IRRIGATION, "is_urgent": True, "sla_days": 3},
				{
					"administrative_area": woreda,
					"start_month": 11,
					"start_day": 1,
					"end_month": 3,
					"end_day": 31,
					"is_urgent": True,
				},
				{"administrative_area": woreda},
				{"grievance_type": kind, "priority": "High"},
			],
		)
		self.assertEqual(result["status"], "success", msg=result)
		rules = result["data"]["sla_configuration"]["rules"]
		self.assertEqual(len(rules), 4)
		self.assertEqual(
			(rules[0]["rule_label"], rules[0]["start_month"], rules[0]["is_urgent"], rules[0]["sla_days"]),
			("Dry season", 10, True, 3),
		)
		self.assertEqual((rules[1]["administrative_area"], rules[1]["end_month"]), (woreda, 3))
		self.assertEqual(
			(rules[2]["is_urgent"], rules[2]["start_month"], rules[2]["priority"]), (False, None, None)
		)
		self.assertEqual((rules[3]["grievance_type"], rules[3]["priority"]), (kind, "High"))
		self.assertEqual(self.configuration()["rules"], rules)

	def test_the_rules_change_the_next_case_without_code(self):
		kind = a_type(self.category)
		update_sla_configuration(self.config, rules=[{**IRRIGATION, "is_urgent": True, "sla_days": 3}])
		inside = classification.classify(kind, None, date(2026, 11, 15))
		self.assertEqual((inside.handling_class, inside.sla_days), (C.HANDLING_URGENT, 3))
		outside = classification.classify(kind, None, date(2026, 6, 15))
		self.assertEqual((outside.handling_class, outside.sla_days), (C.HANDLING_STANDARD, None))

	def test_rules_are_replaced_as_a_whole(self):
		update_sla_configuration(self.config, rules=[{**IRRIGATION, "is_urgent": True}, {"priority": "High"}])
		result = update_sla_configuration(self.config, rules=[{"is_urgent": True}])
		self.assertEqual(len(result["data"]["sla_configuration"]["rules"]), 1)

	def test_empty_or_null_removes_every_rule(self):
		for empty in ([], None):
			update_sla_configuration(self.config, rules=[{"is_urgent": True}])
			result = update_sla_configuration(self.config, rules=empty)
			self.assertEqual(result["data"]["sla_configuration"]["rules"], [], msg=result)

	def test_other_fields_leave_the_rules_alone(self):
		update_sla_configuration(self.config, rules=[{"is_urgent": True}])
		result = update_sla_configuration(self.config, sla_days=9)
		self.assertEqual(result["data"]["sla_configuration"]["sla_days"], 9)
		self.assertEqual(len(result["data"]["sla_configuration"]["rules"]), 1)

	def test_an_invalid_window_is_a_validation_error(self):
		for rule in (
			{"start_month": 2, "start_day": 30, "end_month": 3, "end_day": 1},
			{"start_month": 2, "start_day": 1},
			{"start_month": 13, "start_day": 1, "end_month": 3, "end_day": 1},
			{"priority": "Critical"},
			{"sla_days": 0},
		):
			with self.subTest(rule=rule), _keep_transaction():
				result = update_sla_configuration(self.config, rules=[rule])
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_rules_that_would_tie_are_a_validation_error(self):
		with _keep_transaction():
			result = update_sla_configuration(
				self.config,
				rules=[
					{**IRRIGATION, "is_urgent": True},
					{"start_month": 3, "start_day": 1, "end_month": 5, "end_day": 31},
				],
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_a_type_of_another_category_is_a_validation_error(self):
		other = a_type(a_category())
		with _keep_transaction():
			result = update_sla_configuration(
				self.config, rules=[{"grievance_type": other, "is_urgent": True}]
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_only_a_writer_of_the_configuration_sets_rules(self):
		frappe.set_user(a_user_with_role("Grievance Officer", "slarules"))
		with _keep_transaction():
			result = update_sla_configuration(self.config, rules=[{"is_urgent": True}])
		self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
