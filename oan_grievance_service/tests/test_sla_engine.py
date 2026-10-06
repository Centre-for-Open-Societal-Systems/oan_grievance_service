# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from datetime import datetime

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from oan_grievance_service.api.v1.change_request import raise_change_request
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import lifecycle, sla
from oan_grievance_service.tasks import escalate_breached, send_sla_reminders
from oan_grievance_service.tests.fixtures import (
	a_department,
	a_grievance,
	a_grievance_type,
	a_leaf_area,
	a_service_category,
)


class TestSLAEngineAndCategoryRecalculation(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.db.rollback()

		# Category A (5 days SLA)
		self.cat_a = "CatSLA_A"
		if not frappe.db.exists("Grievance Service Category", self.cat_a):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": self.cat_a,
					"code": "A01",
					"sort_order": 10,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		# Category B (10 days SLA)
		self.cat_b = "CatSLA_B"
		if not frappe.db.exists("Grievance Service Category", self.cat_b):
			frappe.get_doc(
				{
					"doctype": "Grievance Service Category",
					"category_name": self.cat_b,
					"code": "B01",
					"sort_order": 11,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		# Grievance types
		if not frappe.db.exists("Grievance Type", {"service_category": self.cat_a}):
			self.gtype_a = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Type A",
					"service_category": self.cat_a,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			self.gtype_a = frappe.get_doc(
				"Grievance Type", {"service_category": self.cat_a, "type_name": "Type A"}
			)

		if not frappe.db.exists("Grievance Type", {"service_category": self.cat_b}):
			self.gtype_b = frappe.get_doc(
				{
					"doctype": "Grievance Type",
					"type_name": "Type B",
					"service_category": self.cat_b,
					"is_active": 1,
				}
			).insert(ignore_permissions=True)
		else:
			self.gtype_b = frappe.get_doc(
				"Grievance Type", {"service_category": self.cat_b, "type_name": "Type B"}
			)

		# Holiday list with Sundays off and specific holidays
		self.hl_name = f"Test Calendar {frappe.generate_hash(length=6)}"
		self.hlist = frappe.get_doc(
			{
				"doctype": "Grievance Holiday List",
				"holiday_list_name": self.hl_name,
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
				"weekly_off": "Sunday",
				"is_default": 0,
			}
		)
		self.hlist.append(
			"holidays",
			{
				"holiday_date": "2026-01-07",
				"description": "Christmas",
				"weekly_off": 0,
			},
		)
		self.hlist.insert(ignore_permissions=True)

		# Configure SLA policy for Category A (5 days, no holiday list)
		for name in frappe.get_all(
			"Grievance SLA Configuration", filters={"service_category": self.cat_a}, pluck="name"
		):
			frappe.delete_doc("Grievance SLA Configuration", name, force=True)

		self.sla_a = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_a,
				"sla_days": 5,
				"auto_escalate": 1,
				"auto_escalation_threshold": 100,
				"active": 1,
			}
		).insert(ignore_permissions=True)

		# Configure SLA policy for Category B (10 days, with holiday list)
		for name in frappe.get_all(
			"Grievance SLA Configuration", filters={"service_category": self.cat_b}, pluck="name"
		):
			frappe.delete_doc("Grievance SLA Configuration", name, force=True)

		self.sla_b = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_b,
				"sla_days": 10,
				"holiday_list": self.hl_name,
				"auto_escalate": 1,
				"auto_escalation_threshold": 80,
				"active": 1,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDown()

	def test_sla_clock_starts_from_creation_date(self):
		"""The SLA window starts from the creation date/time of the ticket."""
		created_time = datetime(2026, 1, 2, 10, 0, 0)
		g = a_grievance(
			service_category=self.cat_a,
			grievance_type=self.gtype_a.name,
			workflow_state="Submitted",
			status="Submitted",
		)
		g.db_set("creation", created_time, update_modified=False)
		g.reload()

		lifecycle.transition(g, "Assign")
		g.reload()

		self.assertEqual(get_datetime(g.sla_start_at), created_time)
		self.assertEqual(g.sla_days, 5)
		# 5 working days from Friday 2026-01-02 10:00:00 skipping Sunday (Jan 4) and Christmas (Jan 7) -> 2026-01-09 10:00:00
		self.assertEqual(get_datetime(g.sla_due_date), datetime(2026, 1, 9, 10, 0, 0))

	def test_category_change_recalculates_sla_deadline_from_creation(self):
		"""Changing category from A (5d) to B (10 working days) recalculates deadline from creation."""
		created_time = datetime(2026, 1, 2, 10, 0, 0)
		g = a_grievance(
			service_category=self.cat_a,
			grievance_type=self.gtype_a.name,
			workflow_state="Submitted",
			status="Submitted",
		)
		g.db_set("creation", created_time, update_modified=False)
		g.reload()

		lifecycle.transition(g, "Assign")
		g.reload()
		self.assertEqual(g.sla_days, 5)

		# Change category to Category B via Change Request
		raise_change_request(
			g,
			subject="Recategorize",
			changes={"service_category": self.cat_b, "grievance_type": self.gtype_b.name},
			reason="Misclassified at intake",
		)
		g.reload()

		self.assertEqual(g.service_category, self.cat_b)
		self.assertEqual(g.sla_days, 10)
		self.assertEqual(get_datetime(g.sla_start_at), created_time)

		# 10 working days from Friday 2026-01-02 skipping Sundays (Jan 4, Jan 11) and Christmas (Jan 7):
		# Day 1: Sat Jan 3
		# Sun Jan 4 (Skipped)
		# Day 2: Mon Jan 5
		# Day 3: Tue Jan 6
		# Wed Jan 7 Christmas (Skipped)
		# Day 4: Thu Jan 8
		# Day 5: Fri Jan 9
		# Day 6: Sat Jan 10
		# Sun Jan 11 (Skipped)
		# Day 7: Mon Jan 12
		# Day 8: Tue Jan 13
		# Day 9: Wed Jan 14
		# Day 10: Thu Jan 15 10:00:00
		expected_due = datetime(2026, 1, 15, 10, 0, 0)
		self.assertEqual(get_datetime(g.sla_due_date), expected_due)

		# Next escalation at threshold 80% of window (8 working days = Jan 13)
		self.assertIsNotNone(g.next_escalation_at)

	def test_category_change_preserves_banked_hold_time(self):
		"""Banked pause duration shifts the newly recalculated deadline forward."""
		created_time = datetime(2026, 1, 2, 10, 0, 0)
		g = a_grievance(
			service_category=self.cat_a,
			grievance_type=self.gtype_a.name,
			workflow_state="Assigned",
			status="Assigned",
		)
		g.db_set({"creation": created_time, "total_hold_time": 86400}, update_modified=False)  # 1 day banked
		g.reload()

		sla.start_clock(g)
		g.reload()

		# Recalculate on Category B (10 working days = 2026-01-15 10:00:00 + 1 day banked hold = 2026-01-16 10:00:00)
		sla.recalculate_sla_on_category_change(g, new_category=self.cat_b)
		g.reload()

		expected_due = datetime(2026, 1, 16, 10, 0, 0)
		self.assertEqual(get_datetime(g.sla_due_date), expected_due)

	def test_consumed_percent_calculated_hourly_with_upper_limit(self):
		"""SLA consumed percentage is calculated hourly taking the ceiling (upper limit)."""
		# 100 hours total window, 4h 59m elapsed -> ceiling gives 5 hours -> 5%
		now = now_datetime()
		start_under = add_to_date(now, seconds=-(5 * 3600 - 60))
		g_under = frappe._dict(
			{
				"sla_start_at": start_under,
				"sla_due_date": add_to_date(start_under, hours=100),
				"total_hold_time": 0,
				"on_hold_since": None,
			}
		)
		self.assertEqual(sla.consumed_percent(g_under), 5)

		# 5 hours and 1 minute elapsed out of 100 hours -> ceiling gives 6 hours -> 6%
		start_part_hour = add_to_date(now, seconds=-(5 * 3600 + 60))
		g_part = frappe._dict(
			{
				"sla_start_at": start_part_hour,
				"sla_due_date": add_to_date(start_part_hour, hours=100),
				"total_hold_time": 0,
				"on_hold_since": None,
			}
		)
		self.assertEqual(sla.consumed_percent(g_part), 6)

		# 0 elapsed -> 0%
		g_zero = frappe._dict(
			{
				"sla_start_at": add_to_date(now, hours=1),
				"sla_due_date": add_to_date(now, hours=101),
				"total_hold_time": 0,
				"on_hold_since": None,
			}
		)
		self.assertEqual(sla.consumed_percent(g_zero), 0)

	def test_escalate_with_no_higher_authority_sets_escalated_and_clears_next_escalation_at(self):
		"""When no higher authority exists, escalate sets escalated=1 and next_escalation_at=None."""
		officer_email = "lone_officer@example.com"
		if not frappe.db.exists("User", officer_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": officer_email,
					"first_name": "Lone Officer",
					"roles": [{"role": "Grievance Officer"}],
				}
			).insert(ignore_permissions=True)

		lone_dept = frappe.get_doc(
			{
				"doctype": "Grievance Department",
				"dept_name": f"Lone Officer Dept {frappe.generate_hash(length=6)}",
				"email_account": "lone_dept@example.com",
				"active": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance Department",
			lone_dept.name,
			force=True,
			ignore_permissions=True,
		)
		dept = lone_dept.name
		g = a_grievance(
			service_category=self.cat_a,
			grievance_type=self.gtype_a.name,
			workflow_state="Assigned",
			status="Assigned",
			assigned_to=officer_email,
			assigned_dept=dept,
		)
		g.db_set("next_escalation_at", now_datetime(), update_modified=False)
		g.reload()

		result = sla.escalate(g, reason="SLA Breached")
		g.reload()

		self.assertEqual(g.escalated, 1)
		self.assertIsNone(g.next_escalation_at)
		self.assertTrue(bool(result))

		# Verify timeline entry was written
		timeline_entries = frappe.get_all(
			"Grievance Timeline",
			filters={"grievance": g.name, "entry_type": "escalation"},
			fields=["body"],
		)
		self.assertTrue(len(timeline_entries) > 0)
		self.assertIn("Case escalated", timeline_entries[0].body)

	def test_higher_authority_skips_unstaffed_intermediate_rung(self):
		"""higher_authority_of skips an unstaffed intermediate rung to find the next higher officer."""
		for email, name in [
			("nodal_worker@example.com", "Nodal Worker"),
			("head_worker@example.com", "Head Worker"),
		]:
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": name,
						"roles": [{"role": "Grievance Officer"}],
					}
				).insert(ignore_permissions=True)

		dept = a_department()
		area = a_leaf_area()

		# Setup RBAC assignment with nodal_officer and department_head, leaving senior_nodal_officer unstaffed
		rbac_skip = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": dept,
				"category_scope": self.cat_a,
				"active": 1,
				"effective_from": "2026-01-01",
				"officers": [
					{
						"user": "nodal_worker@example.com",
						"administrative_area": area,
						"role_level": "nodal_officer",
						"is_primary": 1,
						"active": 1,
					},
					{
						"user": "head_worker@example.com",
						"administrative_area": area,
						"role_level": "department_head",
						"is_primary": 1,
						"active": 1,
					},
				],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance RBAC Assignment",
			rbac_skip.name,
			force=True,
			ignore_permissions=True,
		)

		target, level = sla.higher_authority_of(
			"nodal_worker@example.com",
			department=dept,
			administrative_area=area,
		)
		self.assertEqual(target, "head_worker@example.com")
		self.assertEqual(level.name, "department_head")
