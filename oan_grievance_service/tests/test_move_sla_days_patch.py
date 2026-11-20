"""The patch that moves each category's SLA days from its configuration to a base SLA rule (STG-514)."""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.patches import move_sla_days_to_base_rules as patch
from oan_grievance_service.services import classification
from oan_grievance_service.tests.test_classification import a_bare_category, a_category

RULE = "Grievance SLA Rule"


class TestMoveSlaDaysToBaseRules(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.savepoint = "move_sla_days_patch"
		frappe.db.savepoint(self.savepoint)

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)

	def a_legacy_configuration(self):
		"""A category with an active configuration from before the rules, and no base rule."""
		category = a_bare_category()
		config = frappe.new_doc("Grievance SLA Configuration")
		config.update({"service_category": category, "active": 1})
		config.set_new_name()
		config.db_insert()
		return category

	def run_patch(self, rows):
		"""Run the patch as if the configurations' old `sla_days` column held `rows`."""
		with mock.patch.object(patch, "_configurations", return_value=[frappe._dict(row) for row in rows]):
			patch.execute()

	def test_each_configuration_gets_a_base_rule_with_its_days(self):
		category = self.a_legacy_configuration()
		self.run_patch([{"service_category": category, "sla_days": 11}])
		base = frappe.get_doc(RULE, {"service_category": category, "is_base": 1})
		self.assertEqual((base.sla_days, base.is_urgent), (11, 0))
		self.assertFalse(base.priority)
		# Named like the seed's, so loading the seed afterwards adds no second copy.
		code = frappe.db.get_value("Grievance Service Category", category, "code")
		self.assertEqual(base.name, f"{code}-BASE")
		self.assertEqual(classification.sla_days_for(category), 11)

	def test_the_patch_is_safe_to_run_twice(self):
		category = self.a_legacy_configuration()
		rows = [{"service_category": category, "sla_days": 11}]
		self.run_patch(rows)
		self.run_patch(rows)
		self.assertEqual(frappe.db.count(RULE, {"service_category": category}), 1)

	def test_a_category_that_has_its_base_rule_keeps_it(self):
		category = a_category(sla_days=6)
		self.run_patch([{"service_category": category, "sla_days": 11}])
		self.assertEqual(classification.sla_days_for(category), 6)
		self.assertEqual(frappe.db.count(RULE, {"service_category": category}), 1)

	def test_the_newest_configuration_of_a_category_wins(self):
		category = self.a_legacy_configuration()
		self.run_patch(
			[{"service_category": category, "sla_days": 4}, {"service_category": category, "sla_days": 9}]
		)
		self.assertEqual(classification.sla_days_for(category), 4)

	def test_a_site_without_the_old_column_is_left_alone(self):
		with mock.patch.object(frappe.db, "has_column", return_value=False):
			self.assertEqual(patch._configurations(), [])
