# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from contextlib import contextmanager

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1._options import get_departments
from oan_grievance_service.api.v1.department import (
	create_department,
	deactivate_department,
	get_department,
	list_departments,
	update_department,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import notifications
from oan_grievance_service.tests.fixtures import a_grievance

PREFIX = "STG424"


class TestDepartmentApi(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.head = _user("stg424-head@example.com", "Hana Bekele")
		self.other_head = _user("stg424-head2@example.com", "Dawit Tesfaye")
		for name in frappe.get_all(
			"Grievance Department", {"dept_name": ["like", f"{PREFIX}%"]}, pluck="name"
		):
			frappe.delete_doc("Grievance Department", name, force=1, ignore_permissions=True)
		frappe.clear_messages()

	def _create(self, name=f"{PREFIX} Agency", **overrides):
		values = {"department_name": name, "email_account": "stg424@example.com", **overrides}
		result = create_department(**values)
		self.assertEqual(result["status"], "success", msg=result)
		return result["data"]["department"]

	def test_crud_and_head_assignment(self):
		created = create_department(
			department_name=f"{PREFIX} Agency",
			email_account="Dept.Mail@Example.com",
			short_name="S424",
			phone="+251111000000",
		)
		self.assertEqual(created["status"], "success", msg=created)
		self.assertEqual(created["meta"]["api_version"], "v1")
		department = created["data"]["department"]
		self.assertEqual(department["department_id"], f"{PREFIX} Agency")
		self.assertEqual(department["department_name"], f"{PREFIX} Agency")
		self.assertEqual(department["email_account"], "dept.mail@example.com")
		self.assertEqual(department["short_name"], "S424")
		self.assertIsNone(department["head_of_dept"])
		self.assertTrue(department["active"])
		department_id = department["department_id"]

		assigned = update_department(department_id, head_of_dept=self.head)
		self.assertEqual(assigned["status"], "success", msg=assigned)
		self.assertEqual(assigned["data"]["department"]["head_of_dept"], self.head)
		self.assertEqual(assigned["data"]["department"]["head_of_dept_name"], "Hana Bekele")
		self.assertEqual(
			frappe.db.get_value("Grievance Department", department_id, "head_of_dept"), self.head
		)

		reassigned = update_department(department_id, head_of_dept=self.other_head)
		self.assertEqual(reassigned["data"]["department"]["head_of_dept"], self.other_head)

		edited = update_department(department_id, email_account="new@example.com", phone=None)
		self.assertEqual(edited["data"]["department"]["email_account"], "new@example.com")
		self.assertIsNone(edited["data"]["department"]["phone"])
		self.assertEqual(edited["data"]["department"]["head_of_dept"], self.other_head)

		cleared = update_department(department_id, head_of_dept=None)
		self.assertIsNone(cleared["data"]["department"]["head_of_dept"])
		self.assertFalse(frappe.db.get_value("Grievance Department", department_id, "head_of_dept"))

		fetched = get_department(department_id)
		self.assertEqual(fetched["data"]["department"]["email_account"], "new@example.com")

		deactivated = deactivate_department(department_id)
		self.assertEqual(deactivated["status"], "success", msg=deactivated)
		self.assertFalse(deactivated["data"]["department"]["active"])
		self.assertEqual(frappe.db.get_value("Grievance Department", department_id, "active"), 0)
		self.assertFalse(deactivate_department(department_id)["data"]["department"]["active"])

		reactivated = update_department(department_id, active=True)
		self.assertTrue(reactivated["data"]["department"]["active"])

	def test_record_matches_the_options_shape(self):
		"""GrievanceDepartmentOption is a subset of the record, with the same values."""
		department = self._create(head_of_dept=self.head)
		option = next(row for row in get_departments() if row["department_id"] == department["department_id"])
		self.assertEqual(
			option,
			{key: department[key] for key in ("department_id", "department_name", "email_account")},
		)

	def test_retired_department_leaves_the_options_but_stays_listed(self):
		department = self._create()
		deactivate_department(department["department_id"])
		self.assertNotIn(department["department_id"], [row["department_id"] for row in get_departments()])
		listed = list_departments(q=PREFIX)
		self.assertEqual(
			[row["department_id"] for row in listed["data"]["departments"]], [department["department_id"]]
		)

	def test_list_filters_search_and_paging(self):
		first = self._create(f"{PREFIX} Alpha", head_of_dept=self.head, short_name="S424A")
		second = self._create(f"{PREFIX} Beta", email_account="beta@example.com")
		deactivate_department(second["department_id"])

		everything = list_departments(q=PREFIX)
		self.assertEqual(everything["pagination"]["total_count"], 2)
		self.assertEqual(
			[row["department_id"] for row in everything["data"]["departments"]],
			[first["department_id"], second["department_id"]],
		)
		self.assertEqual(everything["data"]["departments"][0]["head_of_dept_name"], "Hana Bekele")

		active = list_departments(q=PREFIX, active=True)
		self.assertEqual(
			[row["department_id"] for row in active["data"]["departments"]], [first["department_id"]]
		)
		inactive = list_departments(q=PREFIX, active="false")
		self.assertEqual(
			[row["department_id"] for row in inactive["data"]["departments"]], [second["department_id"]]
		)
		by_head = list_departments(q=PREFIX, head_of_dept=self.head)
		self.assertEqual(
			[row["department_id"] for row in by_head["data"]["departments"]], [first["department_id"]]
		)
		by_short_name = list_departments(q="S424A")
		self.assertEqual(by_short_name["pagination"]["total_count"], 1)
		by_email = list_departments(q="beta@example")
		self.assertEqual(
			[row["department_id"] for row in by_email["data"]["departments"]], [second["department_id"]]
		)

		page_two = list_departments(q=PREFIX, page=2, page_size=1)
		self.assertEqual(
			[row["department_id"] for row in page_two["data"]["departments"]], [second["department_id"]]
		)
		self.assertEqual(page_two["pagination"]["total_count"], 2)
		self.assertTrue(page_two["pagination"]["has_prev"])

	def test_bad_list_parameters_are_rejected(self):
		with _keep_transaction():
			for params, field in (
				({"actve": True}, "actve"),
				({"page": 0}, "page"),
				({"page_size": 101}, "page_size"),
				({"active": "maybe"}, "active"),
			):
				result = list_departments(**params)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])

	def test_create_validates_the_request(self):
		with _keep_transaction():
			for values, field in (
				({"department_name": " ", "email_account": "a@example.com"}, "department_name"),
				({"department_name": f"{PREFIX} X"}, "email_account"),
				({"department_name": f"{PREFIX} X", "email_account": "not-an-email"}, "email_account"),
				({"department_name": f"{PREFIX} X", "email_account": "a@example.com", "nodal": "x"}, "nodal"),
				(
					{
						"department_name": f"{PREFIX} X",
						"email_account": "a@example.com",
						"routing_strategy": "Random",
					},
					"routing_strategy",
				),
				({"department_name": "N" * 141, "email_account": "a@example.com"}, "department_name"),
			):
				result = create_department(**values)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])
		self.assertFalse(frappe.db.exists("Grievance Department", f"{PREFIX} X"))

	def test_duplicate_name_and_short_name_are_rejected(self):
		first = self._create(short_name="S424")
		with _keep_transaction():
			same_name = create_department(
				department_name=first["department_name"].lower(), email_account="b@example.com"
			)
			self.assertEqual(same_name["code"], "DUPLICATE_ENTRY", msg=same_name)
			same_short = create_department(
				department_name=f"{PREFIX} Other", email_account="b@example.com", short_name="S424"
			)
			self.assertEqual(same_short["code"], "DUPLICATE_ENTRY", msg=same_short)
		other = self._create(f"{PREFIX} Other", short_name="S424B")
		with _keep_transaction():
			taken = update_department(other["department_id"], short_name="S424")
			self.assertEqual(taken["code"], "DUPLICATE_ENTRY", msg=taken)
		# Keeping its own short name is not a clash.
		kept = update_department(other["department_id"], short_name="S424B", phone="+251111000001")
		self.assertEqual(kept["status"], "success", msg=kept)

	def test_head_must_be_an_enabled_officer(self):
		department = self._create()
		department_id = department["department_id"]
		citizen = _user("stg424-citizen@example.com", "Citizen", role="Grievance Submitter")
		disabled = _user("stg424-disabled@example.com", "Gone Officer", enabled=0)
		admin = _user("stg424-admin-head@example.com", "Admin Head", role="Grievance Admin")
		with _keep_transaction():
			for value in ("nobody@example.com", citizen, disabled):
				result = update_department(department_id, head_of_dept=value)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				created = create_department(
					department_name=f"{PREFIX} Headed {value}",
					email_account="a@example.com",
					head_of_dept=value,
				)
				self.assertEqual(created["code"], "VALIDATION_ERROR", msg=created)
		self.assertFalse(frappe.db.get_value("Grievance Department", department_id, "head_of_dept"))
		self.assertEqual(update_department(department_id, head_of_dept=admin)["status"], "success")

	def test_a_head_disabled_later_does_not_block_other_edits(self):
		department = self._create(head_of_dept=self.head)
		frappe.db.set_value("User", self.head, "enabled", 0, update_modified=False)
		edited = update_department(department["department_id"], phone="+251111000002")
		self.assertEqual(edited["status"], "success", msg=edited)
		self.assertEqual(edited["data"]["department"]["head_of_dept"], self.head)

	def test_name_is_fixed_and_unknown_fields_are_rejected(self):
		department = self._create()
		with _keep_transaction():
			for changes, field in (
				({"department_name": "Renamed"}, "department_name"),
				({"department_id": "Renamed"}, "department_id"),
				({"nodal_officer": "x@example.com"}, "nodal_officer"),
				({"email_account": None}, "email_account"),
				({"email_account": "not-an-email"}, "email_account"),
				({"active": None}, "active"),
			):
				result = update_department(department["department_id"], **changes)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn(field, result["details"])
		self.assertEqual(get_department(department["department_id"])["status"], "success")

	def test_empty_update_and_missing_or_blank_ids(self):
		department = self._create()
		with _keep_transaction():
			empty = update_department(department["department_id"])
			self.assertEqual(empty["code"], "VALIDATION_ERROR", msg=empty)
			for result in (
				get_department(f"{PREFIX} Missing"),
				update_department(f"{PREFIX} Missing", phone="+251111000003"),
				deactivate_department(f"{PREFIX} Missing"),
			):
				self.assertEqual(result["code"], "NOT_FOUND", msg=result)
			for result in (
				get_department(" "),
				update_department(" ", phone="1"),
				deactivate_department(" "),
			):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn("department", result["details"])

	def test_department_with_open_cases_cannot_be_retired(self):
		department = self._create()
		department_id = department["department_id"]
		grievance = a_grievance()
		frappe.db.set_value(
			"Grievance",
			grievance.name,
			{"assigned_dept": department_id, "status": C.STATE_IN_PROGRESS},
			update_modified=False,
		)
		with _keep_transaction():
			for result in (
				deactivate_department(department_id),
				update_department(department_id, active=False),
			):
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=result)
				self.assertIn("open case", result["message"])
			self.assertEqual(frappe.db.get_value("Grievance Department", department_id, "active"), 1)
			# Other edits stay allowed while the department is busy.
			self.assertEqual(update_department(department_id, phone="+251111000004")["status"], "success")

		frappe.db.set_value("Grievance", grievance.name, "status", C.STATE_CLOSED, update_modified=False)
		self.assertFalse(deactivate_department(department_id)["data"]["department"]["active"])

	def test_only_admins_can_manage_departments(self):
		department = self._create()
		department_id = department["department_id"]
		officer = _user("stg424-officer@example.com", "Plain Officer")
		admin = _user("stg424-grievance-admin@example.com", "Selam Admin", role="Grievance Admin")
		for user in (officer, "Guest"):
			frappe.set_user(user)
			with _keep_transaction():
				for result in (
					list_departments(),
					get_department(department_id),
					create_department(department_name=f"{PREFIX} Denied", email_account="a@example.com"),
					update_department(department_id, phone="+251111000005"),
					deactivate_department(department_id),
					# A bad body from a non-admin is still 403, not a validation error.
					create_department(department_name=" "),
					update_department(department_id, nodal="x"),
				):
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
		frappe.set_user(admin)
		self.assertEqual(list_departments(q=PREFIX)["status"], "success")
		self.assertEqual(update_department(department_id, phone="+251111000006")["status"], "success")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Grievance Department", department_id, "phone"), "+251111000006")

	def test_routing_preferences_make_the_department_usable_by_category_assignments(self):
		department = self._create(
			l1_role_level="nodal_officer",
			l2_role_level="senior_nodal_officer",
			routing_strategy="Round Robin",
		)
		self.assertEqual(department["l1_role_level"], "nodal_officer")
		self.assertEqual(department["routing_strategy"], "Round Robin")
		updated = update_department(department["department_id"], routing_strategy=None, l2_role_level=None)
		self.assertIsNone(updated["data"]["department"]["routing_strategy"])
		self.assertIsNone(updated["data"]["department"]["l2_role_level"])
		with _keep_transaction():
			unknown = update_department(department["department_id"], l1_role_level="no_such_level")
			self.assertEqual(unknown["code"], "VALIDATION_ERROR", msg=unknown)

	def test_department_validation_applies_outside_the_api(self):
		doc = frappe.get_doc(
			{
				"doctype": "Grievance Department",
				"dept_name": f"{PREFIX} Direct",
				"email_account": "bad address",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert()
		doc.email_account = "direct@example.com"
		doc.head_of_dept = _user("stg424-nobody@example.com", "No Roles", role="Grievance Submitter")
		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_department_head_is_the_dept_head_recipient_when_no_desk_holds_the_level(self):
		department = self._create(head_of_dept=self.head)
		grievance = frappe._dict(
			assigned_dept=department["department_id"], administrative_area=None, name="STG424-CASE"
		)
		self.assertEqual(
			notifications.resolve_recipient(grievance, notifications.RECIPIENT_DEPARTMENT_HEAD), self.head
		)
		update_department(department["department_id"], head_of_dept=self.other_head)
		self.assertEqual(
			notifications.resolve_recipient(grievance, notifications.RECIPIENT_DEPARTMENT_HEAD),
			self.other_head,
		)
		update_department(department["department_id"], head_of_dept=None)
		self.assertIsNone(notifications.resolve_recipient(grievance, notifications.RECIPIENT_DEPARTMENT_HEAD))
		# Only the Dept Head role reads it: the department mailbox is still the Department Officer's.
		self.assertEqual(
			notifications.resolve_recipient(grievance, notifications.RECIPIENT_DEPARTMENT_OFFICER),
			"stg424@example.com",
		)


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
