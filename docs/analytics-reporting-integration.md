# Analytics & Reporting: frontend integration

The Analytics & Reporting screen is fed by the same endpoint as the Dashboard screen,
`GET /api/v1/charts`. Ask for different chart ids.

| Screen                | `charts` value                                                                                                                     |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Dashboard             | `grvKpis,grvStatusDistribution,grvByCategory,grvMonthlyTrend`                                                                      |
| Analytics & Reporting | `grvPerformanceKpis,grvWeeklyTrend,grvResolutionRateByRegion,grvCategoryResolution,grvAvgResolutionByCategory,grvPerformanceScore` |

## Request

```
GET {base}/api/v1/charts?charts=grvPerformanceKpis,grvWeeklyTrend,grvResolutionRateByRegion,grvCategoryResolution,grvAvgResolutionByCategory,grvPerformanceScore&month=2026-04
```

Dev, through the frontend proxy:

```
https://grievance-dev.oanstaging.com/api/proxy/api/v1/charts?charts=grvPerformanceKpis,grvWeeklyTrend,grvResolutionRateByRegion,grvCategoryResolution,grvAvgResolutionByCategory,grvPerformanceScore&month=2026-04
```

Auth is the same as the Dashboard call (the logged-in Grievance Admin or Grievance Officer).
A Grievance Admin sees every case. A Grievance Officer gets the same charts narrowed to
their own desks.

### Query parameters

| Parameter          | Used by                                                                 | Format                   | Notes                                                                                                               |
| ------------------ | ----------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `charts`           | all                                                                     | comma-separated ids      | Up to 20. Omit it and every chart is returned.                                                                      |
| `month`            | `grvPerformanceKpis`, `grvPerformanceScore`                             | `YYYY-MM`                | Defaults to the current month. It cannot be in the future.                                                          |
| `as_of_month`      | `grvResolutionRateByRegion`                                             | `YYYY-MM`                | Optional. Reads the region chart as at the end of that month. Omit it for the all-time snapshot. Not in the future. |
| `from`, `to`       | `grvWeeklyTrend`, `grvCategoryResolution`, `grvAvgResolutionByCategory` | `YYYY-MM-DD`             | Default: last 7 weeks for the weekly chart, last 6 months for the two category charts. The span is at most 2 years. |
| `region`           | all six                                                                 | region code, e.g. `ET14` | Repeat or comma-separate for several (max 100).                                                                     |
| `service_category` | all six                                                                 | category name            | Same rules (max 50).                                                                                                |
| `assigned_dept`    | all six                                                                 | department name          | Admin only. Ignored for officers.                                                                                   |

`month` is read only by the two charts above, so one `month` can go on the whole combined request.
`grvResolutionRateByRegion` is not moved by it and stays an all-time snapshot. The two charts that take
`month` ignore `from`/`to`, and the three that take `from`/`to` ignore `month`.
Unknown region or category values return 400, and so does a malformed or future `month` or `as_of_month`.

## Response

```json
{
  "status": "success",
  "message": "Charts fetched successfully",
  "data": {
    "grvPerformanceKpis": [ ... ],
    "grvWeeklyTrend": [ ... ],
    "grvResolutionRateByRegion": [ ... ],
    "grvCategoryResolution": [ ... ],
    "grvAvgResolutionByCategory": [ ... ],
    "grvPerformanceScore": [ ... ]
  },
  "meta": {
    "as_of": { "grvPerformanceKpis": "2026-10-22T10:15:00+03:00", "...": "..." },
    "filters": { "region": [], "service_category": [], "from": null, "to": "2026-10-22", "assigned_dept": [] },
    "errors": {}
  }
}
```

- `meta.as_of` is when each chart's data was last refreshed. The data comes from rollups
  refreshed every 15 minutes, so it can lag live data by up to that long.
- `meta.unavailable.<id>` is set when a chart is empty because no data was kept, not because
  there are no cases (today only `"no_snapshot"`, from the region chart with `as_of_month`).
  The key is absent otherwise.
- If one chart fails, `data.<id>` is `null` and `meta.errors.<id>` holds the reason. The other
  charts still come back, so handle a `null` per tile rather than failing the whole page.

