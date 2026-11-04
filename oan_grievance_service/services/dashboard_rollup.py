# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Dashboard rollups: the only reader of Grievance for analytics.

The dashboards never aggregate `tabGrievance` on a request. The scheduler folds it
into small tables every 15 minutes, and again as soon as a case is filed or changed
(`request_refresh`), and services.dashboard answers every chart from those:

- Grievance Stat Daily: events per day (filed, resolved, rejected, feedback) by
  region, category, department and assignee, and how many of the cases filed that day have
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

The assignee (`assigned_to`) is a dimension of all three, so an officer's dashboard can count the
cases assigned to them and their team, as their case list does. It is the assignee now: a daily
row is rebuilt with the current one, so a reassignment of an older case reaches its past days at
the next nightly rebuild.

Only counts, sums and minimums are stored. Rates and averages are worked out when
a chart is read, so every filter combination stays exact.
"""

import time
from functools import partial

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

# Keeping the rollups current without two runs on the same rows. Each of these is a Redis key
# that is either there or not; see `request` for how they work together.
DIRTY_KEY = "grievance_dashboard_dirty"  # a case changed since a run last read
FULL_KEY = "grievance_dashboard_full"  # the next run rebuilds every day
LOCK_KEY = "grievance_dashboard_lock"  # a run is under way
PENDING_KEY = "grievance_dashboard_pending"  # a runner job is queued and has not started
LAST_KEY = "grievance_dashboard_last"  # when the last run ended
DIRTY_TTL = 60 * 60
# A run that dies without letting go frees the lock by itself, and a queued job that is lost
# is replaced.
LOCK_TTL = 30 * 60
PENDING_TTL = 5 * 60
# One runner makes at most 1 + CATCH_UP_RUNS passes, then queues a successor behind the other
# jobs on the long queue instead of holding a worker while cases keep arriving.
CATCH_UP_RUNS = 3
# Runs start at least this many seconds apart, so a stream of saves is one pass per interval.
MIN_GAP = 5
# Set on the request while a save has asked for a refresh at commit; see `request_refresh`.
REQUESTED = "grievance_dashboard_requested"
RUNNER = "oan_grievance_service.services.dashboard_rollup.run_pending"

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

DAILY_DIMENSIONS = ("region", "service_category", "assigned_dept", "assigned_to")
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
AREA_FIELDS = ("region", "woreda", "service_category", "assigned_dept", "assigned_to", "grievance_count")
SNAPSHOT_FIELDS = (
	"status",
	"region",
	"service_category",
	"assigned_dept",
	"assigned_to",
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
		# What lets an officer's dashboard count the cases assigned to them and their team,
		# as their case list does.
		Coalesce(g.assigned_to, "").as_("assigned_to"),
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
		.groupby(*_positions(1 + len(dimensions)))
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
		# status, the dimensions, then escalated, has_desk and sla_state.
		.groupby(*_positions(1 + len(dimensions) + 3))
	)


def _area_query():
	g, query, dimensions = _grievances()
	query, woreda = _woreda(query)
	return (
		query.select(*dimensions, woreda, Count("*").as_("grievance_count"))
		.where(g.status.isin(list(live_states())))
		.groupby(*_positions(len(dimensions) + 1))
	)


def enqueue(full=False, after_commit=True):
	"""Ask for a refresh (a full rebuild when `full`): the scheduler, the nightly rebuild, the
	first build after migrate and the patches all come through here.

	With `after_commit` the request is made once the current transaction commits, as
	frappe.enqueue(enqueue_after_commit=True) does: a run started before the commit would
	read the data without it, and its flag would be gone by the time the change is visible.
	"""
	if after_commit:
		frappe.db.after_commit.add(partial(_request_safely, full))
	else:
		_request(full)


def request_refresh(doc=None, method=None):
	"""Grievance doc_events handler: a case was filed, changed or deleted, so refresh now
	instead of waiting for the next 15-minute run.

	However many saves a transaction holds, it asks once, at commit, and not at all if it is
	rolled back. Writes that skip doc events (db_set, bulk SQL) are picked up by the
	scheduled run.
	"""
	if frappe.flags.in_test or frappe.flags.in_migrate or frappe.flags.in_install:
		return
	if frappe.flags.get(REQUESTED):
		return
	frappe.flags[REQUESTED] = True
	frappe.db.after_commit.add(_requested_by_save)
	frappe.db.after_rollback.add(_forget_request)


def _requested_by_save():
	frappe.flags[REQUESTED] = False
	_request_safely()


def _forget_request():
	frappe.flags[REQUESTED] = False


# Requesting, and running
# -----------------------
# A request raises DIRTY (and FULL, for a rebuild) and then makes sure a runner will look.
# A runner holds LOCK while it works, takes DIRTY before it reads, and looks at DIRTY again
# after it lets go. A request that lands while a runner is working is therefore either seen
# by that runner's next pass or by its look after unlocking, and one that lands after that
# finds the lock free for the runner it queues. Nothing relies on the queue to merge two
# requests, because the queue cannot join a job that has already started.


def _key(name):
	"""The Redis key for `name`, prefixed with the site as set_value/get_value do."""
	return frappe.cache.make_key(name)


# The next two are the only raw Redis calls here. A lock needs set-if-absent with an expiry in
# one step, which set_value cannot do; the key is made by `_key`, so it is per site as the
# multitenancy rule asks.
def _put(name, value, ttl, only_if_absent=False):
	return frappe.cache.set(_key(name), value, ex=ttl, nx=only_if_absent)  # nosemgrep


def _read(name):
	return frappe.cache.get(_key(name))  # nosemgrep


def _raise(name, ttl):
	_put(name, 1, ttl)


def _take(name):
	"""Lower a flag and say whether it was up. It is one DEL, so of two callers one wins."""
	return bool(frappe.cache.delete(_key(name)))


def _acquire(name, ttl):
	return bool(_put(name, 1, ttl, only_if_absent=True))


def _request(full=False):
	if full:
		_raise(FULL_KEY, DIRTY_TTL)
	_raise(DIRTY_KEY, DIRTY_TTL)
	_kick()


def _request_safely(full=False):
	"""`_request` for a callback that runs after the commit: a Redis or queue failure must not
	fail the request whose change is already saved. A missed refresh waits for the next
	scheduled run."""
	try:
		_request(full)
	except Exception:
		frappe.log_error(title="Dashboard rollup refresh was not requested")


def _kick():
	"""Queue a runner unless one is queued already. A runner that is working does not count:
	it may be past its last look, so it looks again after unlocking (see `run_pending`)."""
	if not _acquire(PENDING_KEY, PENDING_TTL):
		return
	try:
		frappe.enqueue(RUNNER, queue="long", enqueue_after_commit=False)
	except Exception:
		_take(PENDING_KEY)
		raise


def _wait_for_gap():
	last = _read(LAST_KEY)
	if last:
		wait = MIN_GAP - (time.time() - float(last))
		if wait > 0:
			time.sleep(min(wait, MIN_GAP))


def run_pending():
	"""The queued job: refresh while someone has asked, at most 1 + CATCH_UP_RUNS times.

	Returns what the last refresh did, or None when there was nothing to do or another run
	holds the lock (that run looks again once it lets go, so the request is not lost).
	"""
	_take(PENDING_KEY)
	if not _acquire(LOCK_KEY, LOCK_TTL):
		return None
	result = None
	try:
		for _ in range(1 + CATCH_UP_RUNS):
			if not frappe.cache.exists(DIRTY_KEY):
				break
			_wait_for_gap()
			# DIRTY first, then FULL: a request raises them the other way round, so a rebuild
			# whose DIRTY we took has its FULL up already.
			_take(DIRTY_KEY)
			full = _take(FULL_KEY)
			try:
				result = refresh(full=full)
			except Exception:
				# Put the request back for the next run rather than lose it.
				_raise(DIRTY_KEY, DIRTY_TTL)
				if full:
					_raise(FULL_KEY, DIRTY_TTL)
				raise
			_put(LAST_KEY, repr(time.time()), MIN_GAP * 10)
			# Commit so the next pass reads a fresh snapshot, not the one this run began with;
			# this runs only in the background job, which owns its transaction.
			frappe.db.commit()  # nosemgrep
	finally:
		_take(LOCK_KEY)
	# A request that came after the last look, or that the pass limit left, needs a runner.
	if frappe.cache.exists(DIRTY_KEY):
		_kick()
	return result


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
				key = (row.stat_date, *(row[d] for d in DAILY_DIMENSIONS))
				target = merged.setdefault(key, dict.fromkeys(DAILY_METRICS, 0))
				for metric in DAILY_METRICS:
					if metric in row:
						target[metric] = row[metric] or 0

	frappe.db.delete(DAILY, {"stat_date": [">=", start]})
	for day_start, day_end in windows[1:]:
		frappe.db.delete(DAILY, {"stat_date": ["between", [day_start, add_days(day_end, -1)]]})
	_insert(
		DAILY,
		("stat_date", *DAILY_DIMENSIONS, *DAILY_METRICS),
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
