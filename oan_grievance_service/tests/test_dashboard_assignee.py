# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""The assignee dimension of the dashboard rollups: keeping it true after a case moves, filling it
in on a site that already has rollups, keeping the retained history small, and what an officer's
scope reads where it is not available.

Rollback happens once per class, so what each class writes to the shared rollup tables is
undone with it.
"""

import json
from unittest.mock import patch

import frappe
from frappe.query_builder.functions import Count
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, getdate, now_datetime

from oan_grievance_service import permissions as perms
from oan_grievance_service.grievance_access_control.doctype.grievance_rbac_assignment.grievance_rbac_assignment import (
	top_rung,
)
from oan_grievance_service.grievance_management.doctype.grievance_timeline.grievance_timeline import (
	GrievanceTimeline,
)
from oan_grievance_service.patches import add_assignee_to_dashboard_rollups
from oan_grievance_service.services import constants as C
from oan_grievance_service.services import dashboard, dashboard_rollup
from oan_grievance_service.tests.fixtures import (
	a_desk,
	a_grievance,
	a_service_category,
	a_unique_department,
	a_user_with_role,
	discard_grievance,
)


def _build(chart_id, user=None, scope=None, **params):
	"""A chart built straight from the registry, past the cache, for `user`'s scope."""
	scope = dashboard.caller_scope(user) if user else scope
	return dashboard.CHARTS[chart_id].build(dashboard.Context(dashboard.Params(**params), False, scope))


def _total(user):
	return next(r["value"] for r in _build("grvKpis", user) if r["metric"] == "total")


def _received(user):
	return sum(r["received"] for r in _build("grvWeeklyTrend", user))


