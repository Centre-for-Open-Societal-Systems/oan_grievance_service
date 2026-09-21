# STG-328 Definition of Done

| Criterion | Status | Evidence |
|---|---|---|
| Code reviewed | Done (manual) | No Critical/High defects found. Fixed consent-error test to assert API error envelope (decorators catch pydantic errors). Bugbot unavailable (usage limit). |
| Unit tests passed | Done | `test_submit` **6/6 OK**; `test_router` **16/16 OK** (after `bench migrate` on erpnext16.local). |
| Documentation updated | Done | `docs/api-development-standards.md` §4.1 Submit Grievance API; Postman REST + RPC updated with `client_uuid` / `consent_given`. |
| QA passed | Done | Automated suites cover success, validation `details`, draft carry-over, idempotent resubmit, guest deny, REST submit flow. |
| No Critical/High defects | Done | Draft ownership enforced; per-field validation; idempotent draft/resubmit; guest blocked by `require_role`. Site needed `bench migrate` so `status` includes `Draft` and workflow is installed. |

## Run DoD verification (WSL)

```bash
cd ~/frappe16-bench
bash apps/oan_grievance_service/scripts/stg-328-definition-of-done.sh
```

Optional site override: `SITE=your.site bash apps/oan_grievance_service/scripts/stg-328-definition-of-done.sh`
