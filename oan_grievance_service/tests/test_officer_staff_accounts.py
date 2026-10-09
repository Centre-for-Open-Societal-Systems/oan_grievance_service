# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-443: Admin and Reviewer accounts through the officer API, and the status counts.

Admin and Reviewer accounts are rostered on the inactive staff desk, so they are on the RBAC
record without being officers: routing, escalation, scope checks and statistics never see them.
"""

import json
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import check_password, update_password
from oan_auth_service.api.middleware import validate_jwt_request
from oan_auth_service.api.router import _rules, ensure_routes_registered
from oan_auth_service.api.v1.auth import REFRESH_TOKEN_DOCTYPE, _issue_token_pair
from oan_auth_service.tests.test_router import make_test_request
from oan_auth_service.tests.utils import configured_keys

from oan_grievance_service.api.v1.officer import (
	create_officer,
	get_officer,
	list_officers,
	officer_status_counts,
	reset_temporary_password,
	update_officer,
)
from oan_grievance_service.api.v1.officer_statistics import list_officer_statistics
from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	active_scopes,
	current_level_of,
	find_officer_by_role_level,
	get_officer_supervisor,
	get_subordinate_officers,
	holds_rung,
	query_active_officer_assignments,
	top_rung,
)
from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
	GrievanceRoleLevel,
)
from oan_grievance_service.services import category_assignment, routing, staff_account
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.officer import MUST_CHANGE_PASSWORD_FIELD
from oan_grievance_service.setup.install import seed_role_levels, seed_staff_desk
from oan_grievance_service.tests.fixtures import a_leaf_area
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_role_level,
	_user,
)

PREFIX = "stg443-"
DESK = "Grievance RBAC Assignment"
ROW = "Grievance RBAC Assignment Officer"
PASSWORD = "Temp1234"


class StaffAccountCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Ending a session makes Frappe commit, which would leave this test's fixtures behind.
		commit = mock.patch.object(frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		seed_staff_desk()
		self.department = _department("STG443 Inputs Agency", "S443")
		self.other_department = _department("STG443 Credit Agency", "S444")
		self.inputs = _category("STG443 Inputs", "Z83")
		self.credit = _category("STG443 Credit", "Z82")
		self.l1_seed = _user("seed443-l1@example.com", "Seed Nodal")
		self.l2_seed = _user("seed443-l2@example.com", "Seed Senior")
		for category in (self.inputs, self.credit):
			for name in frappe.get_all(DESK, filters={"category_scope": category}, pluck="name"):
				frappe.delete_doc(DESK, name, force=1, ignore_permissions=True)
			category_assignment.create(
				service_category=category,
				department="S443",
				l1_officer=self.l1_seed,
				l2_officer=self.l2_seed,
				sla_days=7,
				auto_escalate=True,
				active=True,
			)
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _staff(self, role="Admin", email=None, **extra):
		email = email or f"{PREFIX}{role.lower()}@example.com"
		result = create_officer(
			**{
				"role": role,
				"full_name": f"{role} Person",
				"email": email,
				"phone": "+251911000001",
				"temporary_password": PASSWORD,
				**extra,
			}
		)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["officer"]

	def _officer(self, email=f"{PREFIX}l1@example.com", **extra):
		result = create_officer(
			**{
				"full_name": "Tigist Alemu",
				"designation": "Nodal Officer",
				"level": "L1",
				"department": "S443",
				"email": email,
				"phone": "+251911123456",
				"service_categories": [self.inputs],
				"temporary_password": PASSWORD,
				**extra,
			}
		)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["officer"]

	def _rows(self, user):
		return frappe.get_all(
			ROW,
			filters={"user": user, "parenttype": DESK},
			fields=["name", "parent", "role_level", "active", "on_leave", "designation"],
		)

	def _roles(self, user):
		return sorted(frappe.get_all("Has Role", filters={"parent": user}, pluck="role"))

	def _caller(self, email, role):
		"""A login with `role`, and the session acting as it."""
		_user(f"{PREFIX}{email}@example.com", email, role=role)
		return f"{PREFIX}{email}@example.com"


class TestStaffDeskSeed(StaffAccountCase):
	def test_the_staff_desk_is_found_by_its_documented_name_and_is_never_active(self):
		desk = frappe.get_doc(DESK, C.STAFF_DESK)
		self.assertEqual(desk.name, "GR-RBAC-STAFF")
		self.assertEqual(desk.department_scope, "Administration")
		self.assertEqual(desk.active, 0)
		self.assertFalse(desk.category_scope)
		self.assertEqual(frappe.db.get_value("Grievance Department", "Administration", "active"), 0)

	def test_the_staff_desk_cannot_be_switched_on(self):
		self._staff("Admin")
		desk = frappe.get_doc(DESK, C.STAFF_DESK)
		desk.active = 1
		desk.save()
		self.assertEqual(frappe.db.get_value(DESK, C.STAFF_DESK, "active"), 0)

	def test_the_role_levels_are_inactive_and_outside_the_escalation_chain(self):
		for code in (C.ROLE_LEVEL_ADMIN, C.ROLE_LEVEL_REVIEW_OFFICER):
			self.assertEqual(frappe.db.get_value("Grievance Role Level", code, "is_active"), 0)
		frappe.cache.delete_value("grievance_role_level_chain")
		names = [level.name for level in GrievanceRoleLevel.get_chain()]
		self.assertNotIn(C.ROLE_LEVEL_ADMIN, names)
		self.assertNotIn(C.ROLE_LEVEL_REVIEW_OFFICER, names)
		self.assertIn(top_rung(), {"department_head", "senior_nodal_officer", "nodal_officer"})

	def test_seeding_is_idempotent_and_the_officer_api_heals_a_site_without_it(self):
		self.assertEqual(seed_role_levels(), [])
		self.assertEqual(seed_staff_desk(), [])
		frappe.delete_doc(DESK, C.STAFF_DESK, force=1, ignore_permissions=True)
		admin = self._staff()
		self.assertEqual(self._rows(admin["name"])[0].parent, C.STAFF_DESK)


class TestCreateStaffAccounts(StaffAccountCase):
	def test_an_admin_is_a_user_with_that_role_alone_and_a_row_on_the_staff_desk(self):
		admin = self._staff("Admin", designation="Programme Admin")
		self.assertEqual(admin["name"], f"{PREFIX}admin@example.com")
		self.assertEqual(admin["role"], "Admin")
		self.assertEqual(admin["designation"], "Programme Admin")
		self.assertEqual(admin["status"], "Active")
		self.assertTrue(admin["must_change_password"])
		self.assertEqual(self._roles(admin["name"]), [C.ROLE_ADMIN])

		rows = self._rows(admin["name"])
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].parent, rows[0].role_level, rows[0].active), (C.STAFF_DESK, "admin", 1))
		self.assertEqual(rows[0].designation, "Programme Admin")
		self.assertEqual(frappe.db.get_value(DESK, C.STAFF_DESK, "active"), 0)

	def test_a_reviewer_is_a_user_with_that_role_alone_and_a_row_on_the_staff_desk(self):
		reviewer = self._staff("Reviewer")
		self.assertEqual(reviewer["role"], "Reviewer")
		self.assertEqual(self._roles(reviewer["name"]), [C.ROLE_REVIEW_OFFICER])
		row = self._rows(reviewer["name"])[0]
		self.assertEqual((row.parent, row.role_level), (C.STAFF_DESK, "review_officer"))

	def test_the_record_has_no_desk_fields(self):
		for role in ("Admin", "Reviewer"):
			record = self._staff(role)
			for field in (
				"level",
				"department",
				"region",
				"region_name",
				"reports_to",
				"reports_to_name",
			):
				self.assertIsNone(record[field], msg=(role, field))
			self.assertEqual(record["service_categories"], [])
			self.assertEqual(record["assignments"], [])
			self.assertEqual(get_officer(record["name"])["data"]["officer"], record)

	def test_designation_is_optional(self):
		self.assertIsNone(self._staff("Admin")["designation"])

	def test_the_temporary_password_is_applied_and_must_be_replaced(self):
		admin = self._staff("Admin", temporary_password="Another123")
		self.assertEqual(check_password(admin["name"], "Another123"), admin["name"])
		self.assertEqual(frappe.db.get_value("User", admin["name"], MUST_CHANGE_PASSWORD_FIELD), 1)

	def test_an_officer_still_needs_the_desk_fields_and_the_error_names_each(self):
		with _keep_transaction():
			result = create_officer(
				full_name="X", email=f"{PREFIX}x@example.com", temporary_password=PASSWORD
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertLessEqual(
			{"designation", "level", "department", "service_categories"}, set(result["details"])
		)

	def test_an_admin_or_reviewer_needs_a_phone(self):
		with _keep_transaction():
			for role in ("Admin", "Reviewer"):
				result = create_officer(
					role=role,
					full_name="X",
					email=f"{PREFIX}nophone@example.com",
					temporary_password=PASSWORD,
				)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn("phone", result["details"])

	def test_desk_only_fields_are_refused_for_an_admin_or_reviewer(self):
		area = a_leaf_area()
		refused = {
			"level": "L1",
			"department": "S443",
			"service_categories": [self.inputs],
			"reports_to": self.l2_seed,
			"region": area,
		}
		with _keep_transaction():
			for role in ("Admin", "Reviewer"):
				for field, value in refused.items():
					result = create_officer(
						role=role,
						full_name="X",
						email=f"{PREFIX}desk@example.com",
						phone="+251911000001",
						temporary_password=PASSWORD,
						**{field: value},
					)
					self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(role, field, result))
					self.assertEqual(list(result["details"]), [field], msg=(role, field, result))
				everything = create_officer(
					role=role,
					full_name="X",
					email=f"{PREFIX}desk@example.com",
					phone="+251911000001",
					temporary_password=PASSWORD,
					**refused,
				)
				self.assertEqual(set(everything["details"]), set(refused))
		self.assertFalse(frappe.db.exists("User", f"{PREFIX}desk@example.com"))

	def test_on_leave_is_refused_for_an_admin_or_reviewer(self):
		with _keep_transaction():
			for role in ("Admin", "Reviewer"):
				result = create_officer(
					role=role,
					full_name="X",
					email=f"{PREFIX}leave@example.com",
					phone="+251911000001",
					temporary_password=PASSWORD,
					status="On Leave",
				)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn("status", result["details"])

	def test_an_unknown_role_and_a_weak_password_are_refused(self):
		with _keep_transaction():
			base = {
				"full_name": "X",
				"email": f"{PREFIX}bad@example.com",
				"phone": "+251911000001",
				"temporary_password": PASSWORD,
			}
			self.assertEqual(create_officer(**{**base, "role": "Owner"})["code"], "VALIDATION_ERROR")
			weak = create_officer(**{**base, "role": "Admin", "temporary_password": "lettersonly"})
			self.assertEqual(weak["code"], "VALIDATION_ERROR")
			self.assertIn("temporary_password", weak["details"])
		self.assertFalse(frappe.db.exists("User", f"{PREFIX}bad@example.com"))

	def test_an_account_can_be_created_inactive(self):
		admin = self._staff("Admin", status="Inactive")
		self.assertEqual(admin["status"], "Inactive")
		self.assertEqual(frappe.db.get_value("User", admin["name"], "enabled"), 0)
		self.assertEqual(self._rows(admin["name"])[0].active, 0)

	def test_a_second_account_for_the_same_email_is_refused(self):
		admin = self._staff("Admin")
		with _keep_transaction():
			again = create_officer(
				role="Reviewer",
				full_name="X",
				email=admin["email"].upper(),
				phone="+251911000001",
				temporary_password=PASSWORD,
			)
			self.assertEqual(again["code"], "VALIDATION_ERROR", msg=again)
			same = create_officer(
				role="Admin",
				full_name="X",
				email=admin["email"],
				phone="+251911000001",
				temporary_password=PASSWORD,
			)
			self.assertEqual(same["code"], "VALIDATION_ERROR", msg=same)
		self.assertEqual(self._roles(admin["name"]), [C.ROLE_ADMIN])


class TestOneGrievanceRole(StaffAccountCase):
	def _create_staff(self, role, email):
		return create_officer(
			role=role,
			full_name="Existing",
			email=email,
			phone="+251911000001",
			temporary_password=PASSWORD,
		)

	def test_an_existing_login_without_a_grievance_role_is_registered_and_keeps_its_password(self):
		login = f"{PREFIX}plain@example.com"
		_user(login, "Plain Login", role="Report Manager")
		update_password(login, "TheirOwn123!")
		frappe.db.set_value("User", login, MUST_CHANGE_PASSWORD_FIELD, 0)
		result = self._create_staff("Reviewer", login)
		self.assertEqual(result["status"], "success", msg=result)
		self.assertIn("already had a login", result["message"])
		self.assertFalse(result["data"]["officer"]["must_change_password"])
		self.assertEqual(check_password(login, "TheirOwn123!"), login)
		self.assertEqual(self._roles(login), sorted(["Report Manager", C.ROLE_REVIEW_OFFICER]))

	def test_a_desk_made_admin_can_be_put_on_the_roster(self):
		login = _user(f"{PREFIX}deskadmin@example.com", "Desk Admin", role=C.ROLE_ADMIN)
		result = self._create_staff("Admin", login)
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(self._rows(login)[0].parent, C.STAFF_DESK)
		self.assertEqual(self._roles(login), [C.ROLE_ADMIN])

	def test_a_login_with_another_grievance_role_cannot_become_an_admin_or_reviewer(self):
		held = {
			C.ROLE_SUBMITTER: f"{PREFIX}submitter@example.com",
			C.ROLE_OFFICER: f"{PREFIX}officer@example.com",
			C.ROLE_ADMIN: f"{PREFIX}other-admin@example.com",
			C.ROLE_REVIEW_OFFICER: f"{PREFIX}other-reviewer@example.com",
		}
		for role, email in held.items():
			_user(email, role, role=role)
		with _keep_transaction():
			for role, wanted in (("Admin", C.ROLE_ADMIN), ("Reviewer", C.ROLE_REVIEW_OFFICER)):
				for held_role, email in held.items():
					if held_role == wanted:
						continue
					result = self._create_staff(role, email)
					self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(role, held_role, result))
					self.assertIn("one grievance role", result["message"])
					self.assertEqual(self._roles(email), [held_role])
					self.assertFalse(self._rows(email))

	def test_create_officer_rejects_a_login_that_is_an_admin_or_reviewer(self):
		held = {
			C.ROLE_ADMIN: f"{PREFIX}was-admin@example.com",
			C.ROLE_REVIEW_OFFICER: f"{PREFIX}was-reviewer@example.com",
			C.ROLE_SUBMITTER: f"{PREFIX}was-submitter@example.com",
		}
		for role, email in held.items():
			_user(email, role, role=role)
		with _keep_transaction():
			for role, email in held.items():
				result = create_officer(
					full_name="X",
					designation="Y",
					level="L1",
					department="S443",
					email=email,
					service_categories=[self.inputs],
					temporary_password=PASSWORD,
				)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(role, result))
				self.assertEqual(self._roles(email), [role])
				self.assertFalse(self._rows(email))

	def test_create_officer_rejects_a_rostered_admin_even_if_the_role_was_removed(self):
		admin = self._staff("Admin")
		frappe.db.delete("Has Role", {"parent": admin["name"]})
		with _keep_transaction():
			result = create_officer(
				full_name="X",
				designation="Y",
				level="L1",
				department="S443",
				email=admin["email"],
				service_categories=[self.inputs],
				temporary_password=PASSWORD,
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_an_officer_is_not_made_an_admin_by_the_same_email(self):
		officer = self._officer()
		with _keep_transaction():
			result = self._create_staff("Admin", officer["email"])
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertEqual(self._roles(officer["name"]), [C.ROLE_OFFICER])

	def test_system_manager_and_administrator_accounts_are_never_managed(self):
		manager = self._caller("manager", "System Manager")
		with _keep_transaction():
			for result in (
				self._create_staff("Admin", manager),
				self._create_staff("Reviewer", manager),
				create_officer(
					full_name="X",
					designation="Y",
					level="L1",
					department="S443",
					email=manager,
					service_categories=[self.inputs],
					temporary_password=PASSWORD,
				),
				update_officer(manager, full_name="Renamed"),
				update_officer("Administrator", full_name="Renamed"),
				reset_temporary_password(manager, temporary_password="Another123"),
				reset_temporary_password("Administrator", temporary_password="Another123"),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
			self.assertEqual(get_officer(manager)["code"], "NOT_FOUND")
		self.assertEqual(self._roles(manager), ["System Manager"])


class TestStaffNeverTakePartInGrievanceWork(StaffAccountCase):
	def test_staff_are_not_in_the_officer_list_or_its_search_or_filters(self):
		officer = self._officer()
		admin = self._staff("Admin")
		reviewer = self._staff("Reviewer")

		def ids(**filters):
			result = list_officers(page_size=100, **filters)
			self.assertEqual(result["status"], "success", msg=result)
			return {row["name"] for row in result["data"]["officers"]}

		for filters in (
			{},
			{"role": "Officer"},
			{"level": "L1"},
			{"q": PREFIX},
			{"status": "Active"},
		):
			found = ids(**filters)
			self.assertNotIn(admin["name"], found, msg=filters)
			self.assertNotIn(reviewer["name"], found, msg=filters)
		self.assertIn(officer["name"], ids(q=PREFIX))
		self.assertEqual(ids(q=PREFIX), {officer["name"]})

	def test_staff_have_no_active_assignment_scope_or_rung(self):
		admin = self._staff("Admin")
		reviewer = self._staff("Reviewer")
		for user in (admin["name"], reviewer["name"]):
			# The rows are active; it is the staff desk being inactive that takes them out.
			self.assertEqual(self._rows(user)[0].active, 1)
			self.assertEqual(query_active_officer_assignments(user=user), [])
			self.assertEqual(query_active_officer_assignments(user=user, include_user_details=True), [])
			self.assertEqual(active_scopes(user), [])
			self.assertIsNone(current_level_of(user))
			self.assertIsNone(get_officer_supervisor(user))
			self.assertEqual(get_subordinate_officers(user), {user})
			for level in (C.ROLE_LEVEL_ADMIN, C.ROLE_LEVEL_REVIEW_OFFICER, "nodal_officer"):
				self.assertFalse(holds_rung(user, level))
		for level in (C.ROLE_LEVEL_ADMIN, C.ROLE_LEVEL_REVIEW_OFFICER):
			self.assertEqual(query_active_officer_assignments(role_level=level), [])
			self.assertIsNone(find_officer_by_role_level(level))

	def test_staff_are_not_offered_a_case_by_routing(self):
		self._staff("Admin")
		self._staff("Reviewer")
		case = {"service_category": self.inputs, "administrative_area": a_leaf_area()}
		self.assertEqual(routing.department_desks(case, C.STAFF_DEPARTMENT), [])
		desks = {desk.name for desk in routing.department_desks(case, self.department)}
		self.assertNotIn(C.STAFF_DESK, desks)

	def test_staff_are_not_in_officer_statistics(self):
		officer = self._officer()
		admin = self._staff("Admin")
		reviewer = self._staff("Reviewer")
		result = list_officer_statistics(page_size=100)
		self.assertEqual(result["status"], "success", msg=result)
		users = {row["user"] for row in result["data"]["officers"]}
		self.assertIn(officer["name"], users)
		self.assertNotIn(admin["name"], users)
		self.assertNotIn(reviewer["name"], users)

	def test_staff_are_not_on_the_category_assignment_desks_or_the_escalation_chain(self):
		self._staff("Admin")
		self._staff("Reviewer")
		self.assertFalse(frappe.get_all(DESK, filters=category_assignment.desk_filters(name=C.STAFF_DESK)))
		frappe.cache.delete_value("grievance_role_level_chain")
		chain = {level.name for level in GrievanceRoleLevel.get_chain()}
		self.assertTrue(chain.isdisjoint({C.ROLE_LEVEL_ADMIN, C.ROLE_LEVEL_REVIEW_OFFICER}))

	def test_an_officer_role_is_not_needed_on_the_staff_desk(self):
		"""validate_links is skipped, so a Reviewer sits on the desk without the Officer role."""
		reviewer = self._staff("Reviewer")
		self.assertNotIn(C.ROLE_OFFICER, self._roles(reviewer["name"]))
		desk = frappe.get_doc(DESK, C.STAFF_DESK)
		self.assertIn(reviewer["name"], [row.user for row in desk.officers])


class TestListByRole(StaffAccountCase):
	def _admins(self):
		return {
			"Aster": self._staff("Admin", f"{PREFIX}aster@example.com", full_name="Aster Admin"),
			"Biruk": self._staff("Admin", f"{PREFIX}biruk@example.com", full_name="Biruk Admin"),
			"Chaltu": self._staff("Admin", f"{PREFIX}chaltu@example.com", full_name="Chaltu Admin"),
		}

	def _ids(self, **filters):
		result = list_officers(page_size=100, **filters)
		self.assertEqual(result["status"], "success", msg=result)
		return [row["name"] for row in result["data"]["officers"]]

	def test_role_selects_the_accounts_and_defaults_to_officer(self):
		officer = self._officer()
		admin = self._staff("Admin")
		reviewer = self._staff("Reviewer")
		self.assertIn(admin["name"], self._ids(role="Admin"))
		self.assertNotIn(reviewer["name"], self._ids(role="Admin"))
		self.assertNotIn(officer["name"], self._ids(role="Admin"))
		self.assertIn(reviewer["name"], self._ids(role="Reviewer"))
		self.assertNotIn(admin["name"], self._ids(role="Reviewer"))
		self.assertIn(officer["name"], self._ids())
		self.assertIn(officer["name"], self._ids(role="Officer"))
		self.assertEqual(self._ids(role=""), self._ids())

	def test_records_in_a_list_carry_the_role(self):
		self._staff("Admin")
		self._officer()
		by_role = {
			role: list_officers(role=role, q=PREFIX, page_size=100)["data"]["officers"]
			for role in ("Officer", "Admin")
		}
		self.assertEqual({row["role"] for row in by_role["Officer"]}, {"Officer"})
		self.assertEqual({row["role"] for row in by_role["Admin"]}, {"Admin"})

	def test_search_matches_name_or_email(self):
		accounts = self._admins()
		self.assertEqual(self._ids(role="Admin", q="Biruk"), [accounts["Biruk"]["name"]])
		self.assertEqual(self._ids(role="Admin", q="chaltu@"), [accounts["Chaltu"]["name"]])
		self.assertEqual(self._ids(role="Admin", q="nobody-by-this-name"), [])

	def test_the_list_is_ordered_by_name_and_paginated(self):
		accounts = self._admins()
		expected = [accounts[name]["name"] for name in ("Aster", "Biruk", "Chaltu")]
		self.assertEqual(self._ids(role="Admin", q=PREFIX), expected)

		first = list_officers(role="Admin", q=PREFIX, page=1, page_size=2)
		self.assertEqual([row["name"] for row in first["data"]["officers"]], expected[:2])
		self.assertEqual(first["pagination"]["total_count"], 3)
		self.assertTrue(first["pagination"]["has_next"])
		second = list_officers(role="Admin", q=PREFIX, page=2, page_size=2)
		self.assertEqual([row["name"] for row in second["data"]["officers"]], expected[2:])
		self.assertFalse(second["pagination"]["has_next"])

	def test_status_filters_the_list(self):
		accounts = self._admins()
		update_officer(accounts["Biruk"]["name"], status="Inactive")
		self.assertEqual(self._ids(role="Admin", q=PREFIX, status="Inactive"), [accounts["Biruk"]["name"]])
		self.assertEqual(
			self._ids(role="Admin", q=PREFIX, status="Active"),
			[accounts["Aster"]["name"], accounts["Chaltu"]["name"]],
		)
		page = list_officers(role="Admin", q=PREFIX, status="Active", page_size=1)
		self.assertEqual(page["pagination"]["total_count"], 2)

	def test_a_login_disabled_on_desk_reads_as_inactive_everywhere(self):
		admin = self._staff("Admin")
		frappe.db.set_value("User", admin["name"], "enabled", 0)
		self.assertEqual(get_officer(admin["name"])["data"]["officer"]["status"], "Inactive")
		self.assertEqual(self._ids(role="Admin", q=PREFIX, status="Inactive"), [admin["name"]])
		self.assertEqual(self._ids(role="Admin", q=PREFIX, status="Active"), [])
		counts = officer_status_counts(role="Admin", q=PREFIX)["data"]
		self.assertEqual((counts["active"], counts["inactive"]), (0, 1))

	def test_filters_that_only_officers_have_are_refused_for_staff(self):
		with _keep_transaction():
			for role in ("Admin", "Reviewer"):
				for filters in (
					{"level": "L1"},
					{"department": "S443"},
					{"service_category": self.inputs},
					{"region": "ET01"},
					{"status": "On Leave"},
				):
					result = list_officers(role=role, **filters)
					self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(role, filters, result))
					self.assertEqual(list(result["details"]), list(filters), msg=(role, filters, result))
			self.assertEqual(list_officers(role="Owner")["code"], "VALIDATION_ERROR")
			self.assertEqual(list_officers(role="Admin", bogus=1)["code"], "VALIDATION_ERROR")

	def test_get_returns_an_account_of_any_role(self):
		officer = self._officer()
		admin = self._staff("Admin")
		reviewer = self._staff("Reviewer")
		for account, role in ((officer, "Officer"), (admin, "Admin"), (reviewer, "Reviewer")):
			result = get_officer(account["name"])
			self.assertEqual(result["status"], "success", msg=result)
			self.assertEqual(result["data"]["officer"]["role"], role)


class TestUpdateStaffAccounts(StaffAccountCase):
	def test_name_phone_designation_and_status_can_change(self):
		admin = self._staff("Admin")
		result = update_officer(
			admin["name"], full_name="Renamed Admin", phone="+251911999999", designation="Lead"
		)
		self.assertEqual(result["status"], "success", msg=result)
		record = result["data"]["officer"]
		self.assertEqual(
			(record["full_name"], record["phone"], record["designation"], record["role"]),
			("Renamed Admin", "+251911999999", "Lead", "Admin"),
		)
		self.assertEqual(self._rows(admin["name"])[0].designation, "Lead")
		self.assertEqual(self._roles(admin["name"]), [C.ROLE_ADMIN])

	def test_everything_else_is_refused_and_changes_nothing(self):
		admin = self._staff("Admin")
		with _keep_transaction():
			for field, value in (
				("level", "L1"),
				("department", "S443"),
				("region", None),
				("reports_to", None),
				("service_categories", [self.inputs]),
				("email", "other@example.com"),
				("role", "Reviewer"),
			):
				result = update_officer(admin["name"], **{field: value})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(field, result))
			desk_fields = update_officer(admin["name"], level="L1", full_name="Sneaky")
			self.assertEqual(desk_fields["code"], "VALIDATION_ERROR", msg=desk_fields)
			self.assertEqual(update_officer(admin["name"])["code"], "VALIDATION_ERROR")
		self.assertEqual(get_officer(admin["name"])["data"]["officer"]["full_name"], "Admin Person")
		self.assertEqual(self._roles(admin["name"]), [C.ROLE_ADMIN])

	def test_on_leave_and_a_blank_phone_are_refused(self):
		reviewer = self._staff("Reviewer")
		with _keep_transaction():
			leave = update_officer(reviewer["name"], status="On Leave")
			self.assertEqual(leave["code"], "VALIDATION_ERROR", msg=leave)
			phone = update_officer(reviewer["name"], phone=None)
			self.assertEqual(phone["code"], "VALIDATION_ERROR", msg=phone)
			blank = update_officer(reviewer["name"], phone="  ")
			self.assertEqual(blank["code"], "VALIDATION_ERROR", msg=blank)
		self.assertEqual(self._rows(reviewer["name"])[0].on_leave, 0)
		self.assertEqual(get_officer(reviewer["name"])["data"]["officer"]["phone"], "+251911000001")

	def test_an_officer_update_is_unchanged(self):
		officer = self._officer()
		result = update_officer(officer["name"], level="L2", status="On Leave", department="S443")
		self.assertEqual(result["status"], "success", msg=result)
		record = result["data"]["officer"]
		self.assertEqual((record["role"], record["level"], record["status"]), ("Officer", "L2", "On Leave"))


class TestDeactivation(StaffAccountCase):
	def setUp(self):
		super().setUp()
		ensure_routes_registered()

	def _session(self, user):
		frappe.db.sql(
			"INSERT INTO `tabSessions` (`sid`, `user`, `lastupdate`, `sessiondata`, `status`) "
			"VALUES (%s, %s, NOW(), '{}', 'Active')",
			(f"stg443{frappe.generate_hash(length=12)}", user),
		)

	def _sessions(self, user):
		return frappe.db.sql("SELECT sid FROM `tabSessions` WHERE user = %s", user)

	def test_inactive_retires_the_row_disables_the_login_and_ends_sessions_and_tokens(self):
		reviewer = self._staff("Reviewer")
		user = reviewer["name"]
		frappe.db.set_value("User", user, MUST_CHANGE_PASSWORD_FIELD, 0)
		self._session(user)
		with configured_keys():
			pair = _issue_token_pair(user, remember_me=False)
		self.assertTrue(frappe.db.exists(REFRESH_TOKEN_DOCTYPE, {"user": user}))
		self.assertTrue(self._sessions(user))

		result = update_officer(user, status="Inactive")
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["officer"]["status"], "Inactive")
		self.assertEqual(self._rows(user)[0].active, 0)
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 0)
		self.assertFalse(self._sessions(user))
		self.assertFalse(frappe.db.exists(REFRESH_TOKEN_DOCTYPE, {"user": user}))

		# The access token it held is refused too: the middleware checks the login is enabled.
		with configured_keys():
			request = make_test_request(
				"/api/v1/auth/health",
				method="GET",
				headers={"Authorization": f"Bearer {pair['access_token']}"},
			)
			frappe.set_user("Guest")
			frappe.local.session = frappe._dict({"user": "Guest"})
			with self.assertRaises(frappe.AuthenticationError):
				validate_jwt_request(request)
		frappe.set_user("Administrator")

	def test_an_inactive_account_cannot_sign_in_and_can_after_it_is_active_again(self):
		from oan_auth_service.tests.test_temporary_password import _call

		admin = self._staff("Admin")
		user = admin["name"]
		update_password(user, "TheirOwn123!")
		frappe.db.set_value("User", user, MUST_CHANGE_PASSWORD_FIELD, 0)
		update_officer(user, status="Inactive")
		with configured_keys(), _keep_transaction():
			status, body = _call("/api/v1/auth/login", {"usr": user, "pwd": "TheirOwn123!"})
		self.assertEqual(status, 401, msg=body)
		self.assertNotIn("access_token", json.dumps(body))
		frappe.set_user("Administrator")

		reactivated = update_officer(user, status="Active")
		self.assertEqual(reactivated["data"]["officer"]["status"], "Active")
		self.assertEqual(self._rows(user)[0].active, 1)
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 1)
		with configured_keys(), _keep_transaction():
			status, body = _call("/api/v1/auth/login", {"usr": user, "pwd": "TheirOwn123!"})
		self.assertEqual(status, 200, msg=body)
		frappe.set_user("Administrator")

	def test_an_admin_cannot_deactivate_their_own_account(self):
		me = self._caller("me", C.ROLE_ADMIN)
		self._staff("Admin", me)
		other = self._caller("other-admin", C.ROLE_ADMIN)
		self.assertTrue(other)
		frappe.set_user(me)
		with _keep_transaction():
			result = update_officer(me, status="Inactive")
		self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
		self.assertEqual(frappe.db.get_value("User", me, "enabled"), 1)
		self.assertEqual(self._rows(me)[0].active, 1)

	def test_the_last_active_grievance_admin_cannot_be_deactivated(self):
		# Disable every other admin on the site so the one under test really is the last.
		for name in frappe.get_all(
			"Has Role", filters={"role": C.ROLE_ADMIN, "parenttype": "User"}, pluck="parent"
		):
			frappe.db.set_value("User", name, "enabled", 0)
		last = self._staff("Admin", f"{PREFIX}last@example.com")
		with _keep_transaction():
			result = update_officer(last["name"], status="Inactive")
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertIn("last active", result["message"])
		self.assertEqual(frappe.db.get_value("User", last["name"], "enabled"), 1)
		self.assertEqual(self._rows(last["name"])[0].active, 1)

		second = self._staff("Admin", f"{PREFIX}second@example.com")
		self.assertEqual(update_officer(last["name"], status="Inactive")["status"], "success")
		with _keep_transaction():
			again = update_officer(second["name"], status="Inactive")
		self.assertEqual(again["code"], "VALIDATION_ERROR", msg=again)

	def test_a_reviewer_can_always_be_deactivated(self):
		for name in frappe.get_all(
			"Has Role", filters={"role": C.ROLE_ADMIN, "parenttype": "User"}, pluck="parent"
		):
			frappe.db.set_value("User", name, "enabled", 0)
		reviewer = self._staff("Reviewer")
		result = update_officer(reviewer["name"], status="Inactive")
		self.assertEqual(result["status"], "success", msg=result)

	def test_deactivating_an_officer_behaves_as_before(self):
		officer = self._officer()
		update_officer(officer["name"], status="Inactive")
		self.assertEqual(frappe.db.get_value("User", officer["name"], "enabled"), 1)
		self.assertEqual(self._rows(officer["name"])[0].active, 0)


class TestPasswordResets(StaffAccountCase):
	def test_a_grievance_admin_resets_an_officer_and_a_reviewer(self):
		officer = self._officer()
		reviewer = self._staff("Reviewer")
		caller = self._caller("resetter", C.ROLE_ADMIN)
		frappe.set_user(caller)
		for account in (officer, reviewer):
			frappe.db.set_value("User", account["name"], MUST_CHANGE_PASSWORD_FIELD, 0)
			result = reset_temporary_password(account["name"], temporary_password="Another123")
			self.assertEqual(result["status"], "success", msg=result)
			self.assertTrue(result["data"]["officer"]["must_change_password"])
			self.assertEqual(check_password(account["name"], "Another123"), account["name"])
		self.assertIn("Reviewer", result["message"])

	def test_a_grievance_admin_cannot_reset_an_admin_but_a_system_manager_can(self):
		admin = self._staff("Admin")
		caller = self._caller("other", C.ROLE_ADMIN)
		manager = self._caller("sysmgr", "System Manager")

		frappe.set_user(caller)
		with _keep_transaction():
			denied = reset_temporary_password(admin["name"], temporary_password="Another123")
		self.assertEqual(denied["code"], "PERMISSION_DENIED", msg=denied)
		self.assertEqual(check_password(admin["name"], PASSWORD), admin["name"])

		frappe.set_user(manager)
		allowed = reset_temporary_password(admin["name"], temporary_password="Another123")
		self.assertEqual(allowed["status"], "success", msg=allowed)
		self.assertEqual(check_password(admin["name"], "Another123"), admin["name"])

		frappe.set_user("Administrator")
		again = reset_temporary_password(admin["name"], temporary_password="Third12345")
		self.assertEqual(again["status"], "success", msg=again)

	def test_an_admin_role_on_an_officer_makes_the_account_an_admin_for_resets(self):
		officer = self._officer()
		user = frappe.get_doc("User", officer["name"])
		user.append("roles", {"role": C.ROLE_ADMIN})
		user.save(ignore_permissions=True)
		frappe.set_user(self._caller("someone", C.ROLE_ADMIN))
		with _keep_transaction():
			denied = reset_temporary_password(officer["name"], temporary_password="Another123")
		self.assertEqual(denied["code"], "PERMISSION_DENIED", msg=denied)

	def test_nobody_resets_their_own_password(self):
		for role in (C.ROLE_ADMIN, "System Manager"):
			me = self._caller(f"self-{role.split()[0].lower()}", role)
			if role == C.ROLE_ADMIN:
				self._staff("Admin", me)
			frappe.set_user(me)
			with _keep_transaction():
				result = reset_temporary_password(me, temporary_password="Another123")
			self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(role, result))
			frappe.set_user("Administrator")

	def test_only_an_account_on_the_roster_is_reset(self):
		plain = self._caller("plain", "Report Manager")
		with _keep_transaction():
			result = reset_temporary_password(plain, temporary_password="Another123")
		self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	def test_staff_resets_keep_the_rate_limit_and_the_log(self):
		reviewer = self._staff("Reviewer")
		caller = self._caller("limited", C.ROLE_ADMIN)
		frappe.set_user(caller)
		with (
			mock.patch("oan_grievance_service.api.v1.officer.check_rate_limit") as limit,
			mock.patch("oan_grievance_service.api.v1.officer.frappe.logger") as logger,
		):
			result = reset_temporary_password(reviewer["name"], temporary_password="Another123")
		self.assertEqual(result["status"], "success", msg=result)
		limit.assert_called_once_with(f"rl:officer_temporary_password:{caller}", limit=10, window=300)
		logged = logger.return_value.info.call_args.args[0]
		self.assertIn(f"by={caller}", logged)
		self.assertIn(f"for={reviewer['name']}", logged)
		self.assertIn("role=Reviewer", logged)


class TestPermissions(StaffAccountCase):
	def setUp(self):
		super().setUp()
		self.officer = self._officer()
		self.admin = self._staff("Admin")
		self.reviewer = self._staff("Reviewer")
		self.callers = {
			"Reviewer": self._caller("c-reviewer", C.ROLE_REVIEW_OFFICER),
			"Officer": self._caller("c-officer", C.ROLE_OFFICER),
			"Submitter": self._caller("c-submitter", C.ROLE_SUBMITTER),
		}

	def _writes(self):
		return (
			create_officer(
				role="Admin",
				full_name="X",
				email=f"{PREFIX}w-admin@example.com",
				phone="+251911000001",
				temporary_password=PASSWORD,
			),
			create_officer(
				role="Reviewer",
				full_name="X",
				email=f"{PREFIX}w-reviewer@example.com",
				phone="+251911000001",
				temporary_password=PASSWORD,
			),
			create_officer(
				full_name="X",
				designation="Y",
				level="L1",
				department="S443",
				email=f"{PREFIX}w-officer@example.com",
				service_categories=[self.inputs],
				temporary_password=PASSWORD,
			),
			update_officer(self.admin["name"], status="Inactive"),
			update_officer(self.reviewer["name"], full_name="Renamed"),
			update_officer(self.officer["name"], full_name="Renamed"),
			reset_temporary_password(self.admin["name"], temporary_password="Another123"),
			reset_temporary_password(self.reviewer["name"], temporary_password="Another123"),
			reset_temporary_password(self.officer["name"], temporary_password="Another123"),
		)

	def test_reviewers_officers_and_submitters_are_refused_every_mutating_call(self):
		for label, caller in self.callers.items():
			frappe.set_user(caller)
			with _keep_transaction():
				for result in self._writes():
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(label, result))
		frappe.set_user("Administrator")
		self.assertEqual(self._roles(self.admin["name"]), [C.ROLE_ADMIN])
		self.assertEqual(self._rows(self.admin["name"])[0].active, 1)
		self.assertFalse(frappe.db.exists("User", f"{PREFIX}w-admin@example.com"))

	def test_a_reviewer_reads_officers_but_not_admins_or_reviewers(self):
		frappe.set_user(self.callers["Reviewer"])
		with _keep_transaction():
			for result in (
				list_officers(),
				list_officers(role="Officer"),
				get_officer(self.officer["name"]),
				officer_status_counts(),
				officer_status_counts(role="Officer"),
			):
				self.assertEqual(result["status"], "success", msg=result)
			for result in (
				list_officers(role="Admin"),
				list_officers(role="Reviewer"),
				get_officer(self.admin["name"]),
				get_officer(self.reviewer["name"]),
				officer_status_counts(role="Admin"),
				officer_status_counts(role="Reviewer"),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_officers_and_submitters_read_nothing_here(self):
		for label in ("Officer", "Submitter"):
			frappe.set_user(self.callers[label])
			with _keep_transaction():
				for result in (
					list_officers(),
					list_officers(role="Admin"),
					get_officer(self.officer["name"]),
					officer_status_counts(),
				):
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(label, result))

	def test_admins_and_system_managers_read_and_manage_every_role(self):
		callers = (
			self._caller("g-admin", C.ROLE_ADMIN),
			self._caller("g-manager", "System Manager"),
			"Administrator",
		)
		for caller in callers:
			frappe.set_user(caller)
			for role in ("Officer", "Admin", "Reviewer"):
				self.assertEqual(list_officers(role=role)["status"], "success", msg=(caller, role))
				self.assertEqual(officer_status_counts(role=role)["status"], "success")
			for account in (self.officer, self.admin, self.reviewer):
				self.assertEqual(get_officer(account["name"])["status"], "success")

	def test_no_mutating_route_lists_the_review_officer_with_the_new_calls(self):
		ensure_routes_registered()
		mutating = {
			(tuple(rule.endpoint._route["methods"]), rule.endpoint._route["path"])
			for rule in _rules
			if getattr(rule.endpoint, "_route", None)
			and rule.endpoint._route["path"].startswith("/api/v1/officers")
			and not set(rule.endpoint._route["methods"]) <= {"GET", "HEAD", "OPTIONS"}
		}
		self.assertEqual(
			mutating,
			{
				(("POST",), "/api/v1/officers"),
				(("PATCH",), "/api/v1/officers/<officer>"),
				(("POST",), "/api/v1/officers/<officer>/password-resets"),
			},
		)


class TestStatusCounts(StaffAccountCase):
	def _counts(self, **filters):
		result = officer_status_counts(**filters)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]

	def _list_total(self, **filters):
		result = list_officers(page_size=1, **filters)
		self.assertEqual(result["status"], "success", msg=result)
		return result["pagination"]["total_count"]

	def test_the_endpoint_is_registered_before_the_dynamic_officer_route(self):
		ensure_routes_registered()
		paths = [
			rule.endpoint._route["path"]
			for rule in _rules
			if getattr(rule.endpoint, "_route", None)
			and "GET" in rule.endpoint._route["methods"]
			and rule.endpoint._route["path"].startswith("/api/v1/officers")
		]
		self.assertLess(
			paths.index("/api/v1/officers/status-counts"),
			paths.index("/api/v1/officers/<officer>"),
		)

	def test_counts_match_the_list_for_the_same_filters(self):
		self._officer(f"{PREFIX}a1@example.com", full_name="A One")
		self._officer(f"{PREFIX}a2@example.com", full_name="A Two")
		on_leave = self._officer(f"{PREFIX}b1@example.com", full_name="B One", level="L2")
		inactive = self._officer(f"{PREFIX}c1@example.com", full_name="C One")
		update_officer(on_leave["name"], status="On Leave")
		update_officer(inactive["name"], status="Inactive")

		for filters in (
			{"q": PREFIX},
			{"q": PREFIX, "level": "L1"},
			{"q": PREFIX, "level": "L2"},
			{"q": PREFIX, "department": "S443"},
			{"q": PREFIX, "department": "S444"},
			{"q": PREFIX, "service_category": self.inputs},
			{"q": "One"},
		):
			counts = self._counts(**filters)
			self.assertEqual(counts["active"], self._list_total(status="Active", **filters), msg=filters)
			self.assertEqual(counts["on_leave"], self._list_total(status="On Leave", **filters), msg=filters)
			self.assertEqual(counts["inactive"], self._list_total(status="Inactive", **filters), msg=filters)
			self.assertEqual(counts["total"], self._list_total(**filters), msg=filters)

		counts = self._counts(q=PREFIX)
		self.assertEqual(
			(counts["active"], counts["on_leave"], counts["inactive"], counts["total"]),
			(2, 1, 1, 4),
		)
		self.assertEqual(
			self._counts(q=PREFIX, level="L2"),
			{"active": 0, "on_leave": 1, "inactive": 0, "total": 1},
		)

	def test_an_officer_with_rows_in_two_statuses_is_counted_once_by_the_most_available(self):
		active_and_inactive = self._officer(
			f"{PREFIX}m1@example.com", service_categories=[self.inputs, self.credit]
		)
		leave_and_inactive = self._officer(
			f"{PREFIX}m2@example.com", service_categories=[self.inputs, self.credit]
		)
		active_and_leave = self._officer(
			f"{PREFIX}m3@example.com", service_categories=[self.inputs, self.credit]
		)
		all_inactive = self._officer(f"{PREFIX}m4@example.com", service_categories=[self.inputs, self.credit])

		def set_rows(account, first, second):
			rows = sorted(self._rows(account["name"]), key=lambda row: row.parent)
			for row, (active, on_leave) in zip(rows, (first, second), strict=True):
				frappe.db.set_value(ROW, row.name, {"active": active, "on_leave": on_leave})

		set_rows(active_and_inactive, (1, 0), (0, 0))
		set_rows(leave_and_inactive, (1, 1), (0, 0))
		set_rows(active_and_leave, (1, 0), (1, 1))
		set_rows(all_inactive, (0, 0), (0, 1))

		counts = self._counts(q=f"{PREFIX}m")
		self.assertEqual(counts, {"active": 2, "on_leave": 1, "inactive": 1, "total": 4})
		self.assertEqual(sum(counts[key] for key in ("active", "on_leave", "inactive")), counts["total"])

		# The record agrees with the count ...
		statuses = {
			account["name"]: get_officer(account["name"])["data"]["officer"]["status"]
			for account in (
				active_and_inactive,
				leave_and_inactive,
				active_and_leave,
				all_inactive,
			)
		}
		self.assertEqual(
			statuses,
			{
				active_and_inactive["name"]: "Active",
				leave_and_inactive["name"]: "On Leave",
				active_and_leave["name"]: "Active",
				all_inactive["name"]: "Inactive",
			},
		)
		# ... while the list's status filter matches an officer with a row in that status, so
		# one with mixed rows is on two pages and the pages add up to more than the people.
		per_status = {
			status: self._list_total(q=f"{PREFIX}m", status=status)
			for status in ("Active", "On Leave", "Inactive")
		}
		self.assertGreater(sum(per_status.values()), counts["total"])
		self.assertEqual(self._list_total(q=f"{PREFIX}m"), counts["total"])

	def test_an_officer_on_many_desks_is_one_person(self):
		self._officer(f"{PREFIX}many@example.com", service_categories=[self.inputs, self.credit])
		self.assertEqual(
			self._counts(q=f"{PREFIX}many"),
			{"active": 1, "on_leave": 0, "inactive": 0, "total": 1},
		)

	def test_staff_are_counted_by_role_and_never_among_officers(self):
		officer = self._officer()
		first = self._staff("Admin", f"{PREFIX}ad1@example.com")
		second = self._staff("Admin", f"{PREFIX}ad2@example.com")
		self._staff("Reviewer", f"{PREFIX}rv1@example.com")
		update_officer(second["name"], status="Inactive")

		self.assertEqual(
			self._counts(role="Admin", q=PREFIX),
			{"active": 1, "on_leave": 0, "inactive": 1, "total": 2},
		)
		self.assertEqual(
			self._counts(role="Reviewer", q=PREFIX),
			{"active": 1, "on_leave": 0, "inactive": 0, "total": 1},
		)
		self.assertEqual(self._counts(q=PREFIX), {"active": 1, "on_leave": 0, "inactive": 0, "total": 1})
		self.assertEqual(self._counts(role="Admin", q="ad1"), self._counts(role="Admin", q=first["name"]))
		self.assertTrue(officer)

	def test_counts_take_the_lists_filters_and_nothing_else(self):
		with _keep_transaction():
			for bad in (
				{"status": "Active"},
				{"page": 1},
				{"bogus": 1},
				{"level": "L3"},
				{"role": "Owner"},
				{"department": "Nope"},
				{"service_category": "Nope"},
				{"region": "Nowhere"},
				{"role": "Admin", "level": "L1"},
				{"role": "Reviewer", "department": "S443"},
			):
				result = officer_status_counts(**bad)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(bad, result))

	def test_an_empty_filter_counts_zero(self):
		self.assertEqual(
			self._counts(q="no-one-has-this-name"),
			{"active": 0, "on_leave": 0, "inactive": 0, "total": 0},
		)