class TestReassignmentBackfill(FrappeTestCase):
	"""A daily row carries the assignee of its last rebuild, so a case moved to someone else has
	to have its filing day rebuilt, or the old assignee keeps counting it."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.dept = a_unique_department("Moved")
		cls.first = a_user_with_role(C.ROLE_OFFICER, "first")
		cls.second = a_user_with_role(C.ROLE_OFFICER, "second")
		cls.old = add_days(getdate(), -10)
		doc = a_grievance(assigned_dept=cls.dept)
		cls.name = doc.name
		frappe.db.set_value(
			"Grievance", doc.name, "creation", get_datetime(f"{cls.old} 10:00:00"), update_modified=False
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		discard_grievance(cls.name)
		for email in (cls.first, cls.second):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		cls._rebuild_old_day()
		dashboard_rollup.refresh()
		super().tearDownClass()

	@classmethod
	def _rebuild_old_day(cls):
		# Nothing in the recent window: only the filing day, as a backfill rebuilds it.
		tomorrow = add_days(getdate(), 1)
		dashboard_rollup._rebuild_daily(tomorrow, tomorrow, days=[cls.old])

	def setUp(self):
		frappe.set_user("Administrator")
		# Rollback is once per class, so clear what an earlier test left as a signal.
		frappe.db.delete(
			"Grievance Timeline", {"grievance": self.name, "entry_type": ["in", ["assignment", "escalation"]]}
		)
		frappe.db.delete("Grievance Status History", {"grievance": self.name})
		frappe.db.delete("Version", {"ref_doctype": "Grievance", "docname": self.name})
		frappe.db.set_value("Grievance", self.name, "assigned_to", self.first, update_modified=False)
		self._rebuild_old_day()

	def _move_to_second(self):
		# As the SLA sweep does: db_set, which leaves no Version and does not touch `modified`.
		frappe.db.set_value("Grievance", self.name, "assigned_to", self.second, update_modified=False)

	def _days(self):
		return dashboard_rollup._incremental_window(getdate())[1]

	def test_a_case_filed_long_ago_counts_for_its_assignee(self):
		self.assertEqual((_received(self.first), _received(self.second)), (1, 0))

	def test_a_move_the_sla_sweep_made_reaches_the_filing_day(self):
		self._move_to_second()
		GrievanceTimeline.record(
			grievance=self.name, entry_type="escalation", is_internal=0, body="Escalated."
		)
		dashboard_rollup.refresh()
		self.assertEqual((_received(self.first), _received(self.second)), (0, 1))

	def test_without_any_trace_the_move_waits_for_the_nightly_rebuild(self):
		self._move_to_second()
		dashboard_rollup.refresh()
		self.assertEqual((_received(self.first), _received(self.second)), (1, 0))
		# What the nightly rebuild does for every day, done for this one.
		self._rebuild_old_day()
		self.assertEqual((_received(self.first), _received(self.second)), (0, 1))

	def test_an_assignment_entry_on_the_timeline_is_a_signal(self):
		GrievanceTimeline.record(grievance=self.name, entry_type="assignment", is_internal=0, body="Moved.")
		self.assertIn(self.old, self._days())

	def test_a_move_to_assigned_in_the_status_history_is_a_signal(self):
		frappe.get_doc(
			{
				"doctype": "Grievance Status History",
				"grievance": self.name,
				"to_status": C.STATE_ASSIGNED,
				"timestamp": now_datetime(),
			}
		).insert(ignore_permissions=True)
		self.assertIn(self.old, self._days())

	def test_a_saved_edit_of_the_assignee_is_a_signal(self):
		frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": "Grievance",
				"docname": self.name,
				"data": json.dumps({"changed": [["assigned_to", self.first, self.second]]}),
			}
		).insert(ignore_permissions=True)
		self.assertIn(self.old, self._days())


class TestAssigneePatch(FrappeTestCase):
	"""The patch fills in the assignee before the migrate ends, so no officer reads zeros while a
	background job is awaited."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.category = a_service_category()
		cls.dept = a_unique_department("Patched")
		cls.nodal = a_user_with_role(C.ROLE_OFFICER, "nodal")
		cls.head = a_user_with_role(C.ROLE_OFFICER, "head")
		cls.desk = a_desk(cls.dept, cls.category, top_rung(), (cls.head, None))
		cls.created = []
		for assignee in (cls.nodal, None):
			doc = a_grievance(assigned_dept=cls.dept)
			cls.created.append(doc.name)
			frappe.db.set_value("Grievance", doc.name, "assigned_to", assignee)
		dashboard_rollup.refresh()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in cls.created:
			discard_grievance(name)
		frappe.delete_doc("Grievance RBAC Assignment", cls.desk, force=True, ignore_permissions=True)
		for email in (cls.nodal, cls.head):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		super().tearDownClass()

	def _unattributed(self, doctype):
		T = frappe.qb.DocType(doctype)
		return frappe.qb.from_(T).select(Count("*")).where(T.assigned_to.isnull()).run()[0][0]

	def test_it_fills_the_assignee_into_rows_built_before_the_column(self):
		for doctype in (dashboard_rollup.DAILY, dashboard_rollup.SNAPSHOT, dashboard_rollup.AREA):
			T = frappe.qb.DocType(doctype)
			frappe.qb.update(T).set(T.assigned_to, None).run()

		# The desk head reads the desk, which no assignee filters, so a row without one does not
		# take the desk away; the nodal officer reads by assignee and reads nothing yet.
		self.assertEqual(_total(self.head), 2)
		self.assertEqual(_total(self.nodal), 0)

		add_assignee_to_dashboard_rollups.execute()

		self.assertEqual(_total(self.nodal), 1)
		self.assertEqual(_total(self.head), 2)
		self.assertEqual(self._unattributed(dashboard_rollup.DAILY), 0)
		self.assertEqual(self._unattributed(dashboard_rollup.AREA), 0)
		S = frappe.qb.DocType(dashboard_rollup.SNAPSHOT)
		today_rows = (
			frappe.qb.from_(S)
			.select(Count("*"))
			.where((S.snapshot_date == getdate()) & S.assigned_to.isnull())
			.run()[0][0]
		)
		self.assertEqual(today_rows, 0)

	def test_it_queues_the_rebuild_when_a_runner_holds_the_lock(self):
		with (
			patch.object(dashboard_rollup, "refresh_now", return_value=None) as inline,
			patch.object(dashboard_rollup, "enqueue") as queued,
		):
			add_assignee_to_dashboard_rollups.execute()
		inline.assert_called_once_with(full=True)
		queued.assert_called_once_with(full=True)

	def test_refresh_now_gives_up_while_a_runner_holds_the_lock(self):
		self.assertTrue(dashboard_rollup._acquire(dashboard_rollup.LOCK_KEY, 60))
		try:
			self.assertIsNone(dashboard_rollup.refresh_now(full=True, wait=0))
		finally:
			dashboard_rollup._take(dashboard_rollup.LOCK_KEY)

	def test_refresh_now_lets_go_of_the_lock(self):
		self.assertIsNotNone(dashboard_rollup.refresh_now())
		self.assertFalse(dashboard_rollup._read(dashboard_rollup.LOCK_KEY))


