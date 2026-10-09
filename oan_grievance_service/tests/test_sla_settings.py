# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.category_assignment import create_assignment, get_assignment
from oan_grievance_service.api.v1.sla_settings import (
	get_global_policy,
	list_sla_configurations,
	update_global_policy,
	update_sla_configuration,
)
from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
	auto_escalation_threshold,
	max_deferral_days,
	requires_supervisor_approval,
)
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import sla
from oan_grievance_service.tests.fixtures import a_grievance, discard_grievance
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_role_level,
	_user,
)

POLICY = "Grievance Deferral Policy"
SLA_DOCTYPE = "Grievance SLA Configuration"


class TestSlaSettings(FrappeTestCase):
	def setUp(self):
		# FrappeTestCase rolls back per class, and every test here makes the same names.
		frappe.db.rollback()
		frappe.set_user("Administrator")
		_role_level("nodal_officer", 10)
		_role_level("senior_nodal_officer", 20)
		self.l1 = _user("stg412-l1@example.com", "Tigist Alemu")
		self.category = _category("STG412 Inputs", "Z95")
		self.other_category = _category("STG412 Markets", "Z96")
		self.department = _department("STG412 Inputs Agency", "S412")
		self.other_department = _department("STG412 Markets Bureau", "S413")
		for category in (self.category, self.other_category):
			for name in frappe.get_all(SLA_DOCTYPE, filters={"service_category": category}, pluck="name"):
				frappe.delete_doc(SLA_DOCTYPE, name, force=1, ignore_permissions=True)
			for name in frappe.get_all(
				"Grievance RBAC Assignment", filters={"category_scope": category}, pluck="name"
			):
				frappe.delete_doc("Grievance RBAC Assignment", name, force=1, ignore_permissions=True)
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		# The Single is cached across requests; a rollback does not reach the cache.
		frappe.clear_document_cache(POLICY, POLICY)
		frappe.clear_messages()

	def _assign(self, category=None, department=None, sla_days=14, **extra):
		created = create_assignment(
			service_category=category or self.category,
			department=department or self.department,
			l1_officer=self.l1,
			sla_days=sla_days,
			**extra,
		)
		self.assertEqual(created["status"], "success", msg=created)
		return created["data"]["assignment"]

	def _config(self, category=None):
		"""The category's SLA configuration as the list endpoint returns it."""
		result = list_sla_configurations(service_category=category or self.category)
		self.assertEqual(result["status"], "success", msg=result)
		rows = result["data"]["sla_configurations"]
		self.assertEqual(len(rows), 1, msg=rows)
		return rows[0]

	# Global policy
	# -------------

	def test_global_policy_read_and_partial_update(self):
		result = get_global_policy()
		self.assertEqual(result["status"], "success", msg=result)
		policy = result["data"]["policy"]
		self.assertEqual(
			set(policy),
			{"max_deferral_days", "auto_escalation_threshold", "requires_supervisor_approval", "modified"},
		)

		updated = update_global_policy(
			max_deferral_days=21, auto_escalation_threshold=80, requires_supervisor_approval=False
		)
		self.assertEqual(updated["status"], "success", msg=updated)
		self.assertEqual(
			{key: updated["data"]["policy"][key] for key in list(policy)[:3]},
			{
				"max_deferral_days": 21,
				"auto_escalation_threshold": 80,
				"requires_supervisor_approval": False,
			},
		)

		# What the API saved is what the engine reads, with no cache in between.
		self.assertEqual(max_deferral_days(), 21)
		self.assertEqual(auto_escalation_threshold(), 80)
		self.assertFalse(requires_supervisor_approval())
		self.assertEqual(frappe.db.get_single_value(POLICY, "requires_supervisor_approval"), 0)

		# Omitted fields stay.
		partial = update_global_policy(requires_supervisor_approval=True)
		self.assertEqual(partial["data"]["policy"]["requires_supervisor_approval"], True)
		self.assertEqual(partial["data"]["policy"]["max_deferral_days"], 21)
		self.assertEqual(partial["data"]["policy"]["auto_escalation_threshold"], 80)
		self.assertTrue(requires_supervisor_approval())
		self.assertEqual(get_global_policy()["data"]["policy"]["requires_supervisor_approval"], True)

	def test_global_policy_untouched_single_reads_as_defaults(self):
		frappe.db.delete("Singles", {"doctype": POLICY})
		frappe.clear_document_cache(POLICY, POLICY)
		policy = get_global_policy()["data"]["policy"]
		self.assertEqual(policy["max_deferral_days"], C.DEFAULT_MAX_DEFERRAL_DAYS)
		self.assertEqual(policy["auto_escalation_threshold"], C.DEFAULT_ESCALATION_THRESHOLD)
		self.assertTrue(policy["requires_supervisor_approval"])

	def test_global_policy_rejects_bad_input(self):
		with _keep_transaction():
			for kwargs in (
				{},
				{"max_deferral_days": 0},
				{"max_deferral_days": "many"},
				{"auto_escalation_threshold": 0},
				{"auto_escalation_threshold": 101},
				{"requires_supervisor_approval": "nobody"},
				{"deferral_approval": "l2_approval"},
			):
				result = update_global_policy(**kwargs)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(kwargs, result))

	# Per-category settings
	# ---------------------

	def test_category_settings_are_the_category_assignment_values(self):
		assignment = self._assign(sla_days=14, auto_escalate=True)
		config = self._config()
		self.assertEqual(config["service_category"], self.category)
		self.assertEqual(config["departments"], [self.department])
		self.assertEqual(config["sla_days"], 14)
		self.assertTrue(config["auto_escalate"])
		self.assertTrue(config["notify_on_breach"])

		# Written here, read there.
		updated = update_sla_configuration(config["name"], sla_days=9, auto_escalate=False)
		self.assertEqual(updated["status"], "success", msg=updated)
		record = updated["data"]["sla_configuration"]
		self.assertEqual((record["sla_days"], record["auto_escalate"]), (9, False))
		self.assertTrue(record["notify_on_breach"])
		shown = get_assignment(assignment["name"])["data"]["assignment"]
		self.assertEqual((shown["sla_days"], shown["auto_escalate"]), (9, False))

		# Written there, read here, on the same record.
		from oan_grievance_service.api.v1.category_assignment import update_assignment

		self.assertEqual(
			update_assignment(assignment["name"], sla_days=6, auto_escalate=True)["status"], "success"
		)
		again = self._config()
		self.assertEqual(again["name"], config["name"])
		self.assertEqual((again["sla_days"], again["auto_escalate"]), (6, True))
		self.assertEqual(frappe.db.count(SLA_DOCTYPE, {"service_category": self.category}), 1)

	def test_departments_serving_a_category_share_one_row(self):
		self._assign(sla_days=14)
		self._assign(department=self.other_department, sla_days=10)
		config = self._config()
		self.assertEqual(config["departments"], sorted([self.department, self.other_department]))
		self.assertEqual(config["sla_days"], 10)

	def test_update_changes_only_what_was_sent(self):
		self._assign(sla_days=14)
		config = self._config()
		result = update_sla_configuration(config["name"], notify_on_breach=False)
		self.assertEqual(result["status"], "success", msg=result)
		record = result["data"]["sla_configuration"]
		self.assertEqual((record["sla_days"], record["auto_escalate"]), (14, True))
		self.assertFalse(record["notify_on_breach"])
		self.assertEqual(frappe.db.get_value(SLA_DOCTYPE, config["name"], "notify_on_breach"), 0)

	def test_update_rejects_bad_input_and_unknown_configs(self):
		self._assign()
		name = self._config()["name"]
		with _keep_transaction():
			for kwargs in (
				{},
				{"sla_days": 0},
				{"sla_days": "soon"},
				{"auto_escalate": "maybe"},
				{"service_category": self.other_category},
				{"auto_escalation_threshold": 50},
			):
				result = update_sla_configuration(name, **kwargs)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(kwargs, result))
			self.assertEqual(update_sla_configuration("GR-SLA-9999", sla_days=3)["code"], "NOT_FOUND")
		self.assertEqual(self._config()["sla_days"], 14)

	def test_inactive_configuration_is_not_served(self):
		self._assign()
		name = self._config()["name"]
		frappe.db.set_value(SLA_DOCTYPE, name, "active", 0)
		self.assertEqual(
			list_sla_configurations(service_category=self.category)["data"]["sla_configurations"], []
		)
		with _keep_transaction():
			self.assertEqual(update_sla_configuration(name, sla_days=3)["code"], "NOT_FOUND")

	def test_list_filters_and_paging(self):
		self._assign(sla_days=14)
		self._assign(category=self.other_category, department=self.other_department, sla_days=10)

		by_department = list_sla_configurations(department=self.other_department)
		rows = by_department["data"]["sla_configurations"]
		self.assertEqual([row["service_category"] for row in rows], [self.other_category])

		both = list_sla_configurations(service_category=self.category, department=self.other_department)
		self.assertEqual(both["data"]["sla_configurations"], [])

		page = list_sla_configurations(page=1, page_size=1)["data"]
		self.assertEqual(len(page["sla_configurations"]), 1)
		self.assertGreaterEqual(page["pagination"]["total_count"], 2)

		with _keep_transaction():
			self.assertEqual(list_sla_configurations(page=0)["code"], "VALIDATION_ERROR")
			self.assertEqual(list_sla_configurations(unknown="x")["code"], "VALIDATION_ERROR")
			self.assertEqual(list_sla_configurations(department="Nope")["code"], "VALIDATION_ERROR")

	def test_roles(self):
		self._assign()
		name = self._config()["name"]
		admin = _user("stg412-admin@example.com", "Selam Admin", role="Grievance Admin")
		reviewer = _user("stg412-review@example.com", "Dawit Review", role="Grievance Review Officer")
		officer = _user("stg412-officer@example.com", "Almaz Officer")

		frappe.set_user(admin)
		self.assertEqual(update_global_policy(max_deferral_days=12)["status"], "success")
		self.assertEqual(update_sla_configuration(name, sla_days=8)["status"], "success")

		for user, can_read in ((reviewer, True), (officer, False), ("Guest", False)):
			frappe.set_user(user)
			with _keep_transaction():
				for result in (get_global_policy(), list_sla_configurations()):
					self.assertEqual(result["status"] == "success", can_read, msg=(user, result))
					if not can_read:
						self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
				for result in (
					update_global_policy(max_deferral_days=99),
					update_sla_configuration(name, sla_days=99),
				):
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
		frappe.set_user("Administrator")
		self.assertEqual(max_deferral_days(), 12)
		self.assertEqual(frappe.db.get_value(SLA_DOCTYPE, name, "sla_days"), 8)

	# What the engine does with them
	# ------------------------------

	def test_global_threshold_applies_unless_the_category_sets_its_own(self):
		self._assign()
		name = self._config()["name"]
		self.assertEqual(sla.resolve_policy(self.category).auto_escalation_threshold, 100)

		update_global_policy(auto_escalation_threshold=70)
		self.assertEqual(sla.resolve_policy(self.category).auto_escalation_threshold, 70)

		frappe.db.set_value(SLA_DOCTYPE, name, "auto_escalation_threshold", 90)
		self.assertEqual(sla.resolve_policy(self.category).auto_escalation_threshold, 90)

		update_global_policy(auto_escalation_threshold=100)
		frappe.db.set_value(SLA_DOCTYPE, name, "auto_escalation_threshold", 0)
		self.assertEqual(sla.resolve_policy(self.category).auto_escalation_threshold, 100)

	def test_notify_on_breach_gates_the_breach_notification(self):
		self._assign()
		name = self._config()["name"]
		officer = _user("stg412-assignee@example.com", "Hana Assignee")
		grievance_type = frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "STG412 Late delivery",
				"service_category": self.category,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		grievance = a_grievance(
			service_category=self.category, grievance_type=grievance_type.name, assigned_to=officer
		)
		self.addCleanup(discard_grievance, grievance.name)

		for notify, queued in ((True, 1), (False, 0)):
			update_sla_configuration(name, notify_on_breach=notify)
			grievance.db_set("escalated", 0)
			grievance.reload()
			with (
				patch.object(sla, "higher_authority_of", return_value=(None, None)),
				patch("oan_grievance_service.services.notifications.queue") as queue,
			):
				self.assertTrue(sla.escalate(grievance))
			self.assertEqual(queue.call_count, queued, msg=f"notify_on_breach={notify}")
			grievance.reload()
			# Escalation itself does not depend on the flag.
			self.assertEqual(grievance.escalated, 1)