## Chart reference

### `grvPerformanceKpis`: the four top cards

Always four rows, in this order.

```json
[
  { "metric": "avg_resolution_time", "value": 12.4, "unit": "days",    "delta": -2.1 },
  { "metric": "resolution_rate",     "value": 37.5, "unit": "percent", "delta": 4.2 },
  { "metric": "escalation_rate",     "value": 12.5, "unit": "percent", "delta": 1.1 },
  { "metric": "satisfaction",        "value": 50.0, "unit": "percent", "basis": 2 }
]
```

| UI element                                                            | Field                                                                                                                |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Card value (`12.4`, `37.5%`, `12.5%`, `50%`)                          | `value`                                                                                                              |
| Unit label ("days")                                                   | `unit`                                                                                                               |
| "- 2.1 days last month", "+4.2% vs last month", "+1.1% vs last month" | `delta` (this month minus last month). The unit is days for resolution time and percentage points for the two rates. |
| "Based on 2 responses"                                                | `basis` on `satisfaction` (number of feedback responses)                                                             |

Notes:

- `value` and `delta` can be `null`: no resolved cases in the month for resolution time, no
  submissions for escalation rate, no feedback for satisfaction, or no previous month to compare.
  Show a dash and hide the delta line.
- `satisfaction` has no `delta`, only `basis`.
- The sign decides the colour, and what counts as good depends on the metric. A negative
  resolution-time delta is good, while a positive escalation-rate delta is bad.
- `resolution_rate` is not limited to the month. It is the share of all cases that are Resolved
  or Closed, measured at the end of the selected month. The other three are for the month only.
- `escalation_rate` is of the cases filed in the month: the share that have been escalated, whenever
  the escalation happened. A case filed in March and escalated in April counts in March's rate, so
  the rate never passes 100% and the delta compares like with like. The latest month can still
  rise as its cases are escalated later, so its delta against a settled month can look low.

### `grvWeeklyTrend`: "Weekly Submission vs Resolution"

```json
[
  { "week_start": "2026-09-28", "received": 68, "resolved": 0 },
  { "week_start": "2026-10-05", "received": 27, "resolved": 0 }
]
```

- One row per ISO week (Monday start), oldest first, empty weeks included as zeros. The default is 7 rows.
- `week_start` is a `YYYY-MM-DD` date. Build labels such as "W3 Mar 26" in the frontend.
- Series: `received` (blue) and `resolved` (green).

### `grvResolutionRateByRegion`: "Resolution Rate by Region (%)"

```json
[
  { "region_code": "ET14", "region_name": "Addis Ababa", "resolved": 1, "total": 2, "rate": 50.0 }
]
```

- Sorted by `total` descending, then region code.
- `rate` is a percentage with one decimal and is already multiplied by 100. Use it as the bar width.
- Only regions with at least one grievance are listed. Add the missing regions in the frontend if
  the design needs to show all of them.
- By default this is an all-time snapshot: every case as it stands now. That matches the "All time"
  label on the chart. It ignores `month`, `from` and `to`, so the page's `month` does not change it.
- Send `as_of_month` (not `month`) to read it as it stood at the end of that month instead, from the
  daily snapshots kept since the rollups began. A month with no snapshot kept inside it returns an
  empty array and sets `meta.unavailable.grvResolutionRateByRegion` to `"no_snapshot"`; an empty
  array without that key means the month has no cases. The current month matches the default. Only use it if the chart is relabelled to show
  the month.
- `region` and `service_category` apply either way.

### `grvCategoryResolution`: "Volume & Resolution by Service Category"

```json
[
  { "category": "Inputs", "filed": 121, "resolved": 0, "resolved_on_time": 0, "resolved_breached": 0 }
]
```

- Sorted by `filed` descending.
- `filed` is the volume and `resolved` the resolved count in the window. `resolved_on_time` and
  `resolved_breached` split `resolved` by SLA outcome.
- The window is `from` to `to`, default last 6 months.

### `grvAvgResolutionByCategory`: "Avg Resolution Days by Category"

