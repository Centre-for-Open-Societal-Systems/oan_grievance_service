# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-443: department heads (L3) and reviewers on the officer API, and the status counts.

A department head is an officer at the `department_head` rung. A reviewer sits on the
department's desks too, read-only and scoped to them, and is never assigned a case.
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

from oan_grievance_service import permissions
from oan_grievance_service.api.v1._options import get_department_officers
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
	department_head_of,
	find_officer_by_role_level,
	query_active_officer_assignments,
)
from oan_grievance_service.grievance_masters.doctype.grievance_role_level.grievance_role_level import (
	GrievanceRoleLevel,
)
from oan_grievance_service.services import category_assignment, routing
from oan_grievance_service.services import constants as C
from oan_grievance_service.services.officer import MUST_CHANGE_PASSWORD_FIELD
from oan_grievance_service.setup.install import seed_role_levels
from oan_grievance_service.tests.fixtures import a_grievance, a_leaf_area, a_service_category
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_user,
)

PREFIX = "stg443-"
DESK = "Grievance RBAC Assignment"
ROW = "Grievance RBAC Assignment Officer"
PASSWORD = "Temp1234"


class RoleCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Ending a session makes Frappe commit, which would leave this test's fixtures behind.
		commit = mock.patch.object(frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)
		seed_role_levels()
		frappe.cache.delete_value("grievance_role_level_chain")
		self.department = _department("STG443 Inputs Agency", "S443")
		self.other_department = _department("STG443 Credit Agency", "S444")
		self.inputs = _category("STG443 Inputs", "Z83")
		self.credit = _category("STG443 Credit", "Z82")
		self.l1_seed = _user("seed443-l1@example.com", "Seed Nodal")
		self.l2_seed = _user("seed443-l2@example.com", "Seed Senior")
		for category, department in (
			(self.inputs, "S443"),
			(self.credit, "S443"),
			(a_service_category(), "S443"),
			(a_service_category(), "S444"),
		):
			for name in frappe.get_all(
				DESK, filters={"category_scope": category, "department_scope": department}, pluck="name"
			):
				frappe.delete_doc(DESK, name, force=1, ignore_permissions=True)
			category_assignment.create(
				service_category=category,
				department=department,
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

	def _head(self, email=f"{PREFIX}head@example.com", **extra):
		return self._officer(email, level="L3", designation="Department Head", full_name="Hana Head", **extra)

	def _reviewer(self, email=f"{PREFIX}reviewer@example.com", **extra):
		result = create_officer(
			**{
				"role": "Reviewer",
				"full_name": "Rahel Review",
				"email": email,
				"department": "S443",
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
			fields=["name", "parent", "role_level", "is_primary", "active", "on_leave", "reports_to"],
		)

	def _roles(self, user):
		return sorted(frappe.get_all("Has Role", filters={"parent": user}, pluck="role"))

	def _caller(self, email, role):
		_user(f"{PREFIX}{email}@example.com", email, role=role)
		return f"{PREFIX}{email}@example.com"

	def _ids(self, **filters):
		result = list_officers(page_size=100, **filters)
		self.assertEqual(result["status"], "success", msg=result)
		return [row["name"] for row in result["data"]["officers"]]


class TestDepartmentHead(RoleCase):
	def test_a_department_head_is_an_l3_officer_on_the_departments_desks(self):
		head = self._head(service_categories=[self.inputs, self.credit])
		self.assertEqual(
			(head["role"], head["level"], head["department"]), ("Officer", "L3", self.department)
		)
		self.assertEqual(head["designation"], "Department Head")
		self.assertEqual(sorted(head["service_categories"]), sorted([self.inputs, self.credit]))
		self.assertEqual({a["level"] for a in head["assignments"]}, {"L3"})
		self.assertIn(C.ROLE_OFFICER, self._roles(head["name"]))
		rows = self._rows(head["name"])
		self.assertEqual(len(rows), 2)
		self.assertEqual({(r.role_level, r.is_primary, r.active) for r in rows}, {("department_head", 0, 1)})
		self.assertEqual(get_officer(head["name"])["data"]["officer"], head)

	def test_a_department_head_needs_a_department_and_categories(self):
		base = {
			"full_name": "X",
			"designation": "Head",
			"level": "L3",
			"email": f"{PREFIX}x@example.com",
			"temporary_password": PASSWORD,
		}
		with _keep_transaction():
			result = create_officer(**base)
			self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
			self.assertLessEqual({"department", "service_categories"}, set(result["details"]))
			no_assignment = create_officer(**base, department="S444", service_categories=["STG443 Credit"])
			self.assertEqual(no_assignment["code"], "VALIDATION_ERROR", msg=no_assignment)
		self.assertFalse(frappe.db.exists("User", base["email"]))

	def test_the_level_filter_and_the_default_list_include_heads(self):
		officer = self._officer()
		head = self._head()
		self.assertLessEqual({officer["name"], head["name"]}, set(self._ids(q=PREFIX)))
		self.assertEqual(self._ids(level="L3", q=PREFIX), [head["name"]])
		self.assertEqual(self._ids(level="L1", q=PREFIX), [officer["name"]])
		self.assertEqual(self._ids(level="L3", q=PREFIX, department="S444"), [])

	def test_the_head_holds_the_top_rung_and_is_never_assigned_first_line_cases(self):
		head = self._head()
		self.assertEqual(department_head_of(self.department), head["name"])
		self.assertTrue(permissions.is_department_head(head["name"], self.department))
		self.assertEqual(current_level_of(head["name"]), "department_head")

		desk_name = self._rows(head["name"])[0].parent
		desk = frappe.get_doc(DESK, desk_name)
		self.assertEqual(routing.pick_officer_by_strategy(desk), self.l1_seed)
		for row in desk.officers:
			if row.user == self.l1_seed:
				row.on_leave = 1
		# The L1 is away, so the desk falls back to the L2 they report to. The head is never
		# the fallback: they are reached by escalation, not assigned first-line cases.
		self.assertEqual(routing.pick_officer_by_strategy(desk), self.l2_seed)

	def test_the_category_assignment_seats_ignore_a_head(self):
		head = self._head()
		desk = frappe.get_doc(DESK, self._rows(head["name"])[0].parent)
		for row in desk.officers:
			if row.user in (self.l1_seed, self.l2_seed):
				row.active = 0
		primary, secondary = category_assignment.split_officers(desk.officers)
		self.assertIsNone(primary)
		self.assertIsNone(secondary)

	def test_an_officer_can_be_promoted_to_head_and_back(self):
		officer = self._officer()
		promoted = update_officer(officer["name"], level="L3")["data"]["officer"]
		self.assertEqual(promoted["level"], "L3")
		row = self._rows(officer["name"])[0]
		self.assertEqual((row.role_level, row.is_primary), ("department_head", 0))
		demoted = update_officer(officer["name"], level="L1")["data"]["officer"]
		self.assertEqual(demoted["level"], "L1")
		self.assertEqual(self._rows(officer["name"])[0].is_primary, 1)

	def test_a_head_has_no_supervisor(self):
		head = self._head()
		with _keep_transaction():
			result = update_officer(head["name"], reports_to=self.l2_seed)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_heads_are_in_the_status_counts(self):
		self._head()
		self.assertEqual(
			officer_status_counts(level="L3", q=PREFIX)["data"],
			{"active": 1, "on_leave": 0, "inactive": 0, "total": 1},
		)

	def test_an_l4_is_not_a_level(self):
		with _keep_transaction():
			result = create_officer(
				full_name="X",
				designation="Y",
				level="L4",
				department="S443",
				email=f"{PREFIX}l4@example.com",
				service_categories=[self.inputs],
				temporary_password=PASSWORD,
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)


class TestReviewerOnTheDesk(RoleCase):
	def test_a_reviewer_is_placed_on_the_departments_desks_with_the_review_role(self):
		reviewer = self._reviewer(service_categories=[self.inputs, self.credit], designation="Auditor")
		self.assertEqual(reviewer["role"], "Reviewer")
		self.assertIsNone(reviewer["level"])
		self.assertEqual(reviewer["department"], self.department)
		self.assertEqual(sorted(reviewer["service_categories"]), sorted([self.inputs, self.credit]))
		self.assertEqual(reviewer["designation"], "Auditor")
		self.assertEqual({a["level"] for a in reviewer["assignments"]}, {None})
		self.assertEqual(self._roles(reviewer["name"]), [C.ROLE_REVIEW_OFFICER])
		rows = self._rows(reviewer["name"])
		self.assertEqual(len(rows), 2)
		self.assertEqual(
			{(r.role_level, r.is_primary, r.active, r.reports_to) for r in rows},
			{("review_officer", 0, 1, None)},
		)
		self.assertEqual(get_officer(reviewer["name"])["data"]["officer"], reviewer)

	def test_a_reviewer_needs_a_department_and_categories_and_takes_no_level(self):
		base = {"role": "Reviewer", "full_name": "X", "temporary_password": PASSWORD}
		email = f"{PREFIX}rv@example.com"
		with _keep_transaction():
			missing = create_officer(**base, email=email)
			self.assertLessEqual({"department", "service_categories"}, set(missing["details"]))
			for field, value in (("level", "L1"), ("reports_to", self.l2_seed)):
				result = create_officer(
					**base,
					email=email,
					department="S443",
					service_categories=[self.inputs],
					**{field: value},
				)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(field, result))
				self.assertEqual(list(result["details"]), [field])
			leave = create_officer(
				**base,
				email=email,
				department="S443",
				service_categories=[self.inputs],
				status="On Leave",
			)
			self.assertIn("status", leave["details"])
		self.assertFalse(frappe.db.exists("User", email))

	def test_a_reviewer_is_never_in_the_officer_views_or_the_chain(self):
		officer = self._officer()
		reviewer = self._reviewer()
		self.assertNotIn(reviewer["name"], self._ids(q=PREFIX))
		self.assertEqual(self._ids(role="Reviewer", q=PREFIX), [reviewer["name"]])
		self.assertNotIn(officer["name"], self._ids(role="Reviewer", q=PREFIX))

		stats = list_officer_statistics(page_size=100)["data"]["officers"]
		self.assertNotIn(reviewer["name"], {row["user"] for row in stats})
		self.assertIn(officer["name"], {row["user"] for row in stats})
		self.assertNotIn(reviewer["name"], {o["user_id"] for o in get_department_officers(self.department)})
		self.assertIn(officer["name"], {o["user_id"] for o in get_department_officers(self.department)})

		user = reviewer["name"]
		self.assertEqual(query_active_officer_assignments(user=user), [])
		self.assertIsNone(current_level_of(user))
		self.assertIsNone(find_officer_by_role_level("review_officer"))
		frappe.cache.delete_value("grievance_role_level_chain")
		self.assertNotIn("review_officer", {level.name for level in GrievanceRoleLevel.get_chain()})

	def test_a_reviewer_has_scopes_for_their_own_desks_only(self):
		reviewer = self._reviewer(service_categories=[self.inputs, self.credit])
		scopes = active_scopes(reviewer["name"])
		self.assertEqual({s.department_scope for s in scopes}, {self.department})
		self.assertEqual({s.category_scope for s in scopes}, {self.inputs, self.credit})

	def test_a_reviewer_is_never_assigned_a_case(self):
		reviewer = self._reviewer()
		desk = frappe.get_doc(DESK, self._rows(reviewer["name"])[0].parent)
		self.assertIn(reviewer["name"], [row.user for row in desk.officers])
		for _attempt in range(3):
			self.assertEqual(routing.pick_officer_by_strategy(desk), self.l1_seed)
		for row in desk.officers:
			if row.user == self.l1_seed:
				row.on_leave = 1
		self.assertEqual(routing.pick_officer_by_strategy(desk), self.l2_seed)
		seats = category_assignment.split_officers(desk.officers)
		self.assertNotEqual(seats[0].user if seats[0] else None, reviewer["name"])

	def test_a_reviewer_reads_their_departments_cases_and_nothing_else(self):
		reviewer = self._reviewer(service_categories=[a_service_category()])
		mine = a_grievance(assigned_dept=self.department)
		elsewhere = a_grievance(assigned_dept=self.other_department)
		draft = a_grievance(assigned_dept=self.department, workflow_state=C.STATE_DRAFT)

		self.assertTrue(permissions.has_grievance_permission(mine, "read", reviewer["name"]))
		self.assertFalse(permissions.has_grievance_permission(elsewhere, "read", reviewer["name"]))
		self.assertFalse(permissions.has_grievance_permission(draft, "read", reviewer["name"]))
		for ptype in ("write", "create", "delete", "submit", "cancel"):
			self.assertFalse(permissions.has_grievance_permission(mine, ptype, reviewer["name"]))

		frappe.set_user(reviewer["name"])
		names = {row.name for row in frappe.get_list("Grievance", fields=["name"], limit=500)}
		frappe.set_user("Administrator")
		self.assertIn(mine.name, names)
		self.assertNotIn(elsewhere.name, names)
		self.assertNotIn(draft.name, names)

	def test_a_reviewer_who_was_never_placed_keeps_oversight_of_every_filed_case(self):
		loose = _user(f"{PREFIX}loose@example.com", "Loose Reviewer", role=C.ROLE_REVIEW_OFFICER)
		case = a_grievance(assigned_dept=self.other_department)
		self.assertTrue(permissions.has_grievance_permission(case, "read", loose))
		self.assertEqual(permissions.grievance_query_conditions(loose), "(`tabGrievance`.docstatus != 0)")

	def test_a_reviewer_with_every_desk_retired_reads_nothing(self):
		reviewer = self._reviewer(service_categories=[a_service_category()])
		case = a_grievance(assigned_dept=self.department)
		update_officer(reviewer["name"], status="Inactive")
		self.assertFalse(permissions.has_grievance_permission(case, "read", reviewer["name"]))
		self.assertEqual(permissions.grievance_query_conditions(reviewer["name"]), "(1 = 0)")

	def test_a_reviewer_on_a_desk_still_validates_without_the_officer_role(self):
		reviewer = self._reviewer()
		desk = frappe.get_doc(DESK, self._rows(reviewer["name"])[0].parent)
		desk.save()
		self.assertNotIn(C.ROLE_OFFICER, self._roles(reviewer["name"]))

	def test_a_reviewer_has_no_level_or_supervisor_to_change(self):
		reviewer = self._reviewer()
		with _keep_transaction():
			for field, value in (("level", "L1"), ("reports_to", self.l2_seed)):
				result = update_officer(reviewer["name"], **{field: value})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(field, result))
			leave = update_officer(reviewer["name"], status="On Leave")
			self.assertEqual(leave["code"], "VALIDATION_ERROR", msg=leave)
		renamed = update_officer(
			reviewer["name"], full_name="Renamed", designation="Lead", service_categories=[self.credit]
		)["data"]["officer"]
		self.assertEqual(
			(renamed["full_name"], renamed["designation"], renamed["service_categories"]),
			("Renamed", "Lead", [self.credit]),
		)

	def test_reviewer_status_filters_and_counts(self):
		first = self._reviewer(f"{PREFIX}rv1@example.com")
		second = self._reviewer(f"{PREFIX}rv2@example.com")
		update_officer(second["name"], status="Inactive")
		self.assertEqual(self._ids(role="Reviewer", q=PREFIX, status="Active"), [first["name"]])
		self.assertEqual(self._ids(role="Reviewer", q=PREFIX, status="Inactive"), [second["name"]])
		self.assertEqual(
			officer_status_counts(role="Reviewer", q=PREFIX)["data"],
			{"active": 1, "on_leave": 0, "inactive": 1, "total": 2},
		)
		with _keep_transaction():
			for bad in ({"level": "L1"}, {"status": "On Leave"}):
				result = list_officers(role="Reviewer", **bad)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=bad)
			counts = officer_status_counts(role="Reviewer", level="L1")
			self.assertEqual(counts["code"], "VALIDATION_ERROR")

	def test_roles_are_not_exclusive(self):
		"""An existing login keeps what it has and is given the role the placement needs."""
		login = _user(f"{PREFIX}submitter@example.com", "Submitter", role=C.ROLE_SUBMITTER)
		update_password(login, "TheirOwn123!")
		frappe.db.set_value("User", login, MUST_CHANGE_PASSWORD_FIELD, 0)
		result = create_officer(
			role="Reviewer",
			full_name="Submitter Reviewer",
			email=login,
			department="S443",
			service_categories=[self.inputs],
			temporary_password=PASSWORD,
		)
		self.assertEqual(result["status"], "success", msg=result)
		self.assertIn("already had a login", result["message"])
		self.assertEqual(self._roles(login), sorted([C.ROLE_SUBMITTER, C.ROLE_REVIEW_OFFICER]))
		self.assertEqual(check_password(login, "TheirOwn123!"), login)

	def test_an_officer_or_reviewer_cannot_be_created_twice(self):
		officer = self._officer()
		reviewer = self._reviewer()
		with _keep_transaction():
			for email in (officer["email"].upper(), reviewer["email"]):
				for role in ("Officer", "Reviewer"):
					extra = {"designation": "Y", "level": "L1"} if role == "Officer" else {}
					result = create_officer(
						role=role,
						full_name="Dup",
						email=email,
						department="S443",
						service_categories=[self.inputs],
						temporary_password=PASSWORD,
						**extra,
					)
					self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(email, role, result))


