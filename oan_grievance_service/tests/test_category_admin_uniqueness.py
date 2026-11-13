# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""One active category admin (L3) per category and department, and what the refusal says."""

import frappe

from oan_grievance_service.api.v1.officer import update_officer
from oan_grievance_service.services import category_assignment
from oan_grievance_service.services.officer import MAX_CONFLICTS_SHOWN
from oan_grievance_service.tests.test_category_assignment import _category
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

	def _created(self, number, department, categories):
		"""An admin who must be created, so a refusal here is a broken test and not the rule."""
		result = self._admin(number, department, categories)
		self.assertEqual(result["status"], "success", msg=result)
		frappe.clear_messages()
		return result["data"]["officer"]["name"]

	def _first(self, department="S443", categories=None):
		"""The admin already on the desk. Their rows stay in the transaction for the next call."""
		return self._created(1, department, [self.inputs] if categories is None else categories)

	def _set_status(self, name, status):
		result = update_officer(name, status=status)
		self.assertEqual(result["status"], "success", msg=result)
		frappe.clear_messages()

	def _assert_refused(self, result, *needles):
		self.assertEqual(result["status"], "error", msg=result)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		message = result["message"]
		self.assertIn("Only one active category admin is allowed", message)
		for needle in needles:
			self.assertIn(needle, message)
		return message

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

	def test_a_second_admin_with_no_department_is_refused(self):
		self._first(department=None)
		self._assert_refused(self._admin(2, None, [self.inputs]), f"{self.inputs} in no department")

	def test_every_taken_category_is_reported_at_once(self):
		taken_inputs = self._first()
		other_name = self._created(2, "S443", [self.credit])
		result = self._admin(3, "S443", [self.inputs, self.credit])
		self._assert_refused(result, self.inputs, taken_inputs, self.credit, other_name)

	def test_taken_categories_are_listed_by_category_whatever_order_they_were_asked_for(self):
		self._created(1, "S443", [self.inputs, self.credit])
		message = self._assert_refused(self._admin(2, "S443", [self.inputs, self.credit]))
		self.assertLess(message.index(self.credit), message.index(self.inputs))

	def test_a_long_list_of_taken_categories_is_cut_short_and_counted(self):
		extra = 2
		categories = [
			_category(f"STG-cap-{number}", f"W{number:02d}") for number in range(MAX_CONFLICTS_SHOWN + extra)
		]
		for category in categories:
			category_assignment.create(
				service_category=category,
				department=self.department,
				l1_officer=self.l1_seed,
				l2_officer=self.l2_seed,
				sla_days=7,
				auto_escalate=True,
				active=True,
			)
		self._created(1, "S443", categories)
		message = self._assert_refused(self._admin(2, "S443", categories))
		self.assertEqual(message.count(" in department "), MAX_CONFLICTS_SHOWN)
		self.assertIn(f"and {extra} more", message)

	def test_a_second_catch_all_admin_is_refused(self):
		self._first(categories=[])
		self._assert_refused(self._admin(2, "S443", None), f"All categories in department {self.department}")

	def test_a_catch_all_admin_may_sit_beside_a_category_admin(self):
		self._first()
		self.assertEqual(self._admin(2, "S443", None)["status"], "success")

	def test_an_inactive_admin_frees_the_desk(self):
		first = self._first()
		self._set_status(first, "Inactive")
		self.assertEqual(self._admin(2, "S443", [self.inputs])["status"], "success")

	def test_an_admin_on_leave_still_holds_the_desk_and_the_message_says_so(self):
		first = self._first()
		self._set_status(first, "On Leave")
		self._assert_refused(self._admin(2, "S443", [self.inputs]), f"{first} (on leave)")

	def test_reactivating_an_admin_whose_desk_was_taken_meanwhile_is_refused(self):
		first = self._first()
		self._set_status(first, "Inactive")
		replacement = self._created(2, "S443", [self.inputs])
		result = update_officer(first, status="Active")
		self._assert_refused(result, self.inputs, replacement)

	def test_returning_from_inactive_to_on_leave_also_takes_the_desk_back(self):
		first = self._first()
		self._set_status(first, "Inactive")
		replacement = self._created(2, "S443", [self.inputs])
		result = update_officer(first, status="On Leave")
		self._assert_refused(result, self.inputs, replacement)

	def test_reactivating_an_admin_whose_desk_is_still_free_is_allowed(self):
		first = self._first()
		self._set_status(first, "Inactive")
		result = update_officer(first, status="Active")
		self.assertEqual(result["status"], "success", msg=result)

	def test_going_on_leave_and_back_never_clashes_with_the_admin_who_kept_the_desk(self):
		first = self._first()
		self._set_status(first, "On Leave")
		result = update_officer(first, status="Active")
		self.assertEqual(result["status"], "success", msg=result)

	def test_editing_an_admin_onto_a_taken_category_is_refused(self):
		first = self._first()
		second = self._created(2, "S443", [self.credit])
		result = update_officer(second, service_categories=[self.credit, self.inputs])
		self._assert_refused(result, self.inputs, first)

	def test_moving_an_admin_to_a_department_where_their_category_is_taken_is_refused(self):
		first = self._first()
		second = self._created(2, "S444", [self.inputs])
		result = update_officer(second, department="S443")
		self._assert_refused(result, f"{self.inputs} in department {self.department}", first)

	def test_moving_an_admin_to_a_department_where_their_category_is_free_is_allowed(self):
		self._first()
		second = self._created(2, "S443", [self.credit])
		result = update_officer(second, department="S444")
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["officer"]["department"], self.other_department)

	def test_saving_an_admin_again_does_not_clash_with_themselves(self):
		first = self._first()
		result = update_officer(first, service_categories=[self.inputs], designation="Chief Admin")
		self.assertEqual(result["status"], "success", msg=result)
