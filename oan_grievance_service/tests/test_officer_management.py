# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-430: create, get, update and list L1 / L2 officer profiles."""

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.officer import (
	create_officer,
	get_officer,
	list_officers,
	update_officer,
)
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_role_level,
	_user,
)


class TestOfficerManagement(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		self.department = _department("STG430 Inputs Agency", "S430")
		self.other_department = _department("STG430 Credit Agency", "S431")
		self.category = _category("STG430 Inputs", "Z93")
		self.other_category = _category("STG430 Credit", "Z92")
		frappe.db.delete("Grievance Officer Profile", {"email": ["like", "stg430-%"]})
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _l1(self, email="stg430-l1@example.com", **extra):
		return create_officer(
			full_name="Tigist Alemu",
			designation="Nodal Officer",
			level="L1",
			department="S430",
			email=email,
			phone="+251911123456",
			service_categories=[self.category],
			**extra,
		)["data"]["officer"]

	def test_create_and_get_an_l1_officer(self):
		created = create_officer(
			full_name="Tigist Alemu",
			designation="Nodal Officer",
			level="L1",
			department="S430",
			email="STG430-L1@example.com",
			phone="+251911123456",
			service_categories=[self.category, self.category],
		)
		self.assertEqual(created["status"], "success")
		officer = created["data"]["officer"]
		self.assertEqual(officer["level"], "L1")
		self.assertEqual(officer["department"], self.department)
		self.assertEqual(officer["email"], "stg430-l1@example.com")
		self.assertEqual(officer["status"], "Active")
		self.assertEqual(officer["service_categories"], [self.category])
		self.assertIsNone(officer["reports_to"])

		fetched = get_officer(officer["name"])["data"]["officer"]
		self.assertEqual(fetched, officer)

	def test_l2_reports_to_an_l1(self):
		l1 = self._l1()
		l2 = create_officer(
			full_name="Yonas Mekonnen",
			designation="Senior Nodal Officer",
			level="L2",
			department=self.department,
			email="stg430-l2@example.com",
			reports_to=l1["name"],
		)["data"]["officer"]
		self.assertEqual(l2["reports_to"], l1["name"])
		self.assertEqual(l2["reports_to_name"], "Tigist Alemu")

	def test_reports_to_rules(self):
		l1 = self._l1()
		other_l1 = self._l1("stg430-l1b@example.com")
		l2 = create_officer(
			full_name="Yonas Mekonnen",
			designation="Senior Nodal Officer",
			level="L2",
			department=self.department,
			email="stg430-l2@example.com",
		)["data"]["officer"]
		with _keep_transaction():
			self.assertEqual(
				update_officer(l1["name"], reports_to=other_l1["name"])["code"], "VALIDATION_ERROR"
			)
			self.assertEqual(update_officer(l2["name"], reports_to=l2["name"])["code"], "VALIDATION_ERROR")
			self.assertEqual(update_officer(l2["name"], reports_to="OFF-99999")["code"], "NOT_FOUND")
		assigned = update_officer(l2["name"], reports_to=l1["name"])["data"]["officer"]
		self.assertEqual(assigned["reports_to"], l1["name"])
		self.assertIsNone(update_officer(l2["name"], reports_to=None)["data"]["officer"]["reports_to"])

	def test_partial_update_changes_only_what_was_sent(self):
		officer = self._l1()
		updated = update_officer(
			officer["name"],
			designation="Chief Nodal Officer",
			phone=None,
			service_categories=[self.other_category],
		)["data"]["officer"]
		self.assertEqual(updated["designation"], "Chief Nodal Officer")
		self.assertIsNone(updated["phone"])
		self.assertEqual(updated["service_categories"], [self.other_category])
		self.assertEqual(updated["full_name"], "Tigist Alemu")
		self.assertEqual(updated["email"], officer["email"])

	def test_deactivate_by_status(self):
		officer = self._l1()
		for status in ("On Leave", "Inactive", "Active"):
			result = update_officer(officer["name"], status=status)["data"]["officer"]
			self.assertEqual(result["status"], status)
		self.assertTrue(frappe.db.exists("Grievance Officer Profile", officer["name"]))

	def test_level_is_fixed_and_unknown_fields_are_rejected(self):
		officer = self._l1()
		with _keep_transaction():
			for field, value in (("level", "L2"), ("nonsense", 1)):
				result = update_officer(officer["name"], **{field: value})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])

	def test_duplicate_email_is_rejected(self):
		first = self._l1()
		other = self._l1("stg430-other@example.com")
		with _keep_transaction():
			self.assertEqual(
				create_officer(
					full_name="Dup",
					designation="Nodal Officer",
					level="L1",
					department="S430",
					email=first["email"].upper(),
				)["code"],
				"VALIDATION_ERROR",
			)
			self.assertEqual(update_officer(other["name"], email=first["email"])["code"], "VALIDATION_ERROR")

	def test_invalid_input_is_rejected(self):
		base = {"full_name": "X", "designation": "Y", "level": "L1", "department": "S430"}
		bad = [
			{"department": "Nope", "email": "stg430-x@example.com"},
			{"email": "stg430-x@example.com", "region": "Nowhere"},
			{"email": "stg430-x@example.com", "service_categories": ["Nope"]},
			{"email": "not-an-email"},
			{"email": "stg430-x@example.com", "phone": "abc"},
			{"email": "stg430-x@example.com", "level": "L3"},
			{"email": "stg430-x@example.com", "status": "Gone"},
			{"email": "stg430-x@example.com", "reports_to": self._l1()["name"]},
		]
		with _keep_transaction():
			for extra in bad:
				result = create_officer(**{**base, **extra})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(extra, result))
			self.assertEqual(
				create_officer(
					**{k: v for k, v in base.items() if k != "full_name"}, email="stg430-x@example.com"
				)["code"],
				"VALIDATION_ERROR",
			)

	def test_inactive_department_is_rejected_for_a_new_officer(self):
		frappe.db.set_value("Grievance Department", self.department, "active", 0)
		with _keep_transaction():
			result = create_officer(
				full_name="X",
				designation="Y",
				level="L1",
				department="S430",
				email="stg430-x@example.com",
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR")

	def test_missing_officer_is_not_found(self):
		with _keep_transaction():
			for result in (get_officer("OFF-99999"), update_officer("OFF-99999", status="Inactive")):
				self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	def test_empty_update_is_rejected(self):
		officer = self._l1()
		with _keep_transaction():
			self.assertEqual(update_officer(officer["name"])["code"], "VALIDATION_ERROR")

	def test_list_filters_by_level_department_and_status(self):
		l1 = self._l1()
		l2 = create_officer(
			full_name="Yonas Mekonnen",
			designation="Senior Nodal Officer",
			level="L2",
			department="S431",
			email="stg430-l2@example.com",
			status="On Leave",
		)["data"]["officer"]

		def ids(**filters):
			data = list_officers(page_size=100, **filters)["data"]
			return {row["name"] for row in data["officers"]}

		self.assertLessEqual({l1["name"], l2["name"]}, ids())
		self.assertIn(l1["name"], ids(level="L1"))
		self.assertNotIn(l2["name"], ids(level="L1"))
		self.assertIn(l2["name"], ids(department="S431"))
		self.assertNotIn(l1["name"], ids(department="S431"))
		self.assertIn(l2["name"], ids(status="On Leave"))
		self.assertNotIn(l1["name"], ids(status="On Leave"))
		self.assertEqual(ids(level="L1", department="S430", status="Active", q="stg430-l1"), {l1["name"]})

	def test_list_paginates(self):
		for index in range(3):
			self._l1(f"stg430-p{index}@example.com")
		data = list_officers(q="stg430-p", page=2, page_size=2)["data"]
		self.assertEqual(len(data["officers"]), 1)
		self.assertEqual(data["pagination"]["total_count"], 3)
		self.assertEqual(data["pagination"]["total_pages"], 2)

	def test_bad_filter_values_are_validation_errors(self):
		with _keep_transaction():
			for bad in (
				{"level": "L3"},
				{"status": "Gone"},
				{"page_size": 0},
				{"bogus": 1},
				{"department": "Nope"},
			):
				self.assertEqual(list_officers(**bad)["code"], "VALIDATION_ERROR", msg=bad)

	def test_only_admins_can_manage_officers(self):
		officer = self._l1()
		plain = _user("stg430-plain@example.com", "Plain Officer")
		admin = _user("stg430-admin@example.com", "Desk Admin", role="Grievance Admin")
		frappe.set_user(plain)
		with _keep_transaction():
			for result in (
				list_officers(),
				get_officer(officer["name"]),
				update_officer(officer["name"], status="Inactive"),
				create_officer(full_name="X", designation="Y", level="L1", department="S430", email="a@b.co"),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
			frappe.set_user("Guest")
			self.assertEqual(list_officers()["code"], "PERMISSION_DENIED")
		frappe.set_user(admin)
		self.assertEqual(list_officers()["status"], "success")
