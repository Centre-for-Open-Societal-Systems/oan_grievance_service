# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from contextlib import contextmanager

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.category_assignment import (
	create_assignment,
	deactivate_assignment,
	get_assignment,
	list_assignments,
	update_assignment,
)


class TestGrievanceCategoryAssignment(FrappeTestCase):
	def setUp(self):
		self.l1 = _user("stg404-l1@example.com", "Tigist Alemu")
		self.l2 = _user("stg404-l2@example.com", "Yonas Mekonnen")
		self.category = _category("STG404 Inputs", "Z94")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		self.department = _department("STG404 Inputs Agency", "S404")
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"Grievance RBAC Assignment", filters={"category_scope": self.category}, pluck="name"
		):
			frappe.delete_doc("Grievance RBAC Assignment", name, force=1, ignore_permissions=True)
		frappe.clear_messages()

	def test_crud_projects_desk_and_sla(self):
		created = create_assignment(
			service_category=self.category,
			department="S404",
			l1_officer=self.l1,
			l2_officer=self.l2,
			sla_days=14,
			auto_escalate=True,
		)
		self.assertEqual(created["status"], "success")
		assignment = created["data"]["assignment"]
		self.assertEqual(assignment["service_category"], self.category)
		self.assertEqual(assignment["department"], self.department)
		self.assertEqual(assignment["sla_days"], 14)
		self.assertTrue(assignment["auto_escalate"])
		self.assertEqual(assignment["l1_officer_name"], "Tigist Alemu")
		self.assertNotIn("rbac_assignment", assignment)
		self.assertNotIn("priority", assignment)
		self.assertNotIn("notify_on_submit", assignment)

		desk = frappe.get_doc("Grievance RBAC Assignment", assignment["name"])
		self.assertEqual(desk.category_scope, self.category)
		self.assertEqual(desk.department_scope, self.department)
		self.assertEqual(desk.active, 1)
		self.assertEqual(desk.routing_strategy, "Primary First")
		self.assertEqual(assignment["l1_role_level"], "nodal_officer")
		self.assertEqual(assignment["routing_strategy"], "Primary First")
		officers = {row.role_level: row for row in desk.officers}
		self.assertEqual(officers["nodal_officer"].user, self.l1)
		self.assertEqual(officers["nodal_officer"].is_primary, 1)
		self.assertEqual(officers["senior_nodal_officer"].user, self.l2)

		sla_days, auto_escalate = frappe.db.get_value(
			"Grievance SLA Configuration",
			{"service_category": self.category},
			["sla_days", "auto_escalate"],
		)
		self.assertEqual(sla_days, 14)
		self.assertEqual(auto_escalate, 1)

		updated = update_assignment(
			assignment["name"],
			sla_days=10,
			l2_officer=None,
		)
		self.assertEqual(updated["status"], "success")
		self.assertEqual(updated["data"]["assignment"]["sla_days"], 10)
		self.assertIsNone(updated["data"]["assignment"]["l2_officer"])
		desk.reload()
		self.assertEqual([row.user for row in desk.officers], [self.l1])

		listed = list_assignments(service_category=self.category)
		self.assertEqual(listed["pagination"]["total_count"], 1)
		self.assertEqual(listed["data"]["assignments"][0]["name"], assignment["name"])

		fetched = get_assignment(assignment["name"])
		self.assertEqual(fetched["data"]["assignment"]["sla_days"], 10)

		deactivated = deactivate_assignment(assignment["name"])
		self.assertEqual(deactivated["status"], "success")
		self.assertFalse(deactivated["data"]["assignment"]["active"])
		self.assertEqual(frappe.db.get_value("Grievance RBAC Assignment", assignment["name"], "active"), 0)
		self.assertEqual(
			frappe.db.get_value("Grievance SLA Configuration", {"service_category": self.category}, "active"),
			1,
		)

		again = deactivate_assignment(assignment["name"])
		self.assertFalse(again["data"]["assignment"]["active"])

	def test_desk_uses_the_departments_role_levels_and_routing_strategy(self):
		_role_level("desk_primary", 110)
		_role_level("desk_senior", 120)
		department = _department(
			"STG404 Routing Agency",
			"S404R",
			l1_role_level="desk_primary",
			l2_role_level="desk_senior",
			routing_strategy="Round Robin",
		)
		category = _category("STG404 Routing", "Z9R")
		created = create_assignment(
			service_category=category,
			department=department,
			l1_officer=self.l1,
			l2_officer=self.l2,
			sla_days=6,
		)
		self.assertEqual(created["status"], "success", msg=created)
		assignment = created["data"]["assignment"]
		self.assertEqual(assignment["l1_role_level"], "desk_primary")
		self.assertEqual(assignment["l2_role_level"], "desk_senior")
		self.assertEqual(assignment["routing_strategy"], "Round Robin")
		desk = frappe.get_doc("Grievance RBAC Assignment", assignment["name"])
		self.assertEqual(desk.routing_strategy, "Round Robin")
		officers = {row.role_level: row.user for row in desk.officers}
		self.assertEqual(officers["desk_primary"], self.l1)
		self.assertEqual(officers["desk_senior"], self.l2)

	def test_department_without_l1_role_level_is_rejected(self):
		department = _department(
			"STG404 No Level Agency",
			"S404N",
			l1_role_level=None,
			l2_role_level=None,
			routing_strategy=None,
		)
		result = create_assignment(
			service_category=self.category,
			department=department,
			l1_officer=self.l1,
			sla_days=5,
		)
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertIn("L1 role level", result["message"])

	def test_duplicate_category_is_rejected(self):
		create_assignment(
			service_category=self.category,
			department=self.department,
			l1_officer=self.l1,
			sla_days=7,
		)
		duplicate = create_assignment(
			service_category=self.category,
			department=self.department,
			l1_officer=self.l1,
			sla_days=8,
		)
		self.assertEqual(duplicate["code"], "DUPLICATE_ENTRY")

	def test_category_can_have_one_assignment_per_department(self):
		other = _department("STG404 Second Agency", "S404B")
		first = self._assignment()
		second = self._assignment(department=other)
		self.assertNotEqual(first, second)
		listed = list_assignments(service_category=self.category)
		self.assertEqual(listed["pagination"]["total_count"], 2)
		self.assertEqual(
			sorted(row["department"] for row in listed["data"]["assignments"]),
			sorted([self.department, other]),
		)
		with _keep_transaction():
			moved = update_assignment(second, department=self.department)
		self.assertEqual(moved["code"], "DUPLICATE_ENTRY")

	def test_deactivate_leaves_the_shared_sla_row_alone(self):
		other = _department("STG404 Shared SLA Agency", "S404C")
		first = self._assignment()
		second = self._assignment(department=other)
		deactivate_assignment(first)
		self.assertTrue(frappe.db.get_value("Grievance RBAC Assignment", second, "active"))
		self.assertEqual(
			frappe.db.get_value("Grievance SLA Configuration", {"service_category": self.category}, "active"),
			1,
		)

	def test_patch_active_false_matches_delete(self):
		name = self._assignment()
		patched = update_assignment(name, active=False)
		self.assertFalse(patched["data"]["assignment"]["active"])
		self.assertEqual(deactivate_assignment(name)["data"]["assignment"], patched["data"]["assignment"])
		revived = update_assignment(name, active=True)
		self.assertTrue(revived["data"]["assignment"]["active"])

	def test_update_keeps_officer_rows_that_do_not_change(self):
		created = create_assignment(
			service_category=self.category,
			department=self.department,
			l1_officer=self.l1,
			l2_officer=self.l2,
			sla_days=7,
		)
		name = created["data"]["assignment"]["name"]
		before = {row.user: row.name for row in frappe.get_doc("Grievance RBAC Assignment", name).officers}
		update_assignment(name, sla_days=9)
		after = {row.user: row.name for row in frappe.get_doc("Grievance RBAC Assignment", name).officers}
		self.assertEqual(before, after)

	def test_unknown_and_immutable_fields_are_rejected(self):
		name = self._assignment()
		with _keep_transaction():
			for field in ("priority", "notify_on_submit", "service_category"):
				result = update_assignment(name, **{field: "Low"})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])
			created = create_assignment(
				service_category=self.category,
				department=self.department,
				l1_officer=self.l1,
				sla_days=5,
				priority="Low",
			)
			self.assertEqual(created["code"], "VALIDATION_ERROR")
			self.assertIn("priority", created["details"])

	def test_category_assignment_is_the_rbac_desk(self):
		created = create_assignment(
			service_category=self.category,
			department=self.department,
			l1_officer=self.l1,
			sla_days=5,
		)
		self.assertEqual(created["status"], "success", msg=created)
		assignment = created["data"]["assignment"]
		self.assertTrue(frappe.db.exists("Grievance RBAC Assignment", assignment["name"]))
		self.assertFalse(assignment["name"].startswith("GR-CAT"))

	def test_missing_assignment_is_not_found(self):
		for result in (
			get_assignment("GR-CAT-DOES-NOT-EXIST"),
			update_assignment("GR-CAT-DOES-NOT-EXIST", sla_days=3),
			deactivate_assignment("GR-CAT-DOES-NOT-EXIST"),
		):
			self.assertEqual(result["code"], "NOT_FOUND")

	def test_null_department_and_l1_are_rejected(self):
		name = self._assignment()
		with _keep_transaction():
			for field, value in (
				("department", None),
				("department", ""),
				("department", " "),
				("l1_officer", None),
				("l1_officer", ""),
			):
				result = update_assignment(name, **{field: value})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])
		stored = frappe.db.get_value(
			"Grievance RBAC Assignment",
			name,
			["department_scope", "category_scope"],
			as_dict=True,
		)
		l1 = frappe.db.get_value(
			"Grievance RBAC Assignment Officer", {"parent": name, "is_primary": 1}, "user"
		)
		self.assertEqual(stored.department_scope, self.department)
		self.assertEqual(l1, self.l1)
		self.assertEqual(stored.category_scope, self.category)

	def test_sla_days_must_be_at_least_one(self):
		with _keep_transaction():
			for days in (0, -1):
				result = create_assignment(
					service_category=self.category,
					department=self.department,
					l1_officer=self.l1,
					sla_days=days,
				)
				self.assertEqual(result["code"], "VALIDATION_ERROR")
				self.assertIn("sla_days", result["details"])
			name = self._assignment()
			updated = update_assignment(name, sla_days=0)
			self.assertEqual(updated["code"], "VALIDATION_ERROR")
			self.assertIn("sla_days", updated["details"])

	def test_page_and_filter_bounds(self):
		with _keep_transaction():
			self.assertEqual(list_assignments(page=0)["code"], "VALIDATION_ERROR")
			self.assertEqual(list_assignments(page_size=0)["code"], "VALIDATION_ERROR")
			self.assertEqual(list_assignments(page_size=101)["code"], "VALIDATION_ERROR")
			self.assertEqual(list_assignments(page="nope")["code"], "VALIDATION_ERROR")
			self.assertEqual(list_assignments(active="maybe")["code"], "VALIDATION_ERROR")

	def test_unknown_links_and_same_officers_are_rejected(self):
		with _keep_transaction():
			missing_category = create_assignment(
				service_category="Missing Category",
				department=self.department,
				l1_officer=self.l1,
				sla_days=5,
			)
			self.assertEqual(missing_category["code"], "VALIDATION_ERROR")
			missing_department = create_assignment(
				service_category=self.category,
				department="Missing Department",
				l1_officer=self.l1,
				sla_days=5,
			)
			self.assertEqual(missing_department["code"], "VALIDATION_ERROR")
			missing_officer = create_assignment(
				service_category=self.category,
				department=self.department,
				l1_officer="nobody@example.com",
				sla_days=5,
			)
			self.assertEqual(missing_officer["code"], "VALIDATION_ERROR")
			same_officer = create_assignment(
				service_category=self.category,
				department=self.department,
				l1_officer=self.l1,
				l2_officer=self.l1,
				sla_days=5,
			)
			self.assertEqual(same_officer["code"], "VALIDATION_ERROR")
			self.assertIn("different", same_officer["message"])

	def test_inactive_department_and_disabled_officer_are_rejected(self):
		inactive = _department("STG404 Inactive Agency", "S404I", active=0)
		disabled = _user("stg404-disabled@example.com", "Disabled Officer", enabled=0)
		with _keep_transaction():
			inactive_result = create_assignment(
				service_category=self.category,
				department=inactive,
				l1_officer=self.l1,
				sla_days=5,
			)
			self.assertEqual(inactive_result["code"], "VALIDATION_ERROR")
			self.assertIn("inactive", inactive_result["message"])
			disabled_result = create_assignment(
				service_category=self.category,
				department=self.department,
				l1_officer=disabled,
				sla_days=5,
			)
			self.assertEqual(disabled_result["code"], "VALIDATION_ERROR")
			self.assertIn("disabled", disabled_result["message"])

	def test_empty_update_is_rejected(self):
		name = self._assignment()
		with _keep_transaction():
			empty = update_assignment(name)
		self.assertEqual(empty["code"], "VALIDATION_ERROR")
		self.assertIn("No fields", empty["message"])

	def test_list_order_is_stable_when_modified_ties(self):
		department = _department("STG404 Order Agency", "S404O")
		first_category = _category("STG404 Order A", "Z9A")
		second_category = _category("STG404 Order B", "Z9B")
		first = self._assignment(category=first_category, department=department)
		second = self._assignment(category=second_category, department=department)
		stamp = "2026-01-01 00:00:00"
		for name in (first, second):
			frappe.db.set_value(
				"Grievance RBAC Assignment",
				name,
				"modified",
				stamp,
				update_modified=False,
			)
		listed = list_assignments(department=department, page_size=10)
		names = [row["name"] for row in listed["data"]["assignments"]]
		self.assertEqual(names, sorted([first, second], reverse=True))
		page_one = list_assignments(department=department, page=1, page_size=1)
		page_two = list_assignments(department=department, page=2, page_size=1)
		self.assertEqual(page_one["data"]["assignments"][0]["name"], names[0])
		self.assertEqual(page_two["data"]["assignments"][0]["name"], names[1])
		self.assertNotEqual(names[0], names[1])

	def test_officer_cannot_manage_assignments(self):
		name = self._assignment()
		frappe.set_user(self.l1)
		with _keep_transaction():
			for result in (
				list_assignments(),
				get_assignment(name),
				create_assignment(
					service_category=self.category,
					department=self.department,
					l1_officer=self.l1,
					sla_days=6,
				),
				update_assignment(name, sla_days=9),
				deactivate_assignment(name),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_grievance_admin_can_list(self):
		self._assignment()
		admin = _user("stg404-admin@example.com", "Selam Admin", role="Grievance Admin")
		frappe.set_user(admin)
		result = list_assignments(service_category=self.category)
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["pagination"]["total_count"], 1)

	def test_guest_cannot_manage_assignments(self):
		name = self._assignment()
		frappe.set_user("Guest")
		with _keep_transaction():
			for result in (
				list_assignments(),
				get_assignment(name),
				create_assignment(
					service_category=self.category,
					department=self.department,
					l1_officer=self.l1,
					sla_days=6,
				),
				update_assignment(name, sla_days=9),
				deactivate_assignment(name),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_non_admin_with_a_bad_body_gets_403_not_400(self):
		name = self._assignment()
		frappe.set_user(self.l1)
		with _keep_transaction():
			for result in (
				create_assignment(service_category=self.category, priority="Low"),
				update_assignment(name, priority="Low"),
				get_assignment(" "),
				deactivate_assignment(" "),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_blank_assignment_id_is_a_validation_error(self):
		with _keep_transaction():
			for result in (get_assignment(" "), deactivate_assignment(" ")):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn("assignment", result["details"])

	def test_desk_validation_applies_outside_the_api(self):
		def desk(category=None, officer=None, department=None):
			doc = frappe.new_doc("Grievance RBAC Assignment")
			doc.department_scope = department or self.department
			doc.category_scope = category or self.category
			doc.effective_from = frappe.utils.today()
			doc.append(
				"officers",
				{"user": officer or self.l1, "role_level": "nodal_officer", "is_primary": 1, "active": 1},
			)
			return doc

		disabled = _user("stg404-direct-disabled@example.com", "Direct Disabled", enabled=0)
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			desk(officer=disabled).insert()
		plain = _user("stg404-direct-plain@example.com", "Direct Plain", role="Guest")
		with self.assertRaisesRegex(frappe.ValidationError, "Grievance Officer"):
			desk(officer=plain).insert()
		inactive = _department("STG404 Direct Inactive", "S404X", active=0)
		with self.assertRaisesRegex(frappe.ValidationError, "inactive"):
			desk(department=inactive).insert()

		desk().insert()
		with self.assertRaises(frappe.DuplicateEntryError):
			desk().insert()

		repeated = desk(category=_category("STG404 Repeat", "Z9X"))
		repeated.append(
			"officers",
			{"user": self.l1, "role_level": "senior_nodal_officer", "is_primary": 0, "active": 1},
		)
		with self.assertRaisesRegex(frappe.ValidationError, "more than once"):
			repeated.insert()

	def test_area_aware_desks_skip_category_only_checks(self):
		area_desk = frappe.new_doc("Grievance RBAC Assignment")
		area_desk.department_scope = self.department
		area_desk.category_scope = self.category
		area_desk.effective_from = frappe.utils.today()
		area_desk.service_provider_scope = "Provider X"
		self.assertFalse(area_desk.is_category_only())
		area_desk.insert()
		self._assignment()

	def _assignment(self, category=None, department=None):
		created = create_assignment(
			service_category=category or self.category,
			department=department or self.department,
			l1_officer=self.l1,
			sla_days=7,
		)
		self.assertEqual(created["status"], "success", msg=created)
		return created["data"]["assignment"]["name"]


@contextmanager
def _keep_transaction():
	"""API errors call frappe.db.rollback(), which would erase this test's fixtures."""
	original = frappe.db.rollback
	frappe.db.rollback = lambda *args, **kwargs: None
	try:
		yield
	finally:
		frappe.db.rollback = original


def _user(email, full_name, role="Grievance Officer", enabled=1):
	if frappe.db.exists("User", email):
		frappe.db.set_value("User", email, "enabled", enabled, update_modified=False)
		return email
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"enabled": enabled,
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	).insert(ignore_permissions=True)
	return email


def _category(name, code):
	if frappe.db.exists("Grievance Service Category", name):
		return name
	frappe.get_doc(
		{
			"doctype": "Grievance Service Category",
			"category_name": name,
			"code": code,
			"is_active": 1,
		}
	).insert(ignore_permissions=True)
	return name


def _role_level(level, order):
	if frappe.db.exists("Grievance Role Level", level):
		return level
	frappe.get_doc(
		{
			"doctype": "Grievance Role Level",
			"level_code": level,
			"level_name": level,
			"level_order": order,
			"is_active": 1,
		}
	).insert(ignore_permissions=True)
	return level


def _department(
	name,
	short_name,
	active=1,
	l1_role_level="nodal_officer",
	l2_role_level="senior_nodal_officer",
	routing_strategy="Primary First",
):
	values = {
		"active": active,
		"l1_role_level": l1_role_level,
		"l2_role_level": l2_role_level,
		"routing_strategy": routing_strategy,
	}
	if frappe.db.exists("Grievance Department", name):
		frappe.db.set_value("Grievance Department", name, values, update_modified=False)
		return name
	frappe.get_doc(
		{
			"doctype": "Grievance Department",
			"dept_name": name,
			"short_name": short_name,
			"email_account": "stg404@example.com",
			**values,
		}
	).insert(ignore_permissions=True)
	return name
