"""The Routing Matrix's sensitive and urgent columns, checked against the workbook itself.

The data in `setup/urgency_matrix.py` is read from the matrix. When the workbook is on the
machine (it is not in this repo), the tests read the Sensitive Flag and Urgent Flag columns from it
and compare, so a drift between the data and the matrix fails here and not in production.
"""

import os
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.grievance_sla.doctype.grievance_sla_configuration.grievance_sla_configuration import (
	save_sla_configuration,
)
from oan_grievance_service.grievance_sla.doctype.grievance_sla_rule.grievance_sla_rule import GrievanceSLARule
from oan_grievance_service.patches import apply_sensitive_urgent_matrix
from oan_grievance_service.services import classification
from oan_grievance_service.services import constants as C
from oan_grievance_service.setup import urgency_matrix as matrix

WORKBOOK = Path(
	os.environ.get(
		"OAN_ROUTING_MATRIX",
		Path.home() / "frappe16-bench" / "RefferDocs" / "OAN_Grievance_Routing_Matrix_D2.xlsx",
	)
)
RULE = "Grievance SLA Rule"


def _window(rule):
	return (rule["start_month"], rule["start_day"], rule["end_month"], rule["end_day"])


class TestMatrixData(unittest.TestCase):
	def test_sensitive_is_exactly_the_eight_flagged_sub_types(self):
		self.assertEqual(
			set(matrix.SENSITIVE_TYPE_CODES),
			{"C13-01", "C13-02", "C13-03", "C13-04", "C15-01", "C15-02", "C15-03", "C16-01"},
		)

	def test_no_c04_or_c16_02_sub_type_is_sensitive(self):
		for code in matrix.SENSITIVE_TYPE_CODES:
			self.assertFalse(code.startswith("C04"), code)
		self.assertNotIn("C16-02", matrix.SENSITIVE_TYPE_CODES)

	def test_urgent_is_c01_c02_c07_and_c17_only(self):
		self.assertEqual(set(matrix.URGENT_CATEGORY_CODES), {"C01", "C02", "C07", "C17"})

	def test_c09_raises_priority_without_being_urgent(self):
		(harvest,) = matrix.SLA_RULES["C09"]
		self.assertEqual((harvest["is_urgent"], harvest["priority"]), (0, C.PRIORITY_HIGH))
		self.assertEqual(_window(harvest), (10, 1, 12, 31))

	def test_each_rule_matches_the_seasonal_calendar(self):
		self.assertEqual(_window(matrix.SLA_RULES["C01"][0]), (5, 15, 8, 31))
		self.assertEqual(_window(matrix.SLA_RULES["C02"][0]), (10, 1, 4, 30))
		self.assertEqual(_window(matrix.SLA_RULES["C07"][0]), (10, 1, 4, 30))
		self.assertEqual(_window(matrix.SLA_RULES["C17"][0]), (None, None, None, None))

	def test_every_rule_is_a_valid_rule_for_the_whole_category(self):
		rules = [frappe._dict(rule) for rules in matrix.SLA_RULES.values() for rule in rules]
		for rule in rules:
			with self.subTest(rule=rule.rule_label):
				self.assertIsNone(classification.window_error(rule))
				self.assertIsNone(rule.get("grievance_type"))
				self.assertIsNone(rule.get("administrative_area"))
				# The matrix gives no in-season turnaround, so the category's SLA days apply.
				self.assertIsNone(rule.get("sla_days"))

	def test_no_calendar_specific_values(self):
		for rules in matrix.SLA_RULES.values():
			for rule in rules:
				self.assertFalse({key for key in rule if key.startswith("eth_")}, rule)


