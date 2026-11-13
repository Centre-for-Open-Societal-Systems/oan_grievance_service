# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""One active category admin (L3) per category and department, and what the refusal says."""

import frappe

from oan_grievance_service.api.v1.officer import update_officer
from oan_grievance_service.tests.test_officer_roles import PASSWORD, RoleCase, create_officer

PREFIX = "catadmin-"


class TestCategoryAdminUniqueness(RoleCase):
	def _admin(self, number, department, categories, **extra):
		fields = {
			"full_name": f"Admin {number}",
			"designation": "Category Admin",
			"level": "L3",
			"department": department,
			"email": f"{PREFIX}{number}@example.com",
			"temporary_password": PASSWORD,
			**extra,
		}
		if categories is not None:
			fields["service_categories"] = categories
		return create_officer(**fields)

	def _first(self, department="S443", categories=None):
		"""The admin already on the desk. Their rows stay in the transaction for the next call."""
		first = self._admin(1, department, [self.inputs] if categories is None else categories)
		self.assertEqual(first["status"], "success", msg=first)
		frappe.clear_messages()
		return first["data"]["officer"]["name"]

	def _assert_refused(self, result, *needles):
		self.assertEqual(result["status"], "error", msg=result)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		message = result["message"]
		self.assertIn("Only one active category admin is allowed", message)
		for needle in needles:
			self.assertIn(needle, message)

	def test_same_category_in_the_same_department_is_refused_with_a_clear_message(self):
		first = self._first()
		result = self._admin(2, "S443", [self.inputs])
		self._assert_refused(
			result,
			f"{self.inputs} in department {self.department}",
			first,
			"Choose a different category or department",
		)

	def test_a_different_category_in_the_same_department_is_allowed(self):
		self._first()
		self.assertEqual(self._admin(2, "S443", [self.credit])["status"], "success")

	def test_the_same_category_in_another_department_is_allowed(self):
		self._first()
		self.assertEqual(self._admin(2, "S444", [self.inputs])["status"], "success")

	def test_a_category_with_no_department_is_its_own_scope(self):
		self._first(department=None)
		self.assertEqual(self._admin(2, "S443", [self.inputs])["status"], "success")
		frappe.clear_messages()

	def test_a_second_admin_with_no_department_is_refused(self):
		self._first(department=None)
		self._assert_refused(self._admin(2, None, [self.inputs]), f"{self.inputs} in no department")

	def test_every_taken_category_is_reported_at_once(self):
		taken_inputs = self._first()
		other = self._admin(2, "S443", [self.credit])
		self.assertEqual(other["status"], "success", msg=other)
		frappe.clear_messages()
		other_name = other["data"]["officer"]["name"]
		result = self._admin(3, "S443", [self.inputs, self.credit])
		self._assert_refused(result, self.inputs, taken_inputs, self.credit, other_name)

	def test_a_second_catch_all_admin_is_refused(self):
		self._first(categories=[])
		self._assert_refused(self._admin(2, "S443", None), f"All categories in department {self.department}")

	def test_a_catch_all_admin_may_sit_beside_a_category_admin(self):
		self._first()
		self.assertEqual(self._admin(2, "S443", None)["status"], "success")

	def test_an_inactive_admin_frees_the_desk(self):
		first = self._first()
		update_officer(first, status="Inactive")
		frappe.clear_messages()
		self.assertEqual(self._admin(2, "S443", [self.inputs])["status"], "success")

	def test_an_admin_on_leave_still_holds_the_desk_and_the_message_says_so(self):
		first = self._first()
		update_officer(first, status="On Leave")
		frappe.clear_messages()
		self._assert_refused(self._admin(2, "S443", [self.inputs]), f"{first} (on leave)")

	def test_editing_an_admin_onto_a_taken_category_is_refused(self):
		first = self._first()
		second = self._admin(2, "S443", [self.credit])
		self.assertEqual(second["status"], "success", msg=second)
		frappe.clear_messages()
		result = update_officer(
			second["data"]["officer"]["name"], service_categories=[self.credit, self.inputs]
		)
		self._assert_refused(result, self.inputs, first)

	def test_saving_an_admin_again_does_not_clash_with_themselves(self):
		first = self._first()
		result = update_officer(first, service_categories=[self.inputs], designation="Chief Admin")
		self.assertEqual(result["status"], "success", msg=result)