class TestReviewerLogin(RoleCase):
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

	def _reviewer_with_own_password(self):
		reviewer = self._reviewer()
		update_password(reviewer["name"], "TheirOwn123!")
		frappe.db.set_value("User", reviewer["name"], MUST_CHANGE_PASSWORD_FIELD, 0)
		return reviewer["name"]

	def test_inactive_retires_the_rows_disables_the_login_and_ends_sessions_and_tokens(self):
		user = self._reviewer_with_own_password()
		self._session(user)
		with configured_keys():
			pair = _issue_token_pair(user, remember_me=False)
		self.assertTrue(frappe.db.exists(REFRESH_TOKEN_DOCTYPE, {"user": user}))

		result = update_officer(user, status="Inactive")
		self.assertEqual(result["status"], "success", msg=result)
		self.assertEqual(result["data"]["officer"]["status"], "Inactive")
		self.assertEqual({r.active for r in self._rows(user)}, {0})
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 0)
		self.assertFalse(self._sessions(user))
		self.assertFalse(frappe.db.exists(REFRESH_TOKEN_DOCTYPE, {"user": user}))

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

	def test_an_inactive_reviewer_cannot_sign_in(self):
		from oan_auth_service.tests.test_temporary_password import _call

		user = self._reviewer_with_own_password()
		deactivated = update_officer(user, status="Inactive")
		self.assertEqual(deactivated["status"], "success", msg=deactivated)
		# A refused sign-in rolls the request back, so nothing is asserted on the data after it.
		with configured_keys(), _keep_transaction():
			status, body = _call("/api/v1/auth/login", {"usr": user, "pwd": "TheirOwn123!"})
		self.assertEqual(status, 401, msg=body)
		self.assertNotIn("access_token", json.dumps(body))
		frappe.set_user("Administrator")

	def test_a_reviewer_can_sign_in_again_once_active_again(self):
		from oan_auth_service.tests.test_temporary_password import _call

		user = self._reviewer_with_own_password()
		update_officer(user, status="Inactive")
		reactivated = update_officer(user, status="Active")
		self.assertEqual(reactivated["status"], "success", msg=reactivated)
		self.assertEqual(reactivated["data"]["officer"]["status"], "Active")
		self.assertEqual({r.active for r in self._rows(user)}, {1})
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 1)
		with configured_keys(), _keep_transaction():
			status, body = _call("/api/v1/auth/login", {"usr": user, "pwd": "TheirOwn123!"})
		self.assertEqual(status, 200, msg=body)
		frappe.set_user("Administrator")

	def test_deactivating_an_officer_does_not_touch_their_login(self):
		officer = self._officer()
		update_officer(officer["name"], status="Inactive")
		self.assertEqual(frappe.db.get_value("User", officer["name"], "enabled"), 1)
		self.assertEqual({r.active for r in self._rows(officer["name"])}, {0})

	def test_nobody_deactivates_their_own_account(self):
		me = self._caller("me", C.ROLE_ADMIN)
		# An admin who is also placed as a reviewer on a desk.
		create_officer(
			role="Reviewer",
			full_name="Me",
			email=me,
			department="S443",
			service_categories=[self.inputs],
			temporary_password=PASSWORD,
		)
		frappe.set_user(me)
		with _keep_transaction():
			result = update_officer(me, status="Inactive")
		self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
		self.assertEqual(frappe.db.get_value("User", me, "enabled"), 1)


