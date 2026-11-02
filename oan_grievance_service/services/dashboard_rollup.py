# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Dashboard rollups: the only reader of Grievance for analytics.

The dashboards never aggregate `tabGrievance` on a request. The scheduler folds it
into small tables every 15 minutes, and services.dashboard answers every chart
from those:

- Grievance Stat Daily: events per day (filed, resolved, rejected, feedback) by
  region, category and department, and how many of the cases filed that day have
  been escalated, and given a public officer update, since. Events never move once
  they happen, so a refresh rebuilds only the last few days, plus the filing day of
  each older case just escalated or first answered (at most BACKFILL_DAYS_MAX of
  them, newest first); the nightly run rebuilds all of them to absorb the rest and
  anything later edited by hand.
- Grievance Stat Snapshot: how many cases stood in each status on a day, by the
  same dimensions plus the escalation flag, whether a desk holds the case, and SLA state. Today's rows are
  replaced on every refresh; earlier days stay as they were at midnight, which is
  the only record of past status counts.
- Grievance Stat Area: how many cases stand in each Region and Woreda, by category and
  department. Only today's: every refresh replaces the whole table. Woreda is a dimension of
  its own rather than a column of the snapshot, which would multiply every retained day.

Only counts, sums and minimums are stored. Rates and averages are worked out when
a chart is read, so every filter combination stays exact.
"""

import frappe
from frappe.query_builder import Case, Order
from frappe.query_builder.functions import Coalesce, Count, Date, Min, Sum, UnixTimestamp
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime
from pypika.terms import ExistsCriterion, LiteralValue

from oan_grievance_service.services import constants as C
from oan_grievance_service.services import sla

DAILY = "Grievance Stat Daily"
SNAPSHOT = "Grievance Stat Snapshot"
AREA = "Grievance Stat Area"

# Where the time of the last completed refresh is kept, as a system default: it is
# what every chart reports as its as_of.
AS_OF_DEFAULT = "grievance_dashboard_as_of"
JOB_ID = "grievance_dashboard_rollup"

# An incremental refresh rebuilds this many days, today included. More than one,
# so a run that failed around midnight is repaired by the next.
INCREMENTAL_DAYS = 3
# Older filing days an incremental refresh may also rebuild. Acting on an old case moves
# its filing day's counts, but a run's work must follow the days touched, not the age of
# the case, so a sweep that touches more waits for the nightly full rebuild.
BACKFILL_DAYS_MAX = 31
AT_RISK_HOURS = 24
SATISFIED_RATING = 4
_EPOCH = "2000-01-01"

DAILY_METRICS = (
	"submitted_count",
	"resolved_count",
	"resolved_on_time_count",
	"resolved_breached_count",
	"resolution_hours_sum",
	"rejected_count",
	"escalated_count",
	"informed_count",
	"feedback_count",
	"feedback_satisfied_count",
)
# Cases per Region and Woreda, with the desk dimensions a scope filters on. Only the rows of
# the moment: every refresh replaces the table, no history is kept.
AREA_FIELDS = ("region", "woreda", "service_category", "assigned_dept", "grievance_count")
SNAPSHOT_FIELDS = (
	"status",
	"region",
	"service_category",
	"assigned_dept",
	"escalated",
	"has_desk",
	"sla_state",
	"grievance_count",
	"dup_pending_count",
	"oldest_open_creation",
)

# GROUP BY is positional: MariaDB resolves a GROUP BY name against the tables
# before the select list, so `assigned_dept` would group on the raw column and
# split NULL from '' after COALESCE had merged them. LiteralValue keeps the
# positions out of the query parameters.
#
# A grievance's region is the Region-level ancestor of its filing area, found
# through the area's own lft rather than the copy on Grievance, which goes stale
# when the tree is rebuilt. It is stored as the region's P-code.


def _positions(count):
	return [LiteralValue(str(i)) for i in range(1, count + 1)]


def _count_if(condition):
	return Sum(Case().when(condition, 1).else_(0))


def _grievances():
	"""Grievance joined to its filing area and that area's region."""
	g = frappe.qb.DocType("Grievance", alias="g")
	a = frappe.qb.DocType("Grievance Administrative Area", alias="a")
	r = frappe.qb.DocType("Grievance Administrative Area", alias="r")
	query = (
		frappe.qb.from_(g)
		.join(a)
		.on(a.name == g.administrative_area)
		.left_join(r)
		.on((r.level_name == "Region") & a.lft.between(r.lft, r.rgt))
	)
	dimensions = (
		Coalesce(r.code, "").as_("region"),
		g.service_category,
		Coalesce(g.assigned_dept, "").as_("assigned_dept"),
	)
	return g, query, dimensions


