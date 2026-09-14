import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.services import sla


class TestGrievanceSLAConfiguration(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()
		self.cat_name = "Inputs"
		if not frappe.db.exists("Grievance Service Category", self.cat_name):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": self.cat_name,
					"code": "INPT",
					"sort_order": 1,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		# Clean up existing test SLA configs for this category
		for name in frappe.get_all(
			"Grievance SLA Configuration", filters={"service_category": self.cat_name}, pluck="name"
		):
			frappe.delete_doc("Grievance SLA Configuration", name, force=True)

	def tearDown(self):
		frappe.db.rollback()

	def test_sla_configuration_with_custom_milestone_durations(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"sla_days": 10,
				"first_response_hours": 8,
				"update_cadence_hours": 72,
				"remand_execution_hours": 36,
				"appeal_window_days": 20,
				"active": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.first_response_hours, 8)
		self.assertEqual(doc.update_cadence_hours, 72)
		self.assertEqual(doc.remand_execution_hours, 36)
		self.assertEqual(doc.appeal_window_days, 20)

		policy = sla.resolve_policy(self.cat_name)
		self.assertIsNotNone(policy)
		self.assertEqual(policy.first_response_hours, 8)
		self.assertEqual(policy.update_cadence_hours, 72)
		self.assertEqual(policy.remand_execution_hours, 36)
		self.assertEqual(policy.appeal_window_days, 20)

	def test_sla_configuration_defaults(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"sla_days": 5,
				"active": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.first_response_hours, 4)
		self.assertEqual(doc.update_cadence_hours, 48)
		self.assertEqual(doc.remand_execution_hours, 24)
		self.assertEqual(doc.appeal_window_days, 15)

	def test_sla_configuration_negative_validation(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"sla_days": 0,
				"active": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, doc.insert)

		doc_neg = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_name,
				"sla_days": 5,
				"first_response_hours": -1,
				"active": 1,
			}
		)
		self.assertRaises(frappe.ValidationError, doc_neg.insert)
