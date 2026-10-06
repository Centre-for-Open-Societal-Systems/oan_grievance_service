# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""STG-430: officers are Users placed on category desks, so edits reach routing directly."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import check_password
from oan_auth_service.setup.install import MUST_CHANGE_PASSWORD_FIELD

from oan_grievance_service.api.v1.officer import (
	create_officer,
	get_officer,
	list_officers,
	reset_temporary_password,
	update_officer,
)
from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	get_officer_supervisor,
)
from oan_grievance_service.services import category_assignment, routing
from oan_grievance_service.tests.fixtures import a_leaf_area
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_role_level,
	_user,
)

PREFIX = "stg430-"
DESK = "Grievance RBAC Assignment"


class TestOfficerManagement(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		self.department = _department("STG430 Inputs Agency", "S430")
		self.other_department = _department("STG430 Credit Agency", "S431")
		self.inputs = _category("STG430 Inputs", "Z93")
		self.credit = _category("STG430 Credit", "Z92")
		self.l1_seed = _user(f"{PREFIX}seed-l1@example.com", "Seed Nodal")
		self.l2_seed = _user(f"{PREFIX}seed-l2@example.com", "Seed Senior")
		for category in (self.inputs, self.credit):
			for name in frappe.get_all(DESK, filters={"category_scope": category}, pluck="name"):
				frappe.delete_doc(DESK, name, force=1, ignore_permissions=True)
			category_assignment.create(
				service_category=category,
				department="S430",
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

	def _create(self, email="stg430-l1@example.com", **extra):
		result = create_officer(
			**{
				"full_name": "Tigist Alemu",
				"designation": "Nodal Officer",
				"level": "L1",
				"department": "S430",
				"email": email,
				"phone": "+251911123456",
				"service_categories": [self.inputs],
				**extra,
			}
		)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["officer"]

	def _rows(self, user):
		return frappe.get_all(
			"Grievance RBAC Assignment Officer",
			filters={"user": user, "parenttype": DESK},
			fields=[
				"parent",
				"role_level",
				"administrative_area",
				"reports_to",
				"active",
				"on_leave",
				"is_primary",
			],
		)

	def test_create_makes_a_user_and_a_desk_row(self):
		officer = self._create(email="STG430-L1@Example.com")
		self.assertEqual(officer["name"], "stg430-l1@example.com")
		self.assertEqual(officer["level"], "L1")
		self.assertEqual(officer["department"], self.department)
		self.assertEqual(officer["designation"], "Nodal Officer")
		self.assertEqual(officer["status"], "Active")
		self.assertEqual(officer["service_categories"], [self.inputs])

		user = frappe.get_doc("User", officer["name"])
		self.assertEqual(user.full_name, "Tigist Alemu")
		self.assertIn("Grievance Officer", [role.role for role in user.roles])
		rows = self._rows(officer["name"])
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].role_level, rows[0].is_primary, rows[0].active), ("nodal_officer", 1, 1))
		self.assertEqual(get_officer(officer["name"])["data"]["officer"], officer)

	def test_the_officer_is_what_routing_reads(self):
		"""One source of truth: the desk row an admin edits here is what the router picks."""
		officer = self._create(service_categories=[self.inputs, self.credit])
		desk = frappe.get_doc(DESK, self._rows(officer["name"])[0].parent)
		self.assertIn(officer["name"], [row.user for row in desk.officers])
		self.assertEqual(get_officer_supervisor(self.l1_seed), self.l2_seed)

	def test_reports_to_is_the_row_supervisor_and_follows_the_ladder(self):
		l2 = self._create("stg430-l2@example.com", level="L2", designation="Senior Nodal Officer")
		l1 = self._create("stg430-l1@example.com", reports_to=l2["name"])
		self.assertEqual(l1["reports_to"], l2["name"])
		self.assertEqual(l1["reports_to_name"], "Tigist Alemu")
		self.assertEqual(get_officer_supervisor(l1["name"]), l2["name"])

		with _keep_transaction():
			own = update_officer(l2["name"], reports_to=l1["name"])
			self.assertEqual(own["code"], "VALIDATION_ERROR", msg=own)
			not_l2 = update_officer(l1["name"], reports_to=self.l1_seed)
			self.assertEqual(not_l2["code"], "VALIDATION_ERROR", msg=not_l2)
		cleared = update_officer(l1["name"], reports_to=None)["data"]["officer"]
		self.assertIsNone(cleared["reports_to"])
		self.assertIsNone(get_officer_supervisor(l1["name"]))

	def test_region_lives_on_the_officer_row(self):
		area = a_leaf_area()
		officer = self._create(region=area)
		self.assertEqual(officer["region"], area)
		self.assertEqual(self._rows(officer["name"])[0].administrative_area, area)
		self.assertIsNone(update_officer(officer["name"], region=None)["data"]["officer"]["region"])
		self.assertIsNone(self._rows(officer["name"])[0].administrative_area)
		with _keep_transaction():
			self.assertEqual(update_officer(officer["name"], region="Nowhere")["code"], "VALIDATION_ERROR")

	def test_routing_picks_the_officer_whose_area_is_nearest(self):
		"""Nearest ancestor now resolves over officer rows rather than over the desk."""
		leaf = a_leaf_area()
		parent = frappe.db.get_value("Grievance Administrative Area", leaf, "parent_administrative_area")
		wide = self._create("stg430-wide@example.com", region=parent)
		narrow = self._create("stg430-narrow@example.com", region=leaf)
		desk = frappe.get_doc(DESK, self._rows(narrow["name"])[0].parent)
		case = {"service_category": self.inputs, "administrative_area": leaf}

		self.assertEqual(routing.pick_officer_by_strategy(desk, case), narrow["name"])
		update_officer(narrow["name"], status="On Leave")
		desk.reload()
		self.assertEqual(routing.pick_officer_by_strategy(desk, case), wide["name"])

	def test_on_leave_is_skipped_and_inactive_retires_the_rows(self):
		officer = self._create()
		desk = frappe.get_doc(DESK, self._rows(officer["name"])[0].parent)

		on_leave = update_officer(officer["name"], status="On Leave")["data"]["officer"]
		self.assertEqual(on_leave["status"], "On Leave")
		self.assertEqual(self._rows(officer["name"])[0].active, 1)
		desk.reload()
		self.assertNotEqual(routing.pick_officer_by_strategy(desk), officer["name"])

		self.assertEqual(self._rows(officer["name"])[0].on_leave, 1)

		update_officer(officer["name"], status="Inactive")
		self.assertEqual(self._rows(officer["name"])[0].active, 0)
		self.assertEqual(get_officer(officer["name"])["data"]["officer"]["status"], "Inactive")

		update_officer(officer["name"], status="Active")
		self.assertEqual(self._rows(officer["name"])[0].active, 1)
		self.assertEqual(self._rows(officer["name"])[0].on_leave, 0)

	def test_status_lives_on_every_desk_row_not_on_user(self):
		officer = self._create(service_categories=[self.inputs, self.credit])
		self.assertFalse(frappe.get_meta("User").has_field("grievance_officer_status"))

		update_officer(officer["name"], status="On Leave")
		rows = self._rows(officer["name"])
		self.assertEqual(len(rows), 2)
		self.assertEqual({(row.active, row.on_leave) for row in rows}, {(1, 1)})
		self.assertEqual(get_officer(officer["name"])["data"]["officer"]["status"], "On Leave")

	def test_service_categories_re_seat_the_officer_on_desks(self):
		officer = self._create()
		both = update_officer(officer["name"], service_categories=[self.inputs, self.credit])
		self.assertEqual(
			sorted(both["data"]["officer"]["service_categories"]), sorted([self.inputs, self.credit])
		)
		self.assertEqual(len(self._rows(officer["name"])), 2)
		only_credit = update_officer(officer["name"], service_categories=[self.credit])
		self.assertEqual(only_credit["data"]["officer"]["service_categories"], [self.credit])
		self.assertEqual(len(self._rows(officer["name"])), 1)

	def test_a_category_without_an_assignment_is_rejected(self):
		_category("STG430 Markets", "Z91")
		with _keep_transaction():
			result = create_officer(
				full_name="X",
				designation="Y",
				level="L1",
				department="S430",
				email="stg430-x@example.com",
				service_categories=["STG430 Markets"],
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertFalse(frappe.db.exists("User", "stg430-x@example.com"))

	def test_the_last_officer_cannot_be_taken_off_a_desk(self):
		officer = self._create()
		desk = self._rows(officer["name"])[0].parent
		for name in frappe.get_all(
			"Grievance RBAC Assignment Officer",
			filters={"parent": desk, "user": ["!=", officer["name"]]},
			pluck="name",
		):
			frappe.delete_doc("Grievance RBAC Assignment Officer", name, force=1, ignore_permissions=True)
		with _keep_transaction():
			result = update_officer(officer["name"], service_categories=[self.credit])
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)

	def test_an_existing_officer_cannot_be_created_again(self):
		officer = self._create()
		with _keep_transaction():
			self.assertEqual(
				create_officer(
					full_name="Dup",
					designation="Nodal Officer",
					level="L1",
					department="S430",
					email=officer["email"].upper(),
					service_categories=[self.inputs],
				)["code"],
				"VALIDATION_ERROR",
			)

	def test_an_existing_login_is_promoted(self):
		login = _user("stg430-existing@example.com", "Existing Login")
		officer = self._create(login)
		self.assertEqual(officer["name"], login)
		self.assertEqual(len(self._rows(login)), 1)

	def test_invalid_input_is_rejected(self):
		base = {
			"full_name": "X",
			"designation": "Y",
			"level": "L1",
			"department": "S430",
			"email": "stg430-x@example.com",
			"service_categories": [self.inputs],
		}
		bad = [
			{"department": "Nope"},
			{"email": "not-an-email"},
			{"level": "L3"},
			{"status": "Gone"},
			{"service_categories": []},
			{"service_categories": ["Nope"]},
			{"level": "L2", "reports_to": self.l2_seed},
			{"nonsense": 1},
		]
		with _keep_transaction():
			for extra in bad:
				result = create_officer(**{**base, **extra})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(extra, result))
			missing = create_officer(**{k: v for k, v in base.items() if k != "service_categories"})
			self.assertEqual(missing["code"], "VALIDATION_ERROR")

	def test_email_is_fixed_and_empty_update_is_rejected(self):
		officer = self._create()
		with _keep_transaction():
			for field, value in (("email", "other@example.com"), ("nonsense", 1)):
				result = update_officer(officer["name"], **{field: value})
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])
			self.assertEqual(update_officer(officer["name"])["code"], "VALIDATION_ERROR")

	def test_level_can_change_and_follows_the_ladder(self):
		l2 = self._create("stg430-l2@example.com", level="L2")
		l1 = self._create("stg430-l1@example.com", reports_to=l2["name"])

		with _keep_transaction():
			blocked = update_officer(l2["name"], level="L1")
			self.assertEqual(blocked["code"], "VALIDATION_ERROR", msg=blocked)
			self.assertEqual(get_officer(l2["name"])["data"]["officer"]["level"], "L2")

		promoted = update_officer(l1["name"], level="L2")["data"]["officer"]
		self.assertEqual(promoted["level"], "L2")
		self.assertIsNone(promoted["reports_to"])
		row = self._rows(l1["name"])[0]
		self.assertEqual((row.role_level, row.is_primary), ("senior_nodal_officer", 0))

		demoted = update_officer(l1["name"], level="L1", reports_to=l2["name"])["data"]["officer"]
		self.assertEqual((demoted["level"], demoted["reports_to"]), ("L1", l2["name"]))
		self.assertEqual(self._rows(l1["name"])[0].is_primary, 1)

	def test_detail_reports_the_desks_the_officer_sits_on(self):
		area = a_leaf_area()
		officer = self._create(service_categories=[self.inputs, self.credit], region=area)
		assignments = get_officer(officer["name"])["data"]["officer"]["assignments"]
		self.assertEqual({a["service_category"] for a in assignments}, {self.inputs, self.credit})
		for entry in assignments:
			self.assertTrue(frappe.db.exists("Grievance RBAC Assignment", entry["assignment"]))
			self.assertEqual(
				(entry["department"], entry["level"], entry["region"], entry["active"], entry["on_leave"]),
				(self.department, "L1", area, True, False),
			)

	def test_designation_lives_on_the_desk_rows_not_on_user(self):
		"""User is shared by submitters and admins, so the title is not a column on it."""
		self.assertFalse(frappe.get_meta("User").has_field("grievance_designation"))
		officer = self._create(service_categories=[self.inputs, self.credit])
		rows = frappe.get_all(
			"Grievance RBAC Assignment Officer",
			filters={"user": officer["name"], "parenttype": DESK},
			pluck="designation",
		)
		self.assertEqual(rows, ["Nodal Officer", "Nodal Officer"])

		update_officer(officer["name"], designation="Chief Nodal Officer")
		update_officer(officer["name"], service_categories=[self.inputs, self.credit])
		rows = frappe.get_all(
			"Grievance RBAC Assignment Officer",
			filters={"user": officer["name"], "parenttype": DESK},
			pluck="designation",
		)
		self.assertEqual(rows, ["Chief Nodal Officer", "Chief Nodal Officer"])
		self.assertEqual(
			get_officer(officer["name"])["data"]["officer"]["designation"], "Chief Nodal Officer"
		)

		with_new_desk = update_officer(officer["name"], service_categories=[self.inputs])
		self.assertEqual(with_new_desk["data"]["officer"]["designation"], "Chief Nodal Officer")

	def test_partial_update_changes_only_what_was_sent(self):
		officer = self._create()
		updated = update_officer(officer["name"], designation="Chief Nodal Officer", phone=None)
		updated = updated["data"]["officer"]
		self.assertEqual(updated["designation"], "Chief Nodal Officer")
		self.assertIsNone(updated["phone"])
		self.assertEqual(updated["full_name"], "Tigist Alemu")
		self.assertEqual(updated["service_categories"], [self.inputs])

	def test_missing_officer_is_not_found(self):
		with _keep_transaction():
			for result in (
				get_officer("nobody@example.com"),
				update_officer("nobody@example.com", status="Inactive"),
			):
				self.assertEqual(result["code"], "NOT_FOUND", msg=result)
			plain_user = get_officer(_user("stg430-notofficer@example.com", "Not An Officer"))
			self.assertEqual(plain_user["code"], "NOT_FOUND")

	def test_list_filters_and_paginates(self):
		l1 = self._create("stg430-a@example.com", full_name="Aster A")
		l2 = self._create("stg430-b@example.com", full_name="Biruk B", level="L2", status="On Leave")

		def ids(**filters):
			result = list_officers(page_size=100, **filters)
			self.assertEqual(result["status"], "success", msg=result)
			return {row["name"] for row in result["data"]["officers"]}

		self.assertLessEqual({l1["name"], l2["name"]}, ids())
		self.assertIn(l1["name"], ids(level="L1"))
		self.assertNotIn(l2["name"], ids(level="L1"))
		self.assertIn(l2["name"], ids(status="On Leave"))
		self.assertNotIn(l1["name"], ids(status="On Leave"))
		self.assertIn(l1["name"], ids(department="S430"))
		self.assertNotIn(l1["name"], ids(department="S431"))
		self.assertEqual(ids(level="L1", department="S430", status="Active", q="stg430-a"), {l1["name"]})

		page = list_officers(q="stg430-", page=1, page_size=1)
		self.assertEqual(len(page["data"]["officers"]), 1)
		self.assertGreaterEqual(page["pagination"]["total_count"], 2)
		self.assertTrue(page["pagination"]["has_next"])

	def test_list_filters_by_scope(self):
		area = a_leaf_area()
		in_scope = self._create("stg430-scope@example.com", service_categories=[self.credit], region=area)
		elsewhere = self._create("stg430-else@example.com", service_categories=[self.inputs])

		def ids(**filters):
			result = list_officers(page_size=100, **filters)
			self.assertEqual(result["status"], "success", msg=result)
			return {row["name"] for row in result["data"]["officers"]}

		self.assertIn(in_scope["name"], ids(service_category=self.credit))
		self.assertNotIn(elsewhere["name"], ids(service_category=self.credit))
		self.assertEqual(ids(region=area, q="stg430-"), {in_scope["name"]})

	def test_bad_filter_values_are_validation_errors(self):
		with _keep_transaction():
			for bad in (
				{"level": "L3"},
				{"status": "Gone"},
				{"page_size": 0},
				{"bogus": 1},
				{"department": "Nope"},
				{"service_category": "Nope"},
				{"region": "Nowhere"},
			):
				self.assertEqual(list_officers(**bad)["code"], "VALIDATION_ERROR", msg=bad)

	def test_only_admins_can_manage_officers(self):
		officer = self._create()
		plain = _user("stg430-plain@example.com", "Plain Officer")
		admin = _user("stg430-admin@example.com", "Desk Admin", role="Grievance Admin")
		frappe.set_user(plain)
		with _keep_transaction():
			for result in (
				list_officers(),
				get_officer(officer["name"]),
				update_officer(officer["name"], status="Inactive"),
				create_officer(
					full_name="X",
					designation="Y",
					level="L1",
					department="S430",
					email="a@b.co",
					service_categories=[self.inputs],
				),
			):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
			frappe.set_user("Guest")
			self.assertEqual(list_officers()["code"], "PERMISSION_DENIED")
		frappe.set_user(admin)
		self.assertEqual(list_officers()["status"], "success")

	def test_a_temporary_password_is_set_flagged_and_replaceable(self):
		without = self._create("stg430-nopassword@example.com")
		self.assertFalse(without["must_change_password"])

		officer = self._create(temporary_password="Temp1234")
		self.assertTrue(officer["must_change_password"])
		self.assertEqual(frappe.db.get_value("User", officer["name"], MUST_CHANGE_PASSWORD_FIELD), 1)
		# The password really exists: it is what set-initial-password will verify.
		self.assertEqual(check_password(officer["name"], "Temp1234"), officer["name"])
		self.assertEqual(get_officer(officer["name"])["data"]["officer"]["must_change_password"], True)

	def test_a_temporary_password_must_meet_the_rule(self):
		base = {
			"full_name": "X",
			"designation": "Y",
			"level": "L1",
			"department": "S430",
			"email": "stg430-weak@example.com",
			"service_categories": [self.inputs],
		}
		with _keep_transaction():
			for weak in ("short1", "lettersonly", "12345678"):
				result = create_officer(**base, temporary_password=weak)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(weak, result))
				self.assertIn("temporary_password", result["details"])
		self.assertFalse(frappe.db.exists("User", base["email"]))

	def test_a_temporary_password_is_refused_for_an_existing_login(self):
		"""An existing account already has a password; setting one here would be taking it over."""
		login = _user("stg430-has-login@example.com", "Has A Login")
		with _keep_transaction():
			result = create_officer(
				full_name="Has A Login",
				designation="Nodal Officer",
				level="L1",
				department="S430",
				email=login,
				service_categories=[self.inputs],
				temporary_password="Temp1234",
			)
		self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
		self.assertFalse(frappe.db.get_value("User", login, MUST_CHANGE_PASSWORD_FIELD))
		self.assertEqual(self._rows(login), [])

	def test_a_temporary_password_can_be_reissued_to_an_officer(self):
		officer = self._create()
		self.assertFalse(officer["must_change_password"])

		result = reset_temporary_password(officer["name"], temporary_password="Another123")
		self.assertEqual(result["status"], "success", msg=result)
		self.assertTrue(result["data"]["officer"]["must_change_password"])
		self.assertEqual(check_password(officer["name"], "Another123"), officer["name"])

	def test_a_temporary_password_is_only_reissued_to_an_officer(self):
		plain = _user("stg430-plain@example.com", "Plain Login")
		admin_officer = self._create("stg430-admin-officer@example.com")
		user = frappe.get_doc("User", admin_officer["name"])
		user.append("roles", {"role": "System Manager"})
		user.save(ignore_permissions=True)

		with _keep_transaction():
			not_an_officer = reset_temporary_password(plain, temporary_password="Another123")
			self.assertEqual(not_an_officer["code"], "NOT_FOUND", msg=not_an_officer)

			privileged = reset_temporary_password(admin_officer["name"], temporary_password="Another123")
			self.assertEqual(privileged["code"], "PERMISSION_DENIED", msg=privileged)

			weak = reset_temporary_password(admin_officer["name"], temporary_password="lettersonly")
			self.assertEqual(weak["code"], "VALIDATION_ERROR", msg=weak)

		self.assertFalse(frappe.db.get_value("User", plain, MUST_CHANGE_PASSWORD_FIELD))
		self.assertFalse(frappe.db.get_value("User", admin_officer["name"], MUST_CHANGE_PASSWORD_FIELD))
