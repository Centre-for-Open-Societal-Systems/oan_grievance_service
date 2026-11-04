# Dashboard analytics

The grievance dashboards (the officer/admin dashboard and the public OAN programme
dashboards) are served from rollup tables that are refreshed as soon as a grievance is filed,
changed or deleted, and by the scheduler every 15 minutes as a fallback. No dashboard request
ever aggregates `tabGrievance`.

## Data flow

```
a Grievance is saved or deleted   services/dashboard_rollup.request_refresh()  (at commit)
every 15 min (cron */15)          services/dashboard_rollup.enqueue()
nightly (daily_long)              services/dashboard_rollup.enqueue(full=True)
  -> run_pending() on the long queue
     -> services/dashboard_rollup.refresh()
  tabGrievance, Status History, Duplicate
      -> Grievance Stat Daily      events per day (last 3 days rebuilt; all nightly)
      -> Grievance Stat Snapshot   status counts per day (today replaced; history kept)
      -> Grievance Stat Area       cases per region and woreda (whole table replaced; no history)
      -> as_of (system default)

request                        services/dashboard.get_chart()
  Redis cache per chart + parameters, keyed on as_of
      -> rows from the rollups only
```

- **Grievance Stat Daily** holds counts per `stat_date, region, service_category, assigned_dept, assigned_to` (the assignee is what lets an officer's dashboard count their own cases):
  filed, resolved (split on time / after SLA), resolution hours, rejected, feedback and
  satisfied feedback (`satisfaction_rating` 4 or 5, given on confirming the resolution, so
  dated by `resolved_at`), and `escalated_count`: of the cases filed that day, how many have
  been escalated since (dated by filing, so the escalation rate divides like by like), and
  `informed_count`: of the cases filed that day, how many have had a public officer update since
  (the Transparency axis of `grvPerformanceScore`). Events never move once they happen, so a
  refresh rebuilds only the last three days, plus the filing day of each older case first
  escalated or first answered in them (a later reply on an already answered case changes
  nothing). Only those days are rebuilt, newest first and at most 31 a run, so the work follows
  the days touched, not the age of the case; the nightly run rebuilds every day and absorbs
  the rest. A row carries the assignee as of its last rebuild, so after a reassignment the
  case's older days move to the new assignee at the next nightly rebuild.
- **Grievance Stat Snapshot** holds how many cases stood in each status on a day, by the same
  dimensions plus `escalated`, `has_desk` (the case is not on the Unrouted desk and has a
  department, a routing rule or an officer; behind the coverage axis), `sla_state` (ok / at_risk / breached / paused / no_sla),
  cases awaiting duplicate review, and the oldest open, non-escalated filing date. Today's rows
  are replaced on every refresh; earlier days are kept, because a past day's status counts
  cannot be rebuilt from current data. Days kept before the assignee was added have none, so an
  officer's status counts for those days (a past month's resolution rate) read as empty.
- Only counts, sums and minimums are stored. Rates and averages are worked out when a chart is
  read, so every filter combination is exact.
- **Grievance Stat Area** holds how many cases stand in each region and woreda, by service
  category and department, and nothing else: no status, no history. Every refresh replaces the
  whole table, so it stays as small as the woredas that hold cases, and a woreda is not a
  dimension of the retained snapshot, which would multiply every kept day. Only
  `grvByAdminLevel` reads it. It counts the same states as every other chart.
- A region is the Region-level ancestor of the filing area, stored as its P-code (`ET04`). A
  woreda is its Woreda-level ancestor, stored as its code, or its name when the registry gave it
  none.
- Refreshes never overlap, and none is lost. Every trigger (a case save, the 15-minute refresh,
  the nightly rebuild, the first build after migrate, a patch) raises a "changed" flag in Redis
  and queues a runner on the long queue unless one is already queued. A runner takes a Redis
  lock, so only one works at a time; it takes the flag before it reads, repeats while the flag
  is raised again (at most four passes, at least five seconds apart), and looks at the flag
  once more after it unlocks. A request that arrives while a runner is working is seen by its
  next pass or by that last look, and the runner then queues a successor if one is needed.
  A nightly rebuild raises its own flag, so a busy day cannot skip it; the runner turns its
  next pass into a full rebuild. A run that fails puts its flags back. A lock or queue entry
  that is lost frees itself after 30 and 5 minutes.
