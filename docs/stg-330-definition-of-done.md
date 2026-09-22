# STG-330 Definition of Done

| Criterion                | Status  | Evidence                                                                                                                                     |
| ------------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Code reviewed            | Pending | Implement Dashboard Statistics API + FR-09 projection on `feature/STG-330`.                                                                  |
| Unit tests passed        | Pending | `bench --site <site> run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_dashboard_stats` after `bench migrate`. |
| Documentation updated    | Done    | `docs/api-development-standards.md` §4.2; Postman REST + RPC collections updated.                                                            |
| QA passed                | Pending | Cover admin unrestricted totals, officer scope filter, projection lag (no live scan), role deny for submitter/guest.                         |
| No Critical/High defects | Pending | Projection-backed reads; RBAC scope applied; dashboard roles enforced.                                                                       |

## Run DoD verification (WSL)

```bash
cd ~/frappe16-bench
bench --site erpnext16.local migrate
bench --site erpnext16.local run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_dashboard_stats
```

Optional: rebuild projection immediately after migrate:

```bash
bench --site erpnext16.local execute oan_grievance_service.tasks.refresh_dashboard_projection
```
