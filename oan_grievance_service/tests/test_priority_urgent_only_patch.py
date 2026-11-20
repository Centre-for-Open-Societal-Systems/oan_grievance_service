"""The patch that keeps one priority, Urgent, on SLA rules and cases (STG-514)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.patches import drop_normal_and_high_priority as patch
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_grievance
from oan_grievance_service.tests.test_classification import DRY, a_category, a_rule, a_type, add_rules

RULE = "Grievance SLA Rule"


class TestPriorityUrgentOnlyPatch(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.savepoint = "priority_urgent_only_patch"
		frappe.db.savepoint(self.savepoint)

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)

	def a_case(self, grievance_type):
		category = frappe.db.get_value("Grievance Type", grievance_type, "service_category")
		return a_grievance(grievance_type=grievance_type, service_category=category)

	def old_value(self, doctype, name, priority):
		"""Write a priority from before the change, as an old site holds it (no validation runs)."""
		frappe.db.set_value(doctype, name, "priority", priority, update_modified=False)

	def test_rules_keep_urgent_and_lose_normal_and_high(self):
		category = a_category()
		urgent, high = add_rules(category, [a_rule(DRY, urgent=True), a_rule(((5, 1), (6, 30)))])
		base = frappe.db.get_value(RULE, {"service_category": category, "is_base": 1})
		self.old_value(RULE, base, "Normal")
		self.old_value(RULE, high, "High")
		self.old_value(RULE, urgent, "Normal")

		patch.execute()

		self.assertFalse(frappe.db.get_value(RULE, base, "priority"))
		self.assertFalse(frappe.db.get_value(RULE, high, "priority"))
		# An urgent rule's priority is Urgent, whatever it held.
		self.assertEqual(frappe.db.get_value(RULE, urgent, "priority"), C.PRIORITY_URGENT)

	def test_cases_follow_their_handling_class(self):
		kind = a_type(a_category())
		standard, urgent = self.a_case(kind), self.a_case(kind)
		frappe.db.set_value("Grievance", urgent.name, "handling_class", C.HANDLING_URGENT)
		self.old_value("Grievance", standard.name, "High")
		self.old_value("Grievance", urgent.name, "Normal")

		patch.execute()

		self.assertFalse(frappe.db.get_value("Grievance", standard.name, "priority"))
		self.assertEqual(frappe.db.get_value("Grievance", urgent.name, "priority"), C.PRIORITY_URGENT)
		# The case keeps its class.
		self.assertEqual(frappe.db.get_value("Grievance", urgent.name, "handling_class"), C.HANDLING_URGENT)

	def test_a_case_patched_to_blank_saves_again(self):
		case = self.a_case(a_type(a_category()))
		self.old_value("Grievance", case.name, "Normal")
		patch.execute()
		case.reload()
		case.closure_reason = "Saved after the priority change."
		case.save(ignore_permissions=True)
		self.assertFalse(case.priority)

	def test_the_patch_is_safe_to_run_twice(self):
		category = a_category()
		(urgent,) = add_rules(category, [a_rule(DRY, urgent=True)])
		patch.execute()
		patch.execute()
		self.assertEqual(frappe.db.get_value(RULE, urgent, "priority"), C.PRIORITY_URGENT)
		self.assertEqual(
			frappe.db.count(RULE, {"service_category": category, "priority": ["in", ["Normal", "High"]]}), 0
		)