class TestPrivilegedAccounts(RoleCase):
	def test_system_manager_and_administrator_accounts_are_never_managed(self):
		manager = self._caller("manager", "System Manager")
		with _keep_transaction():
			for result in (
				create_officer(
					role="Reviewer",
					full_name="X",
					email=manager,
					department="S443",
					service_categories=[self.inputs],
					temporary_password=PASSWORD,
				),
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


class TestPasswordResets(RoleCase):
	def test_an_admin_resets_an_officer_a_head_and_a_reviewer(self):
		caller = self._caller("resetter", C.ROLE_ADMIN)
		accounts = (self._officer(), self._head(), self._reviewer())
		frappe.set_user(caller)
		for account in accounts:
			frappe.db.set_value("User", account["name"], MUST_CHANGE_PASSWORD_FIELD, 0)
			result = reset_temporary_password(account["name"], temporary_password="Another123")
			self.assertEqual(result["status"], "success", msg=result)
			self.assertTrue(result["data"]["officer"]["must_change_password"])
			self.assertEqual(check_password(account["name"], "Another123"), account["name"])

	def test_an_account_that_holds_an_admin_role_is_never_reset_here(self):
		officer = self._officer()
		frappe.get_doc("User", officer["name"]).add_roles(C.ROLE_ADMIN)
		with _keep_transaction():
			result = reset_temporary_password(officer["name"], temporary_password="Another123")
		self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
		self.assertEqual(check_password(officer["name"], PASSWORD), officer["name"])

	def test_nobody_resets_their_own_password(self):
		me = self._caller("self-admin", C.ROLE_ADMIN)
		frappe.set_user("Administrator")
		create_officer(
			role="Reviewer",
			full_name="Me",
			email=me,
			department="S443",
			service_categories=[self.inputs],
			temporary_password=PASSWORD,
		)
		frappe.set_user(me)
		with _keep_transaction():
			result = reset_temporary_password(me, temporary_password="Another123")
		self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_only_an_officer_or_reviewer_is_reset(self):
		plain = self._caller("plain", "Report Manager")
		with _keep_transaction():
			result = reset_temporary_password(plain, temporary_password="Another123")
		self.assertEqual(result["code"], "NOT_FOUND", msg=result)

	def test_resets_keep_the_rate_limit_and_the_log(self):
		reviewer = self._reviewer()
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


class TestPermissions(RoleCase):
	def setUp(self):
		super().setUp()
		self.officer = self._officer()
		self.head = self._head()
		self.reviewer = self._reviewer()
		self.callers = {
			"Reviewer": self._caller("c-reviewer", C.ROLE_REVIEW_OFFICER),
			"Officer": self._caller("c-officer", C.ROLE_OFFICER),
			"Submitter": self._caller("c-submitter", C.ROLE_SUBMITTER),
		}

	def _writes(self):
		return (
			create_officer(
				role="Reviewer",
				full_name="X",
				email=f"{PREFIX}w-reviewer@example.com",
				department="S443",
				service_categories=[self.inputs],
				temporary_password=PASSWORD,
			),
			create_officer(
				full_name="X",
				designation="Y",
				level="L3",
				department="S443",
				email=f"{PREFIX}w-head@example.com",
				service_categories=[self.inputs],
				temporary_password=PASSWORD,
			),
			update_officer(self.reviewer["name"], status="Inactive"),
			update_officer(self.head["name"], full_name="Renamed"),
			update_officer(self.officer["name"], full_name="Renamed"),
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
		self.assertEqual({r.active for r in self._rows(self.reviewer["name"])}, {1})
		self.assertFalse(frappe.db.exists("User", f"{PREFIX}w-reviewer@example.com"))

	def test_a_reviewer_reads_officers_and_heads_but_not_reviewers(self):
		frappe.set_user(self.callers["Reviewer"])
		with _keep_transaction():
			for result in (
				list_officers(),
				list_officers(level="L3"),
				get_officer(self.head["name"]),
				officer_status_counts(),
			):
				self.assertEqual(result["status"], "success", msg=result)
			for result in (
				list_officers(role="Reviewer"),
				get_officer(self.reviewer["name"]),
				officer_status_counts(role="Reviewer"),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_officers_and_submitters_read_nothing_here(self):
		for label in ("Officer", "Submitter"):
			frappe.set_user(self.callers[label])
			with _keep_transaction():
				for result in (list_officers(), list_officers(role="Reviewer"), officer_status_counts()):
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(label, result))

	def test_admins_and_system_managers_read_and_manage_every_role(self):
		for caller in (
			self._caller("g-admin", C.ROLE_ADMIN),
			self._caller("g-manager", "System Manager"),
			"Administrator",
		):
			frappe.set_user(caller)
			for role in ("Officer", "Reviewer"):
				self.assertEqual(list_officers(role=role)["status"], "success", msg=(caller, role))
				self.assertEqual(officer_status_counts(role=role)["status"], "success")
			for account in (self.officer, self.head, self.reviewer):
				self.assertEqual(get_officer(account["name"])["status"], "success")

	def test_the_mutating_routes_are_the_three_admin_only_ones(self):
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


class TestStatusCounts(RoleCase):
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
			paths.index("/api/v1/officers/status-counts"), paths.index("/api/v1/officers/<officer>")
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
		):
			counts = self._counts(**filters)
			for key, status in (("active", "Active"), ("on_leave", "On Leave"), ("inactive", "Inactive")):
				self.assertEqual(counts[key], self._list_total(status=status, **filters), msg=filters)
			self.assertEqual(counts["total"], self._list_total(**filters), msg=filters)

		self.assertEqual(self._counts(q=PREFIX), {"active": 2, "on_leave": 1, "inactive": 1, "total": 4})

	def test_an_officer_with_rows_in_two_statuses_is_counted_once_by_the_most_available(self):
		def make(n):
			return self._officer(f"{PREFIX}m{n}@example.com", service_categories=[self.inputs, self.credit])

		mixed = {n: make(n) for n in (1, 2, 3, 4)}

		def set_rows(account, first, second):
			rows = sorted(self._rows(account["name"]), key=lambda row: row.parent)
			for row, (active, on_leave) in zip(rows, (first, second), strict=True):
				frappe.db.set_value(ROW, row.name, {"active": active, "on_leave": on_leave})

		set_rows(mixed[1], (1, 0), (0, 0))
		set_rows(mixed[2], (1, 1), (0, 0))
		set_rows(mixed[3], (1, 0), (1, 1))
		set_rows(mixed[4], (0, 0), (0, 1))

		counts = self._counts(q=f"{PREFIX}m")
		self.assertEqual(counts, {"active": 2, "on_leave": 1, "inactive": 1, "total": 4})
		statuses = {n: get_officer(a["name"])["data"]["officer"]["status"] for n, a in mixed.items()}
		self.assertEqual(statuses, {1: "Active", 2: "On Leave", 3: "Active", 4: "Inactive"})
		per_status = {
			status: self._list_total(q=f"{PREFIX}m", status=status)
			for status in ("Active", "On Leave", "Inactive")
		}
		self.assertGreater(sum(per_status.values()), counts["total"])
		self.assertEqual(self._list_total(q=f"{PREFIX}m"), counts["total"])

	def test_an_officer_on_many_desks_is_one_person(self):
		self._officer(f"{PREFIX}many@example.com", service_categories=[self.inputs, self.credit])
		self.assertEqual(
			self._counts(q=f"{PREFIX}many"), {"active": 1, "on_leave": 0, "inactive": 0, "total": 1}
		)

	def test_counts_take_the_lists_filters_and_nothing_else(self):
		with _keep_transaction():
			for bad in (
				{"status": "Active"},
				{"page": 1},
				{"bogus": 1},
				{"level": "L4"},
				{"role": "Owner"},
				{"role": "Admin"},
				{"department": "Nope"},
				{"service_category": "Nope"},
				{"region": "Nowhere"},
			):
				result = officer_status_counts(**bad)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(bad, result))

	def test_an_empty_filter_counts_zero(self):
		self.assertEqual(
			self._counts(q="no-one-has-this-name"),
			{"active": 0, "on_leave": 0, "inactive": 0, "total": 0},
		)


class TestRegionFilter(RoleCase):
	def test_department_service_category_and_region_filters_apply_to_reviewers(self):
		area = a_leaf_area()
		scoped = self._reviewer(f"{PREFIX}rv-area@example.com", region=area)
		elsewhere = self._reviewer(f"{PREFIX}rv-else@example.com", service_categories=[self.credit])
		self.assertEqual(self._ids(role="Reviewer", q=PREFIX, region=area), [scoped["name"]])
		self.assertEqual(
			self._ids(role="Reviewer", q=PREFIX, service_category=self.credit), [elsewhere["name"]]
		)
		self.assertEqual(self._ids(role="Reviewer", q=PREFIX, department="S444"), [])
