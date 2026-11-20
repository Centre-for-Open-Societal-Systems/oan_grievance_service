import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.services import classification, sla


class TestGrievanceSLAConfiguration(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()
		self.cat_name = "Inputs"
		if not frappe.db.exists("Grievance Service Category", self.cat_name):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": self.cat_name,
					"code": "001",
					"sort_order": 1,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		# Clean up existing test SLA configs for this category
		for name in frappe.get_all(
			"Grievance SLA Configuration", filters={"service_category": self.cat_name}, pluck="name"
		):
			frappe.delete_doc("Grievance SLA Configuration", name, force=True)
		# The SLA days live on the category's base SLA rule, which a configuration needs.
		classification.set_base_days(self.cat_name, 10)

	def tearDown(self):
		frappe.db.rollback()

	def test_sla_configuration_with_custom_milestone_durations(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"first_response_hours": 8,
				"update_cadence_hours": 72,
				"remand_execution_hours": 36,
				"active": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.first_response_hours, 8)
		self.assertEqual(doc.update_cadence_hours, 72)
		self.assertEqual(doc.remand_execution_hours, 36)

		policy = sla.resolve_policy(self.cat_name)
		self.assertIsNotNone(policy)
		self.assertEqual(policy.sla_days, 10)
		self.assertEqual(policy.first_response_hours, 8)
		self.assertEqual(policy.update_cadence_hours, 72)
		self.assertEqual(policy.remand_execution_hours, 36)

	def test_sla_configuration_defaults(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"active": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.first_response_hours, 4)
		self.assertEqual(doc.update_cadence_hours, 48)
		self.assertEqual(doc.remand_execution_hours, 24)

	def test_sla_configuration_negative_validation(self):
		# Zero SLA days are refused on the base rule, where the days are kept.
		self.assertRaises(frappe.ValidationError, classification.set_base_days, self.cat_name, 0)

		doc_neg = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"first_response_hours": -1,
				"active": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, doc_neg.insert)