def live_states():
	"""Every state a chart counts, in lifecycle order. A case in a retired state is left out
	of every chart, so they all add up to the same total."""
	return (*sla.open_states(), *C.RESOLVED_STATES, C.STATE_REJECTED)


def _woreda(query):
	"""`query` joined to the Woreda-level ancestor of the filing area, and that Woreda's key.

	Keyed by code, as regions are, or by the area's name for a Woreda the registry gave no
	code: keep in step with grievance_administrative_area.rollup_key.
	"""
	a = frappe.qb.DocType("Grievance Administrative Area", alias="a")
	w = frappe.qb.DocType("Grievance Administrative Area", alias="w")
	query = query.left_join(w).on((w.level_name == C.AREA_LEVEL_WOREDA) & a.lft.between(w.lft, w.rgt))
	key = Case().when(Coalesce(w.code, "") != "", w.code).else_(Coalesce(w.name, ""))
	return query, key.as_("woreda")


def _has_desk(g):
	"""A case sits with a real desk when it is not on the Unrouted catch-all and has a
	department, a routing rule or an officer. routing.ranked_assignments treats an empty
	department scope as a wildcard, so a desk with none is a real desk and the
	department alone cannot say."""
	dept = Coalesce(g.assigned_dept, "")
	held = (dept != "") | (Coalesce(g.routing_rule, "") != "") | (Coalesce(g.assigned_to, "") != "")
	return Case().when((dept != C.UNROUTED_DEPARTMENT) & held, 1).else_(0)


def _public_officer_entry(t):
	"""A public entry an officer put on the timeline: a message, a question, or the
	reason for an action. The submission entry an officer files on someone's behalf
	does not count."""
	return (t.is_internal == 0) & (t.author_type == "officer") & (t.entry_type != "submission")


def _informed(g):
	t = frappe.qb.DocType("Grievance Timeline", alias="t")
	return ExistsCriterion(
		frappe.qb.from_(t).select(t.name).where((t.grievance == g.name) & _public_officer_entry(t))
	)


def _daily_query(start, end, day_column, measures, where=None):
	g, query, dimensions = _grievances()
	day = getattr(g, day_column)
	query = (
		query.select(Date(day).as_("stat_date"), *dimensions, *measures(g))
		.where((day >= start) & (day < end))
		.groupby(*_positions(4))
	)
	return query.where(where(g)) if where else query


def _daily_queries(start, end):
	resolved = list(C.RESOLVED_STATES)
	return [
		_daily_query(start, end, "creation", lambda g: [Count("*").as_("submitted_count")]),
		_daily_query(
			start,
			end,
			"resolved_at",
			lambda g: [
				Count("*").as_("resolved_count"),
				_count_if(g.sla_due_date.isnotnull() & (g.resolved_at <= g.sla_due_date)).as_(
					"resolved_on_time_count"
				),
				_count_if(g.sla_due_date.isnotnull() & (g.resolved_at > g.sla_due_date)).as_(
					"resolved_breached_count"
				),
				(Sum(UnixTimestamp(g.resolved_at) - UnixTimestamp(g.creation)) / 3600.0).as_(
					"resolution_hours_sum"
				),
			],
			lambda g: g.status.isin(resolved),
		),
		# Rejected is terminal and a rejected case is never saved again, so its last
		# modification is when it was rejected.
		_daily_query(
			start,
			end,
			"modified",
			lambda g: [Count("*").as_("rejected_count")],
			lambda g: g.status == "Rejected",
		),
		# Dated by filing, not by escalation: this counts the cases filed that day that have
		# been escalated since. The escalation rate divides it by submitted_count, so both
		# must describe the same cases; a case filed in March and escalated in April is
		# part of March's rate, never of April's.
		_daily_query(
			start,
			end,
			"creation",
			lambda g: [Count("*").as_("escalated_count")],
			lambda g: g.escalated_at.isnotnull(),
		),
		# A case is informed once an officer has put a public entry on its timeline. Dated
		# by filing, like escalated_count.
		_daily_query(start, end, "creation", lambda g: [Count("*").as_("informed_count")], _informed),
		# The submitter rates the case (1-5) only when confirming the resolution, the same
		# transition that stamps resolved_at, so that is the day the feedback was given.
		_daily_query(
			start,
			end,
			"resolved_at",
			lambda g: [
				Count("*").as_("feedback_count"),
				_count_if(g.satisfaction_rating >= SATISFIED_RATING).as_("feedback_satisfied_count"),
			],
			lambda g: g.status.isin(resolved) & (g.satisfaction_rating > 0),
		),
	]