- A save asks once per transaction, after the commit, and not if it rolls back. If Redis or the
  queue is unavailable at that moment the failure is logged (Error Log) and the save still
  succeeds; the next scheduled run catches up.

Staleness: a few seconds after a case is filed or changed (the time to queue and run the
refresh, plus up to five seconds when another run has just ended), plus whatever the consumer
caches (the OAN dashboards cache another 15 minutes). A change made without a doc event
(`db_set`, bulk SQL, some SLA-sweep escalations) waits for the next 15-minute refresh, so at
most 15 minutes.

## Definitions

| Term            | Meaning                                                                      |
| --------------- | ---------------------------------------------------------------------------- |
| Total           | Every grievance                                                              |
| Awaiting Action | Submitted + Assigned + In Progress (an officer owes the next move)           |
| Open            | Awaiting Action + More Info Needed (waiting on them)                         |
| Resolved        | Resolved + Closed; `resolved_at` is when the case last reached either        |
| Escalated       | Open cases with the `escalated` flag; `escalated_at` is the first escalation |
| At risk         | Open, clock running, due within 24 hours                                     |
| Breached        | Open, clock running, past `sla_due_date`                                     |
| On time         | Resolved at or before `sla_due_date`; cases without an SLA count as neither  |

## Endpoints

| Route                           | Access                      | Returns                                                        |
| ------------------------------- | --------------------------- | -------------------------------------------------------------- |
| `GET /api/v1/charts/<chart_id>` | Dashboard Reader            | one chart; public charts only; counts, never case detail       |
| `GET /api/v1/charts?charts=...` | Grievance Admin             | several charts per call, admin-only charts and live detail     |
| `GET /api/v1/charts?charts=...` | Grievance Officer (JWT too) | the public charts, narrowed to the caller's cases; counts only |

All return the standard envelope with `meta.as_of`. Filters: `region` (P-codes),
`service_category` (alias `category`), `from` / `to`, `month`, `as_of_month` (only
`grvResolutionRateByRegion`), `granularity`, `level` (only `grvByAdminLevel`); the admin form
also takes `assigned_dept` and `limit`. An unknown value is a 400 naming the field.

Each public chart is its own literal route, so an admin-only or unknown chart id never reaches
the chart code.

**Authentication.** The OAN dashboards are a Frappe user holding only `Grievance Dashboard Reader`
(no desk access). Generate an API key and secret on that user in the desk, store them in the
dashboards' secret store, and send them as `Authorization: token <api_key>:<api_secret>`. Frappe
checks the key itself (`FrappeTokenAuth` in the spec), so a call without it gets 401 whether it
came through the API gateway or reached the backend directly; the gateway passes the header through. Rotate or
revoke by regenerating the key on the user.

### Officer view (STG-330)

`GET /api/v1/charts` is the JWT route behind the gateway. A `Grievance Officer` calling it gets the
public charts (an admin-only chart id is a 404), computed from the same rollups, definitions and
refresh as everything above, but only over the cases they can read (`services.dashboard.caller_scope`).
Grievance Admin, System Manager and Administrator are unscoped. A request without a valid bearer
token is 401; a submitter or a Dashboard Reader is 403.

What an officer reads is what their case list shows (`permissions.grievance_query_conditions`):

- **Every Grievance Officer, whatever their rung**: the cases assigned to them or to anyone who
  reports to them, directly or not. A Nodal Officer (L1) reads their own cases, a Senior Nodal
  Officer (L2) theirs and their Nodal Officers'.
- **The top rung (Category Admin) also reads their desks**: every case inside each desk. A case
  that is both on the desk and assigned to them counts once.
