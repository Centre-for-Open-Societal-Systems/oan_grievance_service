# Dashboard analytics

The grievance dashboards (the officer/admin dashboard and the public OAN programme
dashboards) are served from rollup tables that the scheduler refreshes every 15
minutes. No dashboard request ever aggregates `tabGrievance`.

## Data flow

```
every 15 min (cron */15)      services/dashboard_rollup.refresh()
  tabGrievance, Status History, Duplicate
      -> Grievance Stat Daily      events per day (last 3 days rebuilt; all nightly)
      -> Grievance Stat Snapshot   status counts per day (today replaced; history kept)
      -> Grievance Stat Area       cases per region and woreda (whole table replaced; no history)
      -> as_of (system default)

request                        services/dashboard.get_chart()
  Redis cache per chart + parameters, keyed on as_of
      -> rows from the rollups only
```

- **Grievance Stat Daily** holds counts per `stat_date, region, service_category, assigned_dept`:
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
  the rest.
- **Grievance Stat Snapshot** holds how many cases stood in each status on a day, by the same
  dimensions plus `escalated`, `has_desk` (the case is not on the Unrouted desk and has a
  department, a routing rule or an officer; behind the coverage axis), `sla_state` (ok / at_risk / breached / paused / no_sla),
  cases awaiting duplicate review, and the oldest open, non-escalated filing date. Today's rows
  are replaced on every refresh; earlier days are kept, because a past day's status counts
  cannot be rebuilt from current data.
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
- Refreshes never overlap: the 15-minute refresh, the nightly rebuild and the first build after
  migrate all queue one background job under a shared job id, and a run is not queued while
  another is queued or running. A nightly rebuild skipped that way is picked up the next night.

Staleness: at most 15 minutes here, plus whatever the consumer caches (the OAN dashboards
cache another 15 minutes).

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

| Route                           | Access                      | Returns                                                             |
| ------------------------------- | --------------------------- | ------------------------------------------------------------------- |
| `GET /api/v1/charts/<chart_id>` | Dashboard Reader            | one chart; public charts only; counts, never case detail            |
| `GET /api/v1/charts?charts=...` | Grievance Admin             | several charts per call, admin-only charts and live detail          |
| `GET /api/v1/charts?charts=...` | Grievance Officer (JWT too) | the public charts, narrowed to the caller's RBAC desks; counts only |

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
refresh as everything above, but only over the cases inside their active RBAC desks
(`services.dashboard.caller_scope`). Grievance Admin, System Manager and Administrator are
unscoped. A request without a valid bearer token is 401; a submitter or a Dashboard Reader is 403.

A desk is an area, a department and a category, all optional. The desks are alternatives; within
one desk every part must match. The rollups are grouped by region, category and department
(Grievance Stat Area also by woreda), so the scope is exact down to region level, and down to woreda
level for `grvByAdminLevel`, and errs towards showing less:

- A desk's area counts as the Regions inside it. A desk on a Zone or a Woreda has no Region
  inside it and counts as the Woredas inside it, for `grvByAdminLevel` only: the other charts
  read the daily table, which has no woreda, so there such a desk contributes nothing. A desk
  below Woreda level (a Kebele) contributes nothing anywhere, rather than its whole woreda.
- A desk narrowed by grievance type or service provider contributes nothing.
- A case an officer holds only through their reporting chain is not counted.
- An officer on no usable desk reads zeros.

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