@unittest.skipUnless(WORKBOOK.exists(), "the Routing Matrix workbook is not on this machine")
class TestMatrixAgainstTheWorkbook(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		import openpyxl

		sheet = openpyxl.load_workbook(WORKBOOK, data_only=True)["Grievance Routing"]
		header = [cell for cell in next(sheet.iter_rows(values_only=True))]
		column = {
			name: header.index(name) for name in ("Code", "Sensitive Flag", "Urgent Flag", "Urgent Season")
		}
		cls.rows = {
			row[column["Code"]]: {name: row[index] for name, index in column.items()}
			for row in sheet.iter_rows(min_row=2, values_only=True)
			if row[column["Code"]] and re.match(r"^C\d\d-\d\d$", row[column["Code"]])
		}

	def test_the_sensitive_column_is_the_sensitive_list(self):
		flagged = {code for code, row in self.rows.items() if row["Sensitive Flag"] == "Yes"}
		self.assertEqual(flagged, set(matrix.SENSITIVE_TYPE_CODES))

	def test_the_urgent_column_is_every_sub_type_of_the_urgent_categories(self):
		"""One rule per category is right only if the matrix flags all of its sub-types alike."""
		flagged = {code for code, row in self.rows.items() if row["Urgent Flag"] == "Yes"}
		of_urgent_categories = {
			code for code in self.rows if code.split("-")[0] in matrix.URGENT_CATEGORY_CODES
		}
		self.assertEqual(flagged, of_urgent_categories)

	def test_every_urgent_row_names_a_season(self):
		for code, row in self.rows.items():
			if row["Urgent Flag"] == "Yes":
				self.assertNotIn(row["Urgent Season"], (None, "—"), code)


class MatrixPatchCase(FrappeTestCase):
	"""Each test runs the patch on the site's own taxonomy and is rolled back afterwards."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.savepoint = "matrix_patch"
		frappe.db.savepoint(self.savepoint)

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)

	def category(self, code):
		"""The category with this matrix code, with an active SLA configuration and no rules."""
		name = frappe.db.get_value("Grievance Service Category", {"code": code})
		if not name:
			name = (
				frappe.get_doc(
					{
						"doctype": "Grievance Service Category",
						"category_name": f"{code} {frappe.generate_hash(length=6)}",
						"code": code,
						"is_active": 1,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		if not frappe.db.exists("Grievance SLA Configuration", {"service_category": name, "active": 1}):
			save_sla_configuration(name, 10)
		frappe.db.delete(RULE, {"service_category": name})
		return name

	def rules(self, code):
		category = frappe.db.get_value("Grievance Service Category", {"code": code})
		return [
			frappe.get_doc(RULE, name)
			for name in frappe.get_all(
				RULE, filters={"service_category": category}, pluck="name", order_by="creation asc"
			)
		]

	def a_type(self, code):
		existing = frappe.db.get_value("Grievance Type", {"type_code": code})
		if existing:
			frappe.db.set_value("Grievance Type", existing, "type_code", f"X{frappe.generate_hash(length=8)}")
		return (
			frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": f"{code} {frappe.generate_hash(length=6)}",
					"service_category": self.category(code.split("-")[0]),
					"type_code": code,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)


class TestApplyMatrixPatch(MatrixPatchCase):
	def test_the_patch_flags_the_sensitive_types(self):
		sensitive, plain = self.a_type("C13-03"), self.a_type("C03-01")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(frappe.db.get_value("Grievance Type", sensitive, "is_sensitive"), 1)
		self.assertEqual(frappe.db.get_value("Grievance Type", plain, "is_sensitive"), 0)

	def test_the_patch_leaves_c16_02_and_c04_alone(self):
		wrong_data, imposed = self.a_type("C16-02"), self.a_type("C04-05")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(frappe.db.get_value("Grievance Type", wrong_data, "is_sensitive"), 0)
		self.assertEqual(frappe.db.get_value("Grievance Type", imposed, "is_sensitive"), 0)

	def test_the_patch_adds_the_seasonal_rules(self):
		for code in matrix.SLA_RULES:
			self.category(code)
		apply_sensitive_urgent_matrix.execute()
		meher = self.rules("C01")[0]
		self.assertEqual((meher.rule_label, meher.is_urgent), ("Meher input season", 1))
		self.assertEqual((meher.start_month, meher.start_day, meher.end_month, meher.end_day), (5, 15, 8, 31))
		harvest = self.rules("C09")[0]
		self.assertEqual((harvest.is_urgent, harvest.priority), (0, C.PRIORITY_HIGH))
		pest = self.rules("C17")[0]
		self.assertEqual(pest.is_urgent, 1)
		self.assertIsNone(classification.window_of(pest))

	def test_a_case_filed_in_season_is_urgent_after_the_patch(self):
		kind = self.a_type("C01-03")
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(
			classification.classify(kind, None, date(2026, 7, 1)).handling_class, C.HANDLING_URGENT
		)
		self.assertEqual(
			classification.classify(kind, None, date(2026, 3, 1)).handling_class, C.HANDLING_STANDARD
		)

	def test_the_patch_does_not_overwrite_an_administrators_rules(self):
		category = self.category("C01")
		frappe.get_doc(
			{
				"doctype": RULE,
				"service_category": category,
				"rule_label": "Own",
				"is_urgent": 1,
				"sla_days": 2,
			}
		).insert(ignore_permissions=True)
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual([rule.rule_label for rule in self.rules("C01")], ["Own"])

	def test_the_patch_is_safe_to_run_twice(self):
		self.a_type("C15-01")
		self.category("C17")
		apply_sensitive_urgent_matrix.execute()
		apply_sensitive_urgent_matrix.execute()
		self.assertEqual(len(self.rules("C17")), 1)

	def test_the_others_are_still_applied_when_one_is_refused(self):
		self.category("C01")
		self.category("C17")
		sensitive = self.a_type("C15-02")
		c01 = frappe.db.get_value("Grievance Service Category", {"code": "C01"})
		original = GrievanceSLARule.validate

		def refuse_c01(doc):
			if doc.service_category == c01:
				raise frappe.ValidationError
			return original(doc)

		with mock.patch.object(GrievanceSLARule, "validate", refuse_c01):
			apply_sensitive_urgent_matrix.execute()
		self.assertEqual(len(self.rules("C01")), 0)
		self.assertEqual(len(self.rules("C17")), 1)
		self.assertEqual(frappe.db.get_value("Grievance Type", sensitive, "is_sensitive"), 1)

	def test_the_sensitive_flag_change_is_in_the_types_history(self):
		name = self.a_type("C13-02")
		# Frappe writes no Version while `in_test` is set; a migration's save does.
		with mock.patch.object(frappe, "in_test", False):
			apply_sensitive_urgent_matrix.execute()
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": "Grievance Type", "docname": name}))