def _snapshot_query(now):
	g, query, dimensions = _grievances()
	duplicate = frappe.qb.DocType("Grievance Duplicate")
	pending = (
		frappe.qb.from_(duplicate)
		.select(duplicate.grievance)
		.distinct()
		.where(duplicate.is_confirmed == 0)
		.as_("d")
	)
	open_states = list(sla.open_states())
	# An empty IN () is a syntax error; no state is named "".
	paused = list(sla.states_in_category(sla.PAUSED)) or [""]
	sla_state = (
		Case()
		.when(g.status.notin(open_states), "")
		.when(g.status.isin(paused), "paused")
		.when(g.sla_due_date.isnull(), "no_sla")
		.when(g.sla_due_date < now, "breached")
		.when(g.sla_due_date < add_to_date(now, hours=AT_RISK_HOURS), "at_risk")
		.else_("ok")
	)
	return (
		query.left_join(pending)
		.on(pending.grievance == g.name)
		.select(
			g.status,
			*dimensions,
			g.escalated,
			_has_desk(g).as_("has_desk"),
			sla_state.as_("sla_state"),
			Count("*").as_("grievance_count"),
			_count_if(pending.grievance.isnotnull() & g.status.isin(open_states)).as_("dup_pending_count"),
			Min(Case().when(g.status.isin(open_states) & (g.escalated == 0), g.creation)).as_(
				"oldest_open_creation"
			),
		)
		.groupby(*_positions(7))
	)


def _area_query():
	g, query, dimensions = _grievances()
	query, woreda = _woreda(query)
	return (
		query.select(*dimensions, woreda, Count("*").as_("grievance_count"))
		.where(g.status.isin(list(live_states())))
		.groupby(*_positions(4))
	)


def enqueue(full=False):
	"""Queue a refresh. Every caller shares one job id, so a refresh never starts while
	another is queued or running; that is what keeps two runs off the same rows."""
	frappe.enqueue(
		"oan_grievance_service.services.dashboard_rollup.refresh",
		full=full,
		queue="long",
		job_id=JOB_ID,
		deduplicate=True,
		enqueue_after_commit=True,
	)


def refresh(full=False):
	"""Bring both rollups up to date. Returns what it did.

	`full` rebuilds every day of Grievance Stat Daily instead of the last few.
	Snapshot history is never rebuilt: a past day's status counts cannot be
	recovered from current data, which is why they are kept.

	Run it through `enqueue`: a refresh deletes and rewrites its days, so two at once
	would double-count.
	"""
	now = now_datetime()
	today = getdate(now)
	if full:
		start, days = getdate(_EPOCH), []
	else:
		start, days = _incremental_window(today)
	daily_rows = _rebuild_daily(start, add_days(today, 1), days)
	snapshot_rows = _rebuild_snapshot(today, now)
	area_rows = _rebuild_area()
	frappe.db.set_default(AS_OF_DEFAULT, str(now))
	return {
		"as_of": now,
		"from": start,
		"days": days,
		"daily_rows": daily_rows,
		"snapshot_rows": snapshot_rows,
		"area_rows": area_rows,
	}