class TestSnapshotHistoryCompaction(FrappeTestCase):
	"""Today's snapshot rows are split by assignee; a past day's are folded to one per group, so
	the retained history does not grow with the number of officers."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.category = a_service_category()
		self.day = getdate("2021-03-15")
		self.oldest = get_datetime("2021-03-01 09:00:00")
		self.addCleanup(frappe.db.delete, dashboard_rollup.SNAPSHOT, {"snapshot_date": self.day})
		dashboard_rollup._insert(
			dashboard_rollup.SNAPSHOT,
			("snapshot_date", *dashboard_rollup.SNAPSHOT_FIELDS),
			[
				(self.day, "Resolved", "XX01", self.category, "", "a@t.local", 0, 1, "", 2, 0, None),
				(self.day, "Resolved", "XX01", self.category, "", "b@t.local", 0, 1, "", 3, 0, None),
				(
					self.day,
					"Submitted",
					"XX01",
					self.category,
					"",
					"a@t.local",
					0,
					1,
					"ok",
					1,
					1,
					self.oldest,
				),
			],
		)

	def _rows(self):
		S = frappe.qb.DocType(dashboard_rollup.SNAPSHOT)
		return (
			frappe.qb.from_(S)
			.select(S.status, S.assigned_to, S.grievance_count, S.dup_pending_count, S.oldest_open_creation)
			.where(S.snapshot_date == self.day)
			.orderby(S.status)
			.run(as_dict=True)
		)

	def test_a_past_day_is_folded_without_the_assignee(self):
		dashboard_rollup.refresh()
		resolved, submitted = self._rows()
		self.assertEqual(
			(resolved.status, resolved.assigned_to, resolved.grievance_count), ("Resolved", "", 5)
		)
		self.assertEqual(
			(
				submitted.assigned_to,
				submitted.grievance_count,
				submitted.dup_pending_count,
				submitted.oldest_open_creation,
			),
			("", 1, 1, self.oldest),
		)

	def test_the_numbers_a_chart_reads_do_not_change(self):
		unscoped = dashboard.Context(dashboard.Params(), True)
		before = dashboard._by_status(dashboard._snapshot(unscoped, ("status",), on_date=self.day))
		dashboard_rollup.refresh()
		after = dashboard._by_status(dashboard._snapshot(unscoped, ("status",), on_date=self.day))
		self.assertEqual(before, after)

	def test_today_keeps_its_assignees(self):
		dashboard_rollup.refresh()
		S = frappe.qb.DocType(dashboard_rollup.SNAPSHOT)
		assigned = (
			frappe.qb.from_(S)
			.select(Count("*"))
			.where((S.snapshot_date == getdate()) & (S.assigned_to != ""))
			.run()[0][0]
		)
		cases = frappe.db.count("Grievance", {"assigned_to": ["is", "set"]})
		self.assertEqual(bool(assigned), bool(cases))


class TestAssignedScope(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.category = a_service_category()
		self.day = getdate("2021-03-15")
		self.addCleanup(frappe.db.delete, dashboard_rollup.SNAPSHOT, {"snapshot_date": self.day})
		dashboard_rollup._insert(
			dashboard_rollup.SNAPSHOT,
			("snapshot_date", *dashboard_rollup.SNAPSHOT_FIELDS),
			[(self.day, C.STATE_RESOLVED, "XX01", self.category, "", "", 0, 0, "", 3, 0, None)],
		)
		self.mine = (dashboard.Clause(assigned_to=("a@t.local",)),)
		self.with_desk = (dashboard.Clause(assigned_dept="Some Desk"), *self.mine)

	def test_a_clause_for_nobody_matches_nothing_and_is_not_invalid_sql(self):
		rows = _build("grvKpis", scope=(dashboard.Clause(assigned_to=()),))
		self.assertEqual(next(r["value"] for r in rows if r["metric"] == "total"), 0)

	def test_a_clause_for_nobody_is_not_everything_when_it_stands_beside_a_desk(self):
		# A scope of nothing but empty clauses must not widen to "no filter".
		scope = (dashboard.Clause(assigned_to=()), dashboard.Clause(assigned_to=()))
		self.assertEqual(
			next(r["value"] for r in _build("grvKpis", scope=scope) if r["metric"] == "total"), 0
		)

	def test_an_assigned_only_scope_cannot_read_a_past_snapshot_day(self):
		ctx = dashboard.Context(dashboard.Params(), False, self.mine)
		yesterday = add_days(ctx.today, -1)
		self.assertIsNone(dashboard._readable_snapshot_day(ctx, yesterday))
		self.assertEqual(dashboard._readable_snapshot_day(ctx, ctx.today), ctx.today)
		self.assertIsNone(dashboard._readable_snapshot_day(ctx, None))

	def test_a_scope_with_a_desk_or_none_reads_a_past_day(self):
		for scope in (self.with_desk, None):
			ctx = dashboard.Context(dashboard.Params(), scope is None, scope)
			yesterday = add_days(ctx.today, -1)
			self.assertEqual(dashboard._readable_snapshot_day(ctx, yesterday), yesterday)

	def test_a_past_month_is_no_data_for_an_officer_and_data_for_an_admin(self):
		ctx = dashboard.Context(dashboard.Params(as_of_month="2021-03"), False, self.mine)
		self.assertEqual(dashboard.build_resolution_rate_by_region(ctx), [])
		self.assertEqual(ctx.unavailable, dashboard.NO_SNAPSHOT)

		admin = dashboard.Context(dashboard.Params(as_of_month="2021-03"), True)
		rows = dashboard.build_resolution_rate_by_region(admin)
		self.assertEqual([r["region_code"] for r in rows], ["XX01"])
		self.assertIsNone(admin.unavailable)

	def test_a_past_months_rates_are_none_not_zero_for_an_officer(self):
		this_month = getdate().replace(day=1)
		last_month = add_days(this_month, -1).strftime("%Y-%m")
		rows = _build("grvPerformanceKpis", scope=self.mine, month=last_month)
		rate = next(r for r in rows if r["metric"] == "resolution_rate")
		self.assertIsNone(rate["value"])
		self.assertIsNone(rate["delta"])

	def test_the_rbac_rows_are_read_once(self):
		officer = a_user_with_role(C.ROLE_OFFICER, "reads-once")
		self.addCleanup(frappe.delete_doc, "User", officer, force=True, ignore_permissions=True)
		with patch.object(perms, "active_scopes", wraps=perms.active_scopes) as spy:
			dashboard.caller_scope(officer)
		self.assertEqual(spy.call_count, 1)