- A Review Officer reads the desks they are placed on, as their case list does, and no assigned
  cases unless they also hold the Grievance Officer role.

Before this, only the top rung read anything: an officer below it had no desk to read and saw
zeros while their case list showed cases.

A desk is an area, a department and a category, all optional. The desks are alternatives; within
one desk every part must match. The rollups are grouped by region, category, department and
assignee (Grievance Stat Area also by woreda), so a desk is exact down to region level, and down to
woreda level for `grvByAdminLevel`, and errs towards showing less:

- A desk's area counts as the Regions inside it. A desk on a Zone or a Woreda has no Region
  inside it and counts as the Woredas inside it, for `grvByAdminLevel` only: the other charts
  read the daily table, which has no woreda, so there such a desk contributes nothing. A desk
  below Woreda level (a Kebele) contributes nothing anywhere, rather than its whole woreda.
- A desk narrowed by grievance type or service provider contributes nothing.
- A case waiting on the officer's ruling on a change request is not counted.
- An officer with no usable desk and no assigned case reads zeros.
- `grvByAdminLevel` shows an officer's assigned cases at the widest level of the areas they are
  placed on, and the levels below it, never the country row for an officer placed on a Region.
  An officer placed on no area reads Woreda counts only.

### Cases by administrative level (STG-471)

`grvByAdminLevel` counts grievances at three levels, over the same cases every other chart
counts (today's snapshot, after the caller's scope and filters):

| `level`   | Rows                         | `code`, `name`                           |
| --------- | ---------------------------- | ---------------------------------------- |
| `federal` | one: every case              | the Country area (null when none exists) |
| `region`  | one per region holding cases | region P-code and name                   |
| `woreda`  | one per woreda holding cases | woreda code (or name) and name           |

Each row is `{level, code, name, region_code, count}`, widest level first, then by count. A
woreda row names its region in `region_code`; a federal row has none. `level=region,woreda`
returns only those levels; an unknown level is a 400. The `region` filter narrows all three
levels, so `federal` then totals that region.

Access follows the role and the desks. Grievance Admin, System Manager, Administrator and the
Dashboard Reader (public route, counts only) read all three levels. An officer reads their own
level and the ones below it, because a wider row would count cases outside their desks as one
number:

| Officer's widest desk area  | Levels returned         |
| --------------------------- | ----------------------- |
| none, or the Country        | federal, region, woreda |
| a Region                    | region, woreda          |
| a Zone or a Woreda          | woreda                  |
| a Kebele, or no usable desk | none (empty list)       |

Within the levels shown, counts cover only the cases of the officer's desks (area, department
and category), so a Woreda desk sees that Woreda's row alone. Asking for a level the officer may
not read returns no rows for it rather than an error.

Public charts: `grvKpis`, `grvPerformanceKpis`, `grvMonthlyTrend`, `grvWeeklyTrend`,
`grvNetBacklogTrend`, `grvStatusDistribution`, `grvByCategory`, `grvByAdminLevel`, `grvCategoryResolution`,
`grvAvgResolutionByCategory`, `grvPerformanceScore`, `grvResolutionRateByRegion`, `grvSlaRisk`, `grvPendingDuplicates`, `grvOldestOpen` (age only),
`grvFilterRegions`, `grvFilterCategories`. Admin only: `grvRecent`, `grvFilterDepartments`, and
`grvOldestOpen` with the case named. Row shapes are in `openapi/openapi_v1.yaml` and
`postman/oan_grievance_dashboard_charts_collection.json`.

## Operations

- First build on a site: `after_migrate` queues a full refresh when the rollups are empty. By hand:
  `bench --site <site> execute oan_grievance_service.services.dashboard_rollup.refresh --kwargs "{'full': True}"`.
- A new chart is a builder in `services/dashboard.py` plus an entry in `CHARTS`; a new metric is
  a column on a rollup doctype plus its SQL in `services/dashboard_rollup.py`.
