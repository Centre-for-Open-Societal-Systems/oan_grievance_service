# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from oan_grievance_service.api.v1.change_request import raise_change_request
from oan_grievance_service.services import classification, lifecycle, sla
from oan_grievance_service.services import constants as C
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
				"holiday_list": self.hl_name,
				"auto_escalate": 1,
				"active": 1,
			}
		).insert(ignore_permissions=True)
		classification.set_base_days(self.cat_a, 5)

		# Configure SLA policy for Category B (10 days, with holiday list)
		for name in frappe.get_all(
			"Grievance SLA Configuration", filters={"service_category": self.cat_b}, pluck="name"
		):
			frappe.delete_doc("Grievance SLA Configuration", name, force=True)

		self.sla_b = frappe.get_doc(
			{
				"doctype": "Grievance SLA Configuration",
				"service_category": self.cat_b,
				"holiday_list": self.hl_name,
				"auto_escalate": 1,
				"active": 1,
			}
		).insert(ignore_permissions=True)
		classification.set_base_days(self.cat_b, 10)

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

		lifecycle.transition(g, "Assign", reason="Assigned to department officer")
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

		lifecycle.transition(g, "Assign", reason="Assigned to department officer")
		g.reload()
		self.assertEqual(g.sla_days, 5)

		# Change category to Category B
		g.db_set({"service_category": self.cat_b, "grievance_type": self.gtype_b.name}, update_modified=False)
		sla.recalculate_sla_on_category_change(g, new_category=self.cat_b)
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
		desk = frappe.get_doc(
			{
				"doctype": "Grievance RBAC Assignment",
				"department_scope": dept,
				"category_scope": self.cat_a,
				"active": 1,
				"effective_from": frappe.utils.today(),
				"officers": [
					{"user": officer_email, "role_level": "nodal_officer", "is_primary": 1, "active": 1}
				],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc,
			"Grievance RBAC Assignment",
			desk.name,
			force=True,
			ignore_permissions=True,
		)
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

		# Verify that with next_escalation_at as None, escalate_breached does not pick it up again
		from oan_grievance_service import tasks

		tasks.escalate_breached()
		g.reload()
		self.assertIsNone(g.next_escalation_at)
		timeline_entries_after = frappe.get_all(
			"Grievance Timeline",
			filters={"grievance": g.name, "entry_type": "escalation"},
			fields=["body"],
		)
		self.assertEqual(len(timeline_entries_after), len(timeline_entries))

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

	# STG-516: escalation waits for the full SLA window
	# --------------------------------------------------

	def _case_with_clock(self, started, due):
		"""An assigned case of Category A whose SLA window runs from `started` to `due`."""
		g = a_grievance(
			service_category=self.cat_a,
			grievance_type=self.gtype_a.name,
			workflow_state="Assigned",
			status="Assigned",
		)
		g.db_set(
			{
				"sla_start_at": started,
				"sla_due_date": due,
				"total_hold_time": 0,
				"on_hold_since": None,
				"escalated": 0,
				**sla.cleared_reminders(),
			},
			update_modified=False,
		)
		g.reload()
		return g

	def test_first_escalation_waits_for_the_deadline(self):
		"""Devtest: filed 1:13 PM, escalated 2:02 PM on a one-day SLA. A 3% threshold armed the
		bump 43 minutes in; now the first escalation waits for the deadline."""
		filed = add_to_date(now_datetime(), minutes=-49)
		g = self._case_with_clock(filed, add_days(filed, 1))
		g.db_set("next_escalation_at", None, update_modified=False)

		sla.arm_escalation(g)
		g.reload()
		self.assertEqual(get_datetime(g.next_escalation_at), get_datetime(g.sla_due_date))

		with patch(
			"oan_grievance_service.tasks.grievance_settings.auto_escalation_enabled", return_value=True
		):
			escalate_breached()
		g.reload()
		self.assertFalse(g.escalated)

	def test_escalation_fires_once_the_sla_has_elapsed(self):
		"""A one-day SLA escalates on the sweep after the day is up."""
		filed = add_to_date(now_datetime(), hours=-25)
		g = self._case_with_clock(filed, add_days(filed, 1))
		sla.arm_escalation(g)

		with patch(
			"oan_grievance_service.tasks.grievance_settings.auto_escalation_enabled", return_value=True
		):
			escalate_breached()
		g.reload()
		self.assertTrue(g.escalated)

	def test_rearm_patch_moves_early_escalations_to_the_deadline(self):
		from oan_grievance_service.patches import rearm_escalation_at_sla_deadline

		filed = add_to_date(now_datetime(), minutes=-10)
		g = self._case_with_clock(filed, add_days(filed, 1))
		g.db_set("next_escalation_at", add_to_date(filed, minutes=43), update_modified=False)

		rearm_escalation_at_sla_deadline.execute()
		g.reload()
		self.assertEqual(get_datetime(g.next_escalation_at), get_datetime(g.sla_due_date))

	# STG-516: reminders at 20%, 50% and 80%, each once per window
	# ------------------------------------------------------------

	def _sweep_reminders(self, g):
		"""Run the reminder sweep and return the events it queued for `g`, in order."""
		with patch("oan_grievance_service.tasks.notifications.queue") as queue:
			send_sla_reminders()
		return [call.args[1] for call in queue.call_args_list if call.args[0].name == g.name]

	def test_each_reminder_threshold_is_sent_once_as_it_is_reached(self):
		def elapsed(hours):
			"""Slide the 100-hour window so that `hours` of it have gone."""
			start = add_to_date(now_datetime(), hours=-hours)
			g.db_set(
				{"sla_start_at": start, "sla_due_date": add_to_date(start, hours=100)}, update_modified=False
			)

		start = add_to_date(now_datetime(), hours=-25)
		g = self._case_with_clock(start, add_to_date(start, hours=100))
		self.assertEqual(self._sweep_reminders(g), [C.EVENT_SLA_REMINDER_20])
		self.assertEqual(self._sweep_reminders(g), [], "the 20% reminder must not repeat")

		elapsed(55)
		self.assertEqual(self._sweep_reminders(g), [C.EVENT_SLA_REMINDER_50])

		elapsed(85)
		self.assertEqual(self._sweep_reminders(g), [C.EVENT_SLA_REMINDER_80, C.EVENT_SLA_AT_RISK])
		self.assertEqual(self._sweep_reminders(g), [])

		g.reload()
		self.assertEqual(g.last_reminder_percent, C.SLA_AT_RISK_THRESHOLD)

	def test_reminders_already_passed_are_not_skipped(self):
		"""A case first swept at 86% gets the 20% and 50% reminders too, not only the 80% one."""
		start = add_to_date(now_datetime(), hours=-85)
		g = self._case_with_clock(start, add_to_date(start, hours=100))
		self.assertEqual(
			self._sweep_reminders(g),
			[C.EVENT_SLA_REMINDER_20, C.EVENT_SLA_REMINDER_50, C.EVENT_SLA_REMINDER_80, C.EVENT_SLA_AT_RISK],
		)

	def test_reminders_of_a_new_window_are_not_deduplicated_away(self):
		"""After a deferral or reopen clears the flags, the reminders are queued again, not dropped."""
		from oan_grievance_service.services import notifications

		start = add_to_date(now_datetime(), hours=-25)
		g = self._case_with_clock(start, add_to_date(start, hours=100))
		recipient = "Administrator"
		for event in (C.EVENT_SLA_REMINDER_20, C.EVENT_SLA_BREACH):
			frappe.get_doc(
				{
					"doctype": "Grievance Notification Log",
					"grievance": g.name,
					"event": event,
					"recipient": recipient,
					"channel": "Email",
					"status": "Sent",
				}
			).insert(ignore_permissions=True)
		for event in (*C.SLA_REMINDER_EVENTS.values(), C.EVENT_SLA_AT_RISK):
			self.assertFalse(notifications._already_queued(g.name, event, recipient, "Email"), event)
		# Other events still go out once per case.
		self.assertTrue(notifications._already_queued(g.name, C.EVENT_SLA_BREACH, recipient, "Email"))

		g.db_set(sla.cleared_reminders(), update_modified=False)
		self.assertEqual(self._sweep_reminders(g), [C.EVENT_SLA_REMINDER_20])

	def test_a_new_threshold_needs_no_new_field(self):
		"""The sweep keeps one number per case, so adding a threshold is a constants change."""
		start = add_to_date(now_datetime(), hours=-35)
		g = self._case_with_clock(start, add_to_date(start, hours=100))
		events = {**C.SLA_REMINDER_EVENTS, 30: "sla_reminder_30"}
		with (
			patch.object(C, "SLA_REMINDER_EVENTS", events),
			patch.object(C, "SLA_REMINDER_THRESHOLDS", tuple(sorted(events))),
		):
			self.assertEqual(self._sweep_reminders(g), [C.EVENT_SLA_REMINDER_20, "sla_reminder_30"])
		g.reload()
		self.assertEqual(g.last_reminder_percent, 30)

	def test_backfill_patch_carries_the_old_flags(self):
		"""A case whose 50% reminder went out under the old flags is not reminded at 20% or 50% again.

		The old columns exist only on a site that ran the earlier code; a fresh site has nothing
		to carry, and the patch must leave it alone."""
		from oan_grievance_service.patches import backfill_last_reminder_percent

		start = add_to_date(now_datetime(), hours=-60)
		g = self._case_with_clock(start, add_to_date(start, hours=100))
		if not frappe.db.has_column("Grievance", "reminder_50_sent"):
			backfill_last_reminder_percent.execute()
			g.reload()
			self.assertEqual(g.last_reminder_percent, 0)
			return
		grievance = frappe.qb.DocType("Grievance")
		frappe.qb.update(grievance).set(grievance.reminder_50_sent, 1).where(grievance.name == g.name).run()
		backfill_last_reminder_percent.execute()
		g.reload()
		self.assertEqual(g.last_reminder_percent, 50)
		self.assertEqual(self._sweep_reminders(g), [])
