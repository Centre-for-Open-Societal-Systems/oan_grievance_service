# STG-330 Definition of Done

| Criterion                | Status  | Evidence                                                                                                                                     |
| ------------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Code reviewed            | Pending | Implement Dashboard Statistics API + FR-09 projection on `feature/STG-330`.                                                                  |
| Unit tests passed        | Pending | `bench --site <site> run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_dashboard_stats` after `bench migrate`. |
| Documentation updated    | Done    | `docs/api-development-standards.md` §4.2; Postman REST collection (get + refresh). Hourly scheduler + admin refresh endpoint.                |
| QA passed                | Pending | Cover admin unrestricted totals, officer scope filter (SQL), projection lag, role deny, admin refresh.                                       |
| No Critical/High defects | Pending | Projection-backed reads; RBAC in SQL; compatible with post-#19 constants; dashboard roles enforced.                                          |

## Run DoD verification (WSL)

```bash
cd ~/frappe16-bench
bench --site erpnext16.local migrate
bench --site erpnext16.local run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_dashboard_stats
```

Projection rebuilds hourly via the scheduler. Optional immediate rebuild after migrate:

```bash
bench --site erpnext16.local execute oan_grievance_service.tasks.refresh_dashboard_projection
# or: POST /api/v1/dashboard-statistics/refresh (Grievance Admin+)
```
