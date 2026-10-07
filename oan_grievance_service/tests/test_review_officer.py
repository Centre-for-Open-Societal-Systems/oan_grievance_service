# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""The Grievance Review Officer: read-only oversight that is refused every mutation."""

import frappe
from frappe.tests.utils import FrappeTestCase
from oan_auth_service.api.router import _rules

from oan_grievance_service import permissions
from oan_grievance_service.api.router import ensure_routes_registered
from oan_grievance_service.api.v1 import attachment, change_request, draft, grievance, submitter
from oan_grievance_service.api.v1.category_assignment import (
	create_assignment,
	deactivate_assignment,
	get_assignment,
	list_assignments,
	update_assignment,
)
from oan_grievance_service.api.v1.officer import (
	create_officer,
	get_officer,
	list_officers,
	reset_temporary_password,
	update_officer,
)
from oan_grievance_service.api.v1.officer_statistics import list_officer_statistics
from oan_grievance_service.api.v1.response_config import create_response_template
from oan_grievance_service.services import constants as C
from oan_grievance_service.setup.install import ROLES
from oan_grievance_service.tests.fixtures import a_grievance, discard_grievance
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_role_level,
	_user,
)

PASSWORD = "Temp-Pass-12345"


class TestReviewOfficer(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.l1 = _user("stg434-l1@example.com", "Tigist Alemu")
		self.reviewer = _user("stg434-reviewer@example.com", "Rahel Review", role=C.ROLE_REVIEW_OFFICER)
		self.reviewer_admin = _roles_user(
			"stg434-rev-admin@example.com", C.ROLE_REVIEW_OFFICER, "Grievance Admin"
		)
		self.reviewer_officer = _roles_user(
			"stg434-rev-officer@example.com", C.ROLE_REVIEW_OFFICER, "Grievance Officer"
		)
		self.category = _category("STG434 Inputs", "Z43")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		self.department = _department("STG434 Agency", "S434")
		for name in frappe.get_all(
			"Grievance RBAC Assignment", filters={"category_scope": self.category}, pluck="name"
		):
			frappe.delete_doc("Grievance RBAC Assignment", name, force=1, ignore_permissions=True)
		created = create_assignment(
			service_category=self.category, department="S434", l1_officer=self.l1, sla_days=10
		)
		self.assignment = created["data"]["assignment"]["name"]
		self.made = []
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in self.made:
			discard_grievance(name)

	def _case(self, **overrides):
		doc = a_grievance(**overrides)
		self.made.append(doc.name)
		return doc

	def test_role_is_seeded_without_a_write_permission_anywhere(self):
		self.assertIn((C.ROLE_REVIEW_OFFICER, 0), ROLES)
		self.assertTrue(frappe.db.exists("Role", C.ROLE_REVIEW_OFFICER))
		rows = frappe.get_all(
			"DocPerm",
			filters={"role": C.ROLE_REVIEW_OFFICER},
			fields=["parent", "write", "create", "delete", "submit", "cancel", "amend", "share"],
		)
		self.assertTrue(rows)
		for row in rows:
			for right in ("write", "create", "delete", "submit", "cancel", "amend", "share"):
				self.assertFalse(row[right], msg=f"{row.parent} grants {right}")

	def test_reads_administration_data_and_officer_statistics(self):
		frappe.set_user(self.reviewer)
		for result in (
			list_assignments(service_category=self.category),
			get_assignment(self.assignment),
			list_officers(),
			get_officer(self.l1),
			list_officer_statistics(),
		):
			self.assertEqual(result["status"], "success", msg=result)

	def test_reads_grievances_and_the_departments_options(self):
		case = self._case()
		frappe.set_user(self.reviewer)
		listed = grievance.list_grievances(page_size=100, sort_order="desc")
		self.assertEqual(listed["status"], "success", msg=listed)
		self.assertIn(case.name, {row["name"] for row in listed["data"]["items"]})
		self.assertEqual(grievance.summary()["status"], "success")
		options = grievance.options()
		self.assertEqual(options["status"], "success", msg=options)
		self.assertTrue(options["data"]["departments"])
		timeline = grievance.timeline(case.ticket_number)
		self.assertEqual(timeline["status"], "success", msg=timeline)
		self.assertEqual(attachment.get_attachments(ticket_number=case.ticket_number)["status"], "success")

	def test_cannot_change_administration_data(self):
		frappe.set_user(self.reviewer)
		with _keep_transaction():
			for result in self._admin_writes():
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
		self.assertEqual(frappe.db.get_value("Grievance RBAC Assignment", self.assignment, "active"), 1)

	def test_cannot_act_on_a_grievance(self):
		case = self._case()
		frappe.set_user(self.reviewer)
		with _keep_transaction():
			for result in self._case_writes(case.ticket_number):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_review_officer_wins_over_an_admin_role(self):
		frappe.set_user(self.reviewer_admin)
		with _keep_transaction():
			for result in self._admin_writes():
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)
		self.assertEqual(frappe.db.get_value("Grievance RBAC Assignment", self.assignment, "active"), 1)
		self.assertEqual(list_assignments()["status"], "success")

	def test_review_officer_wins_over_an_officer_role(self):
		case = self._case()
		frappe.set_user(self.reviewer_officer)
		with _keep_transaction():
			for result in self._case_writes(case.ticket_number):
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=result)

	def test_grievance_documents_are_readable_but_never_writable(self):
		case = self._case()
		draft_case = self._case(workflow_state=C.STATE_DRAFT)
		for user in (self.reviewer, self.reviewer_admin, self.reviewer_officer):
			self.assertTrue(permissions.has_grievance_permission(case, "read", user))
			for ptype in ("write", "create", "delete", "submit", "cancel"):
				self.assertFalse(permissions.has_grievance_permission(case, ptype, user), msg=ptype)
		# Someone's unsent draft is not part of oversight.
		self.assertFalse(permissions.has_grievance_permission(draft_case, "read", self.reviewer))

	def test_grievance_list_condition_hides_drafts_only(self):
		self.assertEqual(
			permissions.grievance_query_conditions(self.reviewer), "(`tabGrievance`.docstatus != 0)"
		)

	def test_the_administrator_account_is_not_read_only(self):
		self.assertFalse(permissions.is_read_only("Administrator"))
		self.assertTrue(permissions.is_read_only(self.reviewer))
		self.assertFalse(permissions.is_read_only(self.l1))

	def test_every_mutating_route_forbids_the_review_officer(self):
		"""A new POST, PATCH or DELETE that forgets `@forbid_read_only` fails here."""
		ensure_routes_registered()
		read_methods = {"GET", "HEAD", "OPTIONS"}
		missing = []
		checked = 0
		for rule in _rules:
			route = getattr(rule.endpoint, "_route", None)
			fn = getattr(rule.endpoint, "__wrapped__", None)
			if not route or not fn or not fn.__module__.startswith("oan_grievance_service."):
				continue
			if set(route["methods"]) <= read_methods:
				continue
			checked += 1
			if not getattr(fn, "forbids_read_only", False):
				missing.append(
					f"{'/'.join(route['methods'])} {route['path']} ({fn.__module__}.{fn.__name__})"
				)
		self.assertGreater(checked, 20)
		self.assertEqual(missing, [], msg="Mutating routes that do not refuse the Review Officer")

	def _admin_writes(self):
		return (
			create_assignment(
				service_category=self.category, department=self.department, l1_officer=self.l1, sla_days=6
			),
			update_assignment(self.assignment, sla_days=9),
			deactivate_assignment(self.assignment),
			create_officer(
				full_name="Blocked Officer",
				designation="Officer",
				level="L1",
				department=self.department,
				email="stg434-new@example.com",
				service_categories=[self.category],
				temporary_password=PASSWORD,
			),
			update_officer(self.l1, full_name="Renamed"),
			reset_temporary_password(officer=self.l1, temporary_password=PASSWORD),
			create_response_template(title="Blocked", reason="Done"),
		)

	def _case_writes(self, ticket_number):
		return (
			grievance.action(ticket_number=ticket_number, action="Resolve", reason="Resolved"),
			grievance.message(ticket_number=ticket_number, body="A note"),
			grievance.feedback(ticket_number=ticket_number, rating=5),
			grievance.reassign(ticket_number=ticket_number, target_department=self.department),
			grievance.defer_sla(ticket_number=ticket_number, additional_days=2, reason="Waiting"),
			attachment.delete(attachment_id="none"),
			draft.discard(client_submission_uuid="00000000-0000-4000-8000-000000000000"),
			submitter.unblock_submitter("none"),
			change_request.decide("none", decision="approved"),
		)


def _roles_user(email, *roles):
	if frappe.db.exists("User", email):
		return email
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"enabled": 1,
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)
	return email