def _incremental_window(today):
	"""What an incremental refresh rebuilds: the first day of the recent window, and the
	older filing days to rebuild besides.

	escalated_count and informed_count sit on the day a case was filed, so first
	escalating a case, or first giving it a public officer update, in the window changes
	an older day. Those days are rebuilt one by one, not everything since the oldest, so
	the work follows how many days were touched and not how old the cases are. At most
	BACKFILL_DAYS_MAX, newest first; the nightly rebuild takes the rest.

	A later reply on a case that already had a public update changes nothing, so only a
	case whose first one falls in the window counts.
	"""
	start = add_days(today, -(INCREMENTAL_DAYS - 1))
	g = frappe.qb.DocType("Grievance", alias="g")
	escalated = (
		frappe.qb.from_(g)
		.select(Date(g.creation))
		.distinct()
		.where((g.escalated_at >= start) & (g.creation < start))
	)
	t = frappe.qb.DocType("Grievance Timeline", alias="t")
	p = frappe.qb.DocType("Grievance Timeline", alias="p")
	answered_before = ExistsCriterion(
		frappe.qb.from_(p)
		.select(p.name)
		.where((p.grievance == t.grievance) & (p.creation < start) & _public_officer_entry(p))
	)
	first_answered = (
		frappe.qb.from_(t)
		.join(g)
		.on(g.name == t.grievance)
		.select(Date(g.creation))
		.distinct()
		.where((t.creation >= start) & (g.creation < start) & _public_officer_entry(t))
		.where(answered_before.negate())
	)
	days = set()
	for query in (escalated, first_answered):
		query = query.orderby(LiteralValue("1"), order=Order.desc).limit(BACKFILL_DAYS_MAX)
		days.update(getdate(d) for (d,) in query.run())
	return start, sorted(sorted(days, reverse=True)[:BACKFILL_DAYS_MAX])


def _day_ranges(days):
	"""Consecutive days folded into (first, day after last) pairs, one query window each."""
	ranges = []
	for day in days:
		if ranges and ranges[-1][1] == day:
			ranges[-1][1] = add_days(day, 1)
		else:
			ranges.append([day, add_days(day, 1)])
	return ranges


def as_of():
	"""When the rollups were last refreshed, or None if they never have been."""
	value = frappe.db.get_default(AS_OF_DEFAULT)
	return get_datetime(value) if value else None


def ensure_built():
	"""after_migrate: queue a first full build on a site that has none yet."""
	if frappe.flags.in_test or frappe.db.exists(DAILY) or not frappe.db.exists("Grievance"):
		return
	enqueue(full=True)


def _rebuild_daily(start, end, days=()):
	"""Rebuild start <= day < end, and each of `days` besides (older filing days)."""
	windows = [(start, end), *_day_ranges(days)]
	merged = {}
	for window_start, window_end in windows:
		for query in _daily_queries(window_start, window_end):
			for row in query.run(as_dict=True):
				key = (row.stat_date, row.region, row.service_category, row.assigned_dept)
				target = merged.setdefault(key, dict.fromkeys(DAILY_METRICS, 0))
				for metric in DAILY_METRICS:
					if metric in row:
						target[metric] = row[metric] or 0

	frappe.db.delete(DAILY, {"stat_date": [">=", start]})
	for day_start, day_end in windows[1:]:
		frappe.db.delete(DAILY, {"stat_date": ["between", [day_start, add_days(day_end, -1)]]})
	_insert(
		DAILY,
		("stat_date", "region", "service_category", "assigned_dept", *DAILY_METRICS),
		[(*key, *(metrics[m] for m in DAILY_METRICS)) for key, metrics in merged.items()],
	)
	return len(merged)


def _rebuild_snapshot(today, now):
	rows = _snapshot_query(now).run(as_dict=True)
	frappe.db.delete(SNAPSHOT, {"snapshot_date": today})
	_insert(
		SNAPSHOT,
		("snapshot_date", *SNAPSHOT_FIELDS),
		[(today, *(row[f] for f in SNAPSHOT_FIELDS)) for row in rows],
	)
	return len(rows)


def _rebuild_area():
	rows = _area_query().run(as_dict=True)
	frappe.db.delete(AREA)
	_insert(AREA, AREA_FIELDS, [tuple(row[f] for f in AREA_FIELDS) for row in rows])
	return len(rows)


def _insert(doctype, fields, values):
	if not values:
		return
	now = now_datetime()
	frappe.db.bulk_insert(
		doctype,
		("name", "creation", "modified", "owner", "modified_by", *fields),
		[
			(frappe.generate_hash(length=12), now, now, "Administrator", "Administrator", *row)
			for row in values
		],
	)