```json
[
  { "category": "Schemes", "resolved": 40, "avg_resolution_days": 16.8 }
]
```

- The average number of days from filing to resolution, for the cases resolved in the window.
  It is total hours over total resolved cases, so every case weighs the same whatever its
  region or department.
- `avg_resolution_days` has one decimal. `resolved` is the number of cases behind it.
- A category with no resolved case in the window is left out, so there are no `null` averages.
  Add the missing categories in the frontend if the design needs to show all of them.
- Sorted by `resolved` descending, then category.
- The window is `from` to `to`, default last 6 months, the same as `grvCategoryResolution`.

### `grvPerformanceScore`: "System Performance Score" (radar)

```json
[
  { "axis": "timeliness", "score": 50.0, "basis": 2 },
  { "axis": "resolution_rate", "score": 75.0, "basis": 4 },
  { "axis": "satisfaction", "score": 50.0, "basis": 2 },
  { "axis": "transparency", "score": 25.0, "basis": 4 },
  { "axis": "follow_up", "score": 66.7, "basis": 3 },
  { "axis": "coverage", "score": 100.0, "basis": 4 }
]
```

- Always six rows, in the order the radar reads clockwise from the top. `score` is 0 to 100 with one
  decimal. `basis` is the number of cases the score is a share of, for a tooltip.
- `score` is `null` when `basis` is 0. Draw a gap or a dash, not a zero.
- For the month in `month` (default the current month). `resolution_rate` and `coverage` are read at
  the end of the month and need a daily snapshot kept inside it, so they are `null` for months
  before the rollups began. The other four are for the month only.

| Axis              | A score of 100 means                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `timeliness`      | Every case resolved in the month that has an SLA was resolved by its due date.              |
| `resolution_rate` | Every case is Resolved or Closed. Same as the KPI card.                                     |
| `satisfaction`    | Every rating given was 4 or 5. Same as the KPI card.                                        |
| `transparency`    | Every case filed in the month has had a public update from an officer.                      |
| `follow_up`       | Every case resolved in the month was confirmed and rated by its submitter.                  |
| `coverage`        | Every case sits with a real desk: not unassigned, and not on the "Unrouted" catch-all desk. A desk with no department still counts. |

Notes:

- These definitions are the backend's proposal, built from data that exists today. Product should
  confirm them, and the labels shown on the chart should say what is measured.
- `transparency` counts a message, a question to the submitter, or the reason given for an action.
  Internal notes, the submitter's own replies and the submission entry do not count. Like the
  escalation rate it is of the cases filed in the month, so the latest month can still rise.
- `satisfaction` is a share of the ratings given, and `follow_up` is how many resolved cases were
  rated at all. A month can score high on one and low on the other.
- Cases without an SLA are in neither the on-time nor the late count, so they do not affect
  `timeliness`.

## Errors

Error bodies use the standard envelope, with details keyed by field.

| Status    | When                                       | Example                                                          |
| --------- | ------------------------------------------ | ---------------------------------------------------------------- |
| 400       | Bad parameter                              | `details: { "month": "Expected a month as YYYY-MM" }`            |
| 400       | More than 20 charts requested              | `details: { "charts": "At most 20 charts per request" }`         |
| 404       | Unknown chart id                           | `code: "NOT_FOUND"`, `details: { "unknown_charts": ["grvFoo"] }` |
| 401 / 403 | Not logged in, or role not allowed         |                                                                  |
| 429       | More than 120 requests per minute per user |                                                                  |

## Things to check on the frontend

- The page subtitle shows "April 2026". Make sure the `month` you send matches what the page
  displays. If you send nothing, the API uses the current month.
- Handle `null` values and empty arrays. A new environment or an empty month returns them. This
  includes `null` radar scores, and an empty array from the region chart with `as_of_month`
  (check `meta.unavailable` to tell a month with no snapshot from a month with no cases).
- The radar and the four KPI cards use the same `month`, so they always describe the same month.
- The data does not refresh live, so there is no need to poll more often than every few minutes.

## Source

- Endpoint: `oan_grievance_service/api/v1/charts.py`
- Chart builders: `oan_grievance_service/services/dashboard.py`
