# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from oan_grievance_service.api.v1.category_assignment import create_assignment
from oan_grievance_service.api.v1.officer_statistics import list_officer_statistics
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_grievance, discard_grievance
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_role_level,
	_user,
)


class TestOfficerStatistics(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.l1 = _user("stg431-l1@example.com", "Hana Bekele")
		self.l2 = _user("stg431-l2@example.com", "Dawit Assefa")
		self.category = _category("STG431 Inputs", "Z95")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		self.department = _department("STG431 Agency", "S431")
		for name in frappe.get_all(
			"Grievance RBAC Assignment", filters={"category_scope": self.category}, pluck="name"
		):
			frappe.delete_doc("Grievance RBAC Assignment", name, force=1, ignore_permissions=True)
		create_assignment(
			service_category=self.category,
			department="S431",
			l1_officer=self.l1,
			l2_officer=self.l2,
			sla_days=10,
		)
		self.made = []
		frappe.clear_messages()

	def tearDown(self):
		for name in self.made:
			discard_grievance(name)

	def _case(self, officer, state=C.STATE_ASSIGNED, hours=None, department=None):
		doc = a_grievance()
		self.made.append(doc.name)
		# Closed and Rejected are docstatus 2 in the workflow, as in production.
		docstatus = 2 if state in (C.STATE_CLOSED, C.STATE_REJECTED) else 1
		values = {"assigned_to": officer, "workflow_state": state, "docstatus": docstatus}
		values["assigned_dept"] = department or self.department
		if hours is not None:
			values["resolved_at"] = add_to_date(doc.creation, hours=hours)
		frappe.db.set_value("Grievance", doc.name, values, update_modified=False)
		return doc

	def _stats(self, **filters):
		data = list_officer_statistics(department="S431", **filters)["data"]
		return {row["user"]: row for row in data["officers"]}, data

	def test_figures_are_computed_from_grievances(self):
		self._case(self.l1, C.STATE_RESOLVED, hours=10)
		self._case(self.l1, C.STATE_CLOSED, hours=20)
		self._case(self.l1, C.STATE_IN_PROGRESS)
		self._case(self.l1, C.STATE_REJECTED)
		stats, _ = self._stats()
		row = stats[self.l1]
		self.assertEqual(row["level"], "L1")
		self.assertEqual(row["full_name"], "Hana Bekele")
		self.assertEqual(row["assigned"], 4)
		self.assertEqual(row["resolved"], 2)
		self.assertEqual(row["resolution_rate"], 50.0)
		self.assertAlmostEqual(row["avg_resolution_hours"], 15.0, places=1)

	def test_officer_without_cases_reports_zero(self):
		stats, _ = self._stats()
		row = stats[self.l2]
		self.assertEqual(row["level"], "L2")
		self.assertEqual((row["assigned"], row["resolved"], row["resolution_rate"]), (0, 0, 0.0))
		self.assertIsNone(row["avg_resolution_hours"])

	def test_reassigned_case_counts_for_current_officer_only(self):
		doc = self._case(self.l1)
		frappe.db.set_value("Grievance", doc.name, "assigned_to", self.l2, update_modified=False)
		stats, _ = self._stats()
		self.assertEqual(stats[self.l1]["assigned"], 0)
		self.assertEqual(stats[self.l2]["assigned"], 1)

	def test_level_filter_and_live_update(self):
		stats, data = self._stats(level="L2")
		self.assertEqual(set(stats), {self.l2})
		self.assertEqual(data["pagination"]["total_count"], 1)
		self._case(self.l2, C.STATE_RESOLVED, hours=5)
		stats, _ = self._stats(level="L2")
		self.assertEqual(stats[self.l2]["resolved"], 1)

	def test_inactive_desk_officers_are_excluded(self):
		desk = frappe.get_all(
			"Grievance RBAC Assignment", filters={"category_scope": self.category}, pluck="name"
		)[0]
		frappe.db.set_value("Grievance RBAC Assignment", desk, "active", 0, update_modified=False)
		stats, _ = self._stats()
		self.assertEqual(stats, {})

	def test_requires_admin_role(self):
		outsider = _user("stg431-officer@example.com", "Plain Officer")
		frappe.set_user(outsider)
		self.addCleanup(frappe.set_user, "Administrator")
		result = list_officer_statistics()
		self.assertNotEqual(result.get("status"), "success")

	def test_closed_and_rejected_cases_are_counted(self):
		self._case(self.l1, C.STATE_CLOSED, hours=8)
		self._case(self.l1, C.STATE_REJECTED)
		stats, _ = self._stats()
		row = stats[self.l1]
		self.assertEqual((row["assigned"], row["resolved"]), (2, 1))
		self.assertAlmostEqual(row["avg_resolution_hours"], 8.0, places=1)

	def test_department_filter_scopes_the_case_counts(self):
		other = _department("STG431 Other Agency", "S432")
		self._case(self.l1, C.STATE_RESOLVED, hours=4, department=self.department)
		self._case(self.l1, C.STATE_IN_PROGRESS, department=other)
		scoped, _ = self._stats()
		self.assertEqual(scoped[self.l1]["assigned"], 1)
		everything = list_officer_statistics()["data"]["officers"]
		total = next(row for row in everything if row["user"] == self.l1)
		self.assertEqual(total["assigned"], 2)
