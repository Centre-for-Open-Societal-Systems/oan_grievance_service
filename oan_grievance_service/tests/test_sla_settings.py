# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from oan_grievance_service.api.v1.category_assignment import create_assignment, update_assignment
from oan_grievance_service.api.v1.sla_settings import get_global_policy, update_global_policy
from oan_grievance_service.grievance_sla.doctype.grievance_deferral_policy.grievance_deferral_policy import (
	max_deferral_days,
	requires_supervisor_approval,
)
from oan_grievance_service.services import classification, sla
from oan_grievance_service.services import constants as C
from oan_grievance_service.tests.fixtures import a_grievance, discard_grievance
from oan_grievance_service.tests.test_category_assignment import (
	_category,
	_department,
	_keep_transaction,
	_role_level,
	_user,
)

POLICY = "Grievance Deferral Policy"


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

	# Global policy
	# -------------

	def test_global_policy_read_and_partial_update(self):
		result = get_global_policy()
		self.assertEqual(result["status"], "success", msg=result)
		policy = result["data"]["policy"]
		self.assertEqual(
			set(policy),
			{"max_deferral_days", "requires_supervisor_approval", "modified"},
		)

		updated = update_global_policy(max_deferral_days=21, requires_supervisor_approval=False)
		self.assertEqual(updated["status"], "success", msg=updated)
		self.assertEqual(
			{key: updated["data"]["policy"][key] for key in list(policy)[:2]},
			{"max_deferral_days": 21, "requires_supervisor_approval": False},
		)

		# What the API saved is what the engine reads, with no cache in between.
		self.assertEqual(max_deferral_days(), 21)
		self.assertFalse(requires_supervisor_approval())
		self.assertEqual(frappe.db.get_single_value(POLICY, "requires_supervisor_approval"), 0)

		# Omitted fields stay.
		partial = update_global_policy(requires_supervisor_approval=True)
		self.assertEqual(partial["data"]["policy"]["requires_supervisor_approval"], True)
		self.assertEqual(partial["data"]["policy"]["max_deferral_days"], 21)
		self.assertTrue(requires_supervisor_approval())
		self.assertEqual(get_global_policy()["data"]["policy"]["requires_supervisor_approval"], True)

	def test_global_policy_untouched_single_reads_as_defaults(self):
		frappe.db.delete("Singles", {"doctype": POLICY})
		frappe.clear_document_cache(POLICY, POLICY)
		policy = get_global_policy()["data"]["policy"]
		self.assertEqual(policy["max_deferral_days"], C.DEFAULT_MAX_DEFERRAL_DAYS)
		self.assertTrue(policy["requires_supervisor_approval"])

	def test_global_policy_rejects_bad_input(self):
		with _keep_transaction():
			for kwargs in (
				{},
				{"max_deferral_days": 0},
				{"max_deferral_days": "many"},
				# Removed in STG-516: an unknown field like any other.
				{"auto_escalation_threshold": 80},
				{"requires_supervisor_approval": "nobody"},
				{"deferral_approval": "l2_approval"},
			):
				result = update_global_policy(**kwargs)
				self.assertEqual(result["code"], "VALIDATION_ERROR", msg=(kwargs, result))

	# Per-category settings
	# ---------------------

	def test_the_desk_holds_the_flags_and_the_category_the_days(self):
		assignment = self._assign(sla_days=14)
		self.assertEqual((assignment["sla_days"], assignment["auto_escalate"]), (14, True))
		self.assertTrue(assignment["notify_on_breach"])

		updated = update_assignment(assignment["name"], sla_days=9, auto_escalate=False)
		self.assertEqual(updated["status"], "success", msg=updated)
		shown = updated["data"]["assignment"]
		self.assertEqual((shown["sla_days"], shown["auto_escalate"]), (9, False))
		# The days are the category's base rule's; the flag stays on that one desk.
		self.assertEqual(classification.sla_days_for(self.category), 9)
		other = self._assign(department=self.other_department, sla_days=9)
		self.assertTrue(other["auto_escalate"])

	def test_roles(self):
		admin = _user("stg412-admin@example.com", "Selam Admin", role="Grievance Admin")
		reviewer = _user("stg412-review@example.com", "Dawit Review", role="Grievance Review Officer")
		officer = _user("stg412-officer@example.com", "Almaz Officer")

		frappe.set_user(admin)
		self.assertEqual(update_global_policy(max_deferral_days=12)["status"], "success")

		for user, can_read in ((reviewer, True), (officer, False), ("Guest", False)):
			frappe.set_user(user)
			with _keep_transaction():
				result = get_global_policy()
				self.assertEqual(result["status"] == "success", can_read, msg=(user, result))
				if not can_read:
					self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
				result = update_global_policy(max_deferral_days=99)
				self.assertEqual(result["code"], "PERMISSION_DENIED", msg=(user, result))
		frappe.set_user("Administrator")
		self.assertEqual(max_deferral_days(), 12)

	# What the engine does with them
	# ------------------------------

	def test_the_policy_is_the_only_deferral_ceiling(self):
		"""max_deferral_days is the one limit on a deferral's length: no code cap sits above it."""
		updated = update_global_policy(max_deferral_days=45)
		self.assertEqual(updated["status"], "success", msg=updated)
		self.assertEqual(max_deferral_days(), 45)

	def test_cap_patch_brings_a_ceiling_above_30_down_to_30(self):
		from oan_grievance_service.patches import cap_max_deferral_days

		for configured, expected in ((45, 30), (21, 21)):
			frappe.db.set_single_value(POLICY, "max_deferral_days", configured)
			cap_max_deferral_days.execute()
			frappe.clear_document_cache(POLICY, POLICY)
			self.assertEqual(max_deferral_days(), expected)

	def test_notify_on_breach_gates_the_breach_notification(self):
		desk = self._assign()["name"]
		officer = self.l1
		grievance_type = frappe.get_doc(
			{
				"doctype": "Grievance Type",
				"type_name": "STG412 Late delivery",
				"service_category": self.category,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		grievance = a_grievance(
			service_category=self.category,
			grievance_type=grievance_type.name,
			assigned_to=officer,
			routing_rule=desk,
		)
		self.addCleanup(discard_grievance, grievance.name)

		for notify, queued in ((True, 1), (False, 0)):
			update_assignment(desk, notify_on_breach=notify)
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

	def test_seed_base_sla_rules_creates_baseline_rules_idempotently(self):
		from oan_grievance_service.setup.install import DEFAULT_BASE_SLA_DAYS, seed_base_sla_rules

		# Ensure standard categories exist
		for idx, (category, _days) in enumerate(DEFAULT_BASE_SLA_DAYS):
			if not frappe.db.exists("Grievance Service Category", category):
				frappe.get_doc(
					{
						"doctype": "Grievance Service Category",
						"category_name": category,
						"code": f"T{idx:02d}",
						"is_active": 1,
					}
				).insert(ignore_permissions=True)

		seed_base_sla_rules()
		for category, _days in DEFAULT_BASE_SLA_DAYS:
			self.assertGreater(classification.sla_days_for(category) or 0, 0, msg=category)

		# Second run is idempotent (no duplicates created)
		self.assertEqual(seed_base_sla_rules(), [])

		# A fresh category without a base rule gets one
		fresh_cat = _category("STG412 Seed Category", "Z99")
		with patch("oan_grievance_service.setup.install.DEFAULT_BASE_SLA_DAYS", [(fresh_cat, 11)]):
			self.assertIn(fresh_cat, seed_base_sla_rules())
			self.assertEqual(classification.sla_days_for(fresh_cat), 11)
			self.assertEqual(seed_base_sla_rules(), [])
