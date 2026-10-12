# Dashboard statistics API (STG-330)

KPI cards and chart series for the officer and admin dashboards (FR-09 / FR-11.2). Unlike the
public chart API in [dashboard-analytics.md](dashboard-analytics.md), the figures here are
**scoped to the caller's RBAC desks**.

They are read from the `Grievance Dashboard Projection` summary table, never from a live
`COUNT(*)` over `Grievance`. The scheduler rebuilds the projection hourly
(`tasks.refresh_dashboard_projection`), and an admin can rebuild it at once.

## Endpoints

|      |                                                                         |
| ---- | ----------------------------------------------------------------------- |
| REST | `GET /api/v1/dashboard-statistics?months=12`                            |
| RPC  | `GET /api/method/oan_grievance_service.api.v1.dashboard.get_statistics` |
| Auth | Bearer token; `Grievance Officer`, `Grievance Admin`, System Manager    |

|      |                                                                              |
| ---- | ---------------------------------------------------------------------------- |
| REST | `POST /api/v1/dashboard-statistics/refresh`                                  |
| RPC  | `POST /api/method/oan_grievance_service.api.v1.dashboard.refresh_projection` |
| Auth | Bearer token; `Grievance Admin`, System Manager, Administrator               |

Neither route is a guest route. A request without a valid bearer token is answered `401` on
both transports; a caller without one of the roles above is answered `403`. Submitters and the
Review Officer role are refused.

**Query** (statistics only): `months` (int, 1-36, default 12) is the length of `monthly_trend`.
Any other parameter is a `400`.

## Scoping

An Administrator, System Manager or Grievance Admin sees every case. An officer sees the cases
inside each of their active RBAC desks: the officer's administrative area (and everything under
it), the desk's department and its category. An officer on no active desk sees nothing.

The projection is grouped by area, department, category and status only, so two things in the
case list's rules are not mirrored, and both err towards showing less:

- A desk narrowed by grievance type or service provider counts nothing, rather than counting the
  whole department.
- A case an officer holds only because it is assigned to someone in their reporting chain is not
  counted.

## Response `data`

| Key              | Consumer                      | Contents                                                                                                       |
| ---------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `kpis`           | `KpiCards.tsx`                | `total`, `open`, `pending`, `in_progress`, `under_review`, `resolved`, `rejected`, `sla_breached`, `escalated` |
| `by_status`      | `StatusDistributionChart.tsx` | `[{ status, count }, …]`                                                                                       |
| `by_category`    | `ServiceCategoryChart.tsx`    | `[{ category, count }, …]`                                                                                     |
| `sla_breach`     | KPI / SLA card                | `breached`, `within_sla`, `open_breached`                                                                      |
| `monthly_trend`  | `MonthlyTrendChart.tsx`       | `[{ month, label, submitted, resolved }, …]`                                                                   |
| `scope` / `meta` | diagnostics                   | Applied RBAC scope; `source: "projection"`, `snapshot_at`                                                      |

## Refreshing

```bash
bench --site erpnext16.local execute oan_grievance_service.tasks.refresh_dashboard_projection
# or: POST /api/v1/dashboard-statistics/refresh (Grievance Admin and above)
```

New grievances do not appear until the next refresh, so figures lag by up to an hour.
