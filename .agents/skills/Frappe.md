# The Frappe Backend API Rule Book

### Standard for Scalable, Secure, Efficient & Maintainable APIs — Billion-Dollar Architecture Bar

**Purpose:** This is a binary checklist, not a style guide. Every rule should be checkable as `PASS` / `FAIL` / `N/A` — during design, during code review, and post-deployment as a maturity audit. If a rule can't be answered with evidence (a line of code, a log, a dashboard), treat it as `FAIL`.

**How to use this document (for AI or engineer):**

1. Before writing an endpoint → read §1–§4 (design + security gates).
2. While writing → apply §5–§9 as constraints, not suggestions.
3. Before merge → run the §14 Pre-Merge Gate.
4. Post-deployment, quarterly → run the §15 Production Maturity Audit.

---

## §1. API Contract & Design Rules

- [ ] Every endpoint is registered via `@frappe.whitelist()` explicitly — nothing is reachable by accident through generic `/api/resource/<doctype>` unless it is intentionally meant to be a generic CRUD surface.
- [ ] `allow_guest=True` is used **only** for endpoints that are provably safe to be public (health checks, webhooks with signature verification, public read-only catalogs). Every `allow_guest=True` has a comment justifying why.
- [ ] HTTP method is declared explicitly (`methods=["POST"]` etc.) — never leave a whitelisted function open to both GET and POST if it mutates state. State-changing operations must never be reachable via GET.
- [ ] Request/response contracts are documented (OpenAPI/Swagger, or at minimum a docstring with `Args`, `Returns`, `Raises`, `Permissions required`).
- [ ] Versioning strategy exists (`/api/method/myapp.api.v1.xxx` or header-based) before the first breaking change is ever needed — never version reactively.
- [ ] Response shape is consistent across all endpoints: same envelope (`{"data":..., "message":..., "success":...}` or Frappe's native `frappe.response`), same error shape everywhere.
- [ ] Idempotency keys are supported for all POST/PUT endpoints that create resources or trigger payments/webhooks (`Idempotency-Key` header, stored and checked against `frappe.cache()` or a dedicated doctype).
- [ ] Pagination is mandatory on every list endpoint — `limit_start` / `limit_page_length` (or cursor-based for high-write tables) — with a hard max page size enforced server-side (never trust client-supplied page size blindly).
- [ ] Filters passed from client are allow-listed against known fields — never pass raw client-supplied filter dicts straight into `frappe.get_all(filters=...)` without validating fields/operators.
- [ ] Every endpoint fails closed: if permission-check logic throws an unexpected exception, the request is rejected, not allowed through.

## §2. Authentication & Authorization

- [ ] Every whitelisted method that isn't `allow_guest` explicitly checks `frappe.session.user != "Guest"` at the top, even though the framework should already block this — defense in depth.
- [ ] Authorization is checked at **object level**, not just method level. (`frappe.has_permission("Doctype", doc=doc, ptype="read")` — not just "user is logged in.") This is the #1 real-world API breach vector (IDOR / Broken Object Level Authorization).
- [ ] Role-based access uses Frappe's Role + Permission Query Conditions (`permission_query_conditions` hook) for row-level security — never filtered only in Python after fetching all rows.
- [ ] API key/secret auth (`Authorization: token api_key:api_secret`) is used for service-to-service; OAuth2/JWT for user-delegated third-party access. Never share a single shared secret across multiple external consumers.
- [ ] Tokens/API keys are scoped — a key issued for "read reports" cannot delete records. If Frappe's native token model can't scope this, a custom permission layer / dedicated "API User" role with minimal roles is used.
- [ ] Password/API secret fields are stored using Frappe's encrypted `Password` fieldtype — never a plain `Data` field, never logged, never returned in any API response (`fields` explicitly excludes them, not just relying on `in_list_view=0`).
- [ ] Session fixation / CSRF: CSRF tokens are enforced for all cookie-authenticated (browser session) state-changing requests; token/API-key authenticated requests (service-to-service) are explicitly exempted by design, not by accident.
- [ ] Rate-limit login and OTP endpoints specifically (separate, stricter bucket) to prevent credential stuffing / brute force, independent of general API rate limits.
- [ ] Multi-factor auth is enforced for admin/System Manager roles at minimum.
- [ ] Every privilege escalation path (role change, permission change, impersonation/"Login As") is logged to an immutable audit trail with actor, target, timestamp, reason.

## §3. Input Validation & Data Integrity

- [ ] Every input parameter is type-checked and range/length-validated before use — never trust `frappe.form_dict` or `**kwargs` blindly.
- [ ] Validation happens both at the API layer (fail fast, clear error) **and** at the Doctype layer (`validate()` controller hook) — API-layer validation is UX, Doctype-layer validation is the real integrity guarantee, because doctypes can be written to from other paths (scripts, imports, other apps).
- [ ] All SQL is parameterized. Raw `frappe.db.sql()` with string-formatted/concatenated user input is an automatic `FAIL` — always use `%s` placeholders with a values tuple/list, never f-strings or `.format()` on SQL text.
- [ ] Prefer the ORM (`frappe.get_all`, `frappe.get_doc`, `frappe.qb`) over raw SQL unless there's a proven performance reason; if raw SQL is used, it's commented with why the ORM wasn't sufficient.
- [ ] File uploads: MIME type, extension, and actual file signature (magic bytes) are all validated — extension alone is not trusted. Max file size enforced. Uploaded files are scanned/sandboxed if executable content is remotely plausible.
- [ ] All user-supplied strings that are rendered back (in emails, PDFs, print formats, portal pages) are escaped for the target context (HTML-escaped, not just "trimmed") to prevent stored XSS.
- [ ] Numeric fields validate bounds (no negative quantities/prices where nonsensical, no overflow-inducing values passed into `qty * rate` type calculations).
- [ ] Bulk/batch endpoints cap the number of records per request (e.g., max 500 rows) to prevent memory exhaustion and long-held DB locks.
- [ ] Idempotent writes: duplicate submission of the same logical request (retry after timeout) must not create duplicate documents — enforced via idempotency key or a natural unique constraint, not "hope the client doesn't retry."

## §4. Data Security & Privacy

- [ ] PII/sensitive fields (Aadhaar, SSN, card numbers, health data) are encrypted at rest at the field level, not just relying on disk-level encryption.
- [ ] `site_config.json` secrets (DB password, encryption key, API keys) are never committed to version control; production secrets are injected via secret manager / environment, and `.gitignore` is verified to exclude `site_config.json`.
- [ ] `encryption_key` in site_config is backed up separately and securely — losing it means losing every encrypted field permanently. A documented key-rotation and backup procedure exists.
- [ ] Data returned by any API is the minimum necessary (no `SELECT *` equivalents in `frappe.get_all` — always pass explicit `fields=[...]`) to avoid leaking internal-only fields (cost price, internal notes, other customers' data via a joined child table).
- [ ] Multi-tenant data isolation is verified: for any Frappe multi-bench/multi-site setup, cross-site data leakage is impossible by architecture (separate DB per site), and single-DB multi-company setups use enforced company-level Permission Query Conditions on every doctype that holds cross-company data.
- [ ] Backups are encrypted, access-controlled, and tested with an actual restore drill (not just "backup job ran successfully") at least quarterly.
- [ ] A documented data-retention and deletion policy exists, and a "right to be forgotten" / hard-delete path exists for regulated data (GDPR/DPDP compliance) that also purges linked child tables, versions, and comments.
- [ ] Webhooks sent to third parties are signed (HMAC signature header) and third-party webhooks received are signature-verified before processing — no webhook payload is trusted merely because it arrived on the expected URL.
- [ ] Logs never contain raw passwords, tokens, card numbers, or full PII — verified via log-scrubbing / structured logging with an explicit denylist of fields.

## §5. Performance & Query Efficiency

- [ ] No N+1 queries: loops that call `frappe.get_doc()` or `frappe.db.get_value()` per row of a list are replaced with a single bulk `frappe.get_all()` / `frappe.qb` join, or `frappe.get_all(..., fields=["name","field1","field2"])` fetched once.
- [ ] Every `frappe.get_all` / `frappe.get_list` call specifies explicit `fields` — never omitted (which pulls all columns) in a hot path.
- [ ] Indexes exist on every field used in `filters`, `order_by`, or joins for high-row-count doctypes — checked in the Doctype's JSON (`"search_index": 1`) or via a custom DB migration, not assumed.
- [ ] `frappe.db.count()` is used instead of fetching all rows and taking `len()` when only a count is needed.
- [ ] Heavy read endpoints use `frappe.cache()` (Redis) with a sane TTL and explicit, correct cache invalidation on writes (cache keys are namespaced and cleared in `on_update`/`after_insert` hooks) — no stale-forever caches, no cache stampede on expiry (use locks or jittered TTL for hot keys).
- [ ] Long-running work (report generation, bulk emails, PDF generation, third-party API calls) is never done synchronously in the request thread — it's pushed to `frappe.enqueue()` on the appropriate queue (`short`/`default`/`long`), and the client is given a job ID to poll or a websocket event to subscribe to.
- [ ] Database transactions are as short as possible — no network calls (emails, third-party APIs) inside a DB transaction/lock window.
- [ ] Row-level locking (`for_update=True` in `frappe.db.get_value`/`frappe.qb`) is used for concurrent-safe counters/stock/ledger updates, with a bounded lock hold time and deadlock-retry logic (Frappe's `retry_on_deadlock` decorator or equivalent).
- [ ] Bulk inserts/updates use `frappe.db.bulk_insert` or batched `db.sql` rather than looping `doc.insert()` one at a time, when inserting hundreds/thousands of rows.
- [ ] Pagination default `limit_page_length` is sane (≤100) and API refuses/clips absurd values (`limit_page_length=999999`) that could cause a full-table scan.
- [ ] Response payload size is bounded — large blobs (files, big JSON blobs) are served via signed URLs / streaming rather than embedded in the JSON response body.
- [ ] `EXPLAIN` has been run on any query touching a table expected to exceed 1M rows, and the plan is verified to use an index, not a full scan.

## §6. Scalability & Concurrency

- [ ] Stateless API layer: no in-process memory used to hold session/business state across requests (workers can be killed/restarted/scaled horizontally without data loss). Shared state lives in Redis or the DB.
- [ ] Background job queues are separated by expected duration (`short`, `default`, `long`) and by tenancy/criticality if volume is high, so one slow tenant/job type can't starve others.
- [ ] Worker count, Gunicorn worker count, and Socket.IO scaling are sized against expected concurrent load and documented (not left at bench defaults for a production billion-request system).
- [ ] Horizontal scalability is proven: the app has been load-tested with more than one web worker / more than one node behind a load balancer, confirming no hidden single-node assumption (local file writes instead of shared storage, in-memory caches instead of Redis, etc.).
- [ ] File storage for user uploads is on shared/object storage (S3-compatible) in any multi-node deployment — never local disk only.
- [ ] Rate limiting is enforced per API key/user/IP (`frappe.rate_limiter` or a reverse-proxy layer) with documented limits, and limit-exceeded responses return proper `429` with `Retry-After`.
- [ ] Circuit breakers / timeouts exist around every outbound third-party API call — a slow/down third party must not cascade into exhausting all app workers.
- [ ] Queue depth, worker lag, and job failure rate are monitored with alerting thresholds — not discovered only when jobs silently pile up.
- [ ] Database connection pool sizing is deliberate and tested under peak concurrency; connection exhaustion has a defined, tested failure mode (fail fast with `503`, not a hang).

## §7. Error Handling, Resilience & Observability

- [ ] Every endpoint has explicit `try/except` around operations that can fail (external calls, parsing, DB writes) — uncaught exceptions never leak Python stack traces to the client in production (`frappe.local.conf.developer_mode` off, generic error message returned, full trace only in `frappe.log_error`).
- [ ] Errors are logged with enough context to debug without reproducing (`frappe.log_error(title=..., message=...)` including request id, user, key input params — minus secrets) and routed to a real error-tracking tool (Sentry or equivalent), not just the Frappe error log table nobody watches.
- [ ] Every response error uses consistent, documented error codes/messages — clients can programmatically distinguish "validation error" vs "not found" vs "permission denied" vs "server error" (proper HTTP status codes, not always `200` with an error field buried in the body).
- [ ] Structured logging (JSON logs with request ID, user, latency, status) is enabled and shipped to a central log system (ELK/Grafana Loki/Datadog) — not just plain text on local disk.
- [ ] Distributed tracing / request IDs are propagated through background jobs so a job failure can be traced back to the originating API call.
- [ ] Health check and readiness endpoints exist (`/api/method/ping` or dedicated) that check DB and Redis connectivity, used by the load balancer/orchestrator to route traffic away from unhealthy nodes.
- [ ] Alerting exists on: error rate spike, p95/p99 latency spike, queue backlog, failed background jobs, DB replication lag — with on-call ownership, not just a dashboard nobody looks at.
- [ ] Retries for transient failures (network blips to third parties) use exponential backoff with a max attempt cap and are logged distinctly from permanent failures.
- [ ] A documented runbook exists for the top 5 most likely production incidents (DB down, Redis down, queue backlog, third-party API down, disk full).

## §8. Code Quality & Maintainability

- [ ] Business logic lives in importable Python modules/functions, not inline inside the whitelisted API function — the API function is a thin wrapper (parse input → call service function → shape output) so logic is unit-testable without HTTP.
- [ ] No hardcoded config (URLs, thresholds, feature flags) — sourced from `frappe.conf` / site config / a Settings doctype.
- [ ] Naming, structure, and folder layout follow one consistent convention across the whole app (documented in a CONTRIBUTING.md/README), not ad hoc per developer.
- [ ] Type hints are used on all new Python functions (`def foo(name: str, qty: int) -> dict:`), checked via `mypy` or equivalent in CI where feasible.
- [ ] Linting (`ruff`/`flake8`/`black`) and pre-commit hooks are enforced in CI — style is not a matter of individual taste or code-review nitpicking.
- [ ] No duplicated business logic between API layer and background job / scheduled job / other API — shared logic is factored into one function called from all entry points.
- [ ] Every non-trivial function has a docstring explaining intent, not just parameters — future maintainers (human or AI) should understand _why_, not just _what_.
- [ ] Dead code, commented-out blocks, and debug `print()`/`frappe.msgprint()` calls are removed before merge.
- [ ] Migrations (patches) are idempotent and safe to re-run; destructive migrations (drop column, etc.) are reversible or have a verified backup checkpoint before running in production.
- [ ] Customizations are done via a proper custom app (version-controlled) — never via one-off UI "Customize Form" changes in a production instance with no corresponding code record.

## §9. Testing

- [ ] Every new API endpoint has unit tests using `FrappeTestCase` covering: happy path, permission-denied path, invalid-input path, and at least one edge case (empty list, max boundary, duplicate submission).
- [ ] Integration tests exercise the actual whitelisted method through `frappe.client` or test HTTP client, not just the underlying Python function, to catch permission/serialization issues that unit tests on raw functions would miss.
- [ ] Test data uses factories/fixtures, not hand-copied production data (never real customer PII in test fixtures).
- [ ] Load/performance tests exist for endpoints expected to carry high traffic, with a defined pass/fail latency threshold (e.g., p95 < 300ms at expected peak concurrency) before shipping.
- [ ] CI runs the full test suite plus linting on every PR; merge is blocked on failure — this is not optional/manual.
- [ ] Security regression tests exist for previously found vulnerabilities (if an IDOR was found and fixed once, a test locks that specific path down forever).
- [ ] Test coverage is measured and tracked (not necessarily 100%, but critical business logic and all permission-checking code paths must be covered).

## §10. Deployment, Config & Environment

- [ ] Separate environments exist: local/dev, staging, production — with staging as close to production topology as feasible (same DB engine version, same Redis config).
- [ ] Deployments are zero/near-zero downtime: `bench migrate` runs are tested against production-scale data volume beforehand (a migration that takes 2 hours locks a live table — this is caught in staging, not discovered in production).
- [ ] Maintenance-mode / graceful draining exists for deploys that require downtime — in-flight requests finish, new ones get a clear "maintenance" response, not a connection reset.
- [ ] Rollback procedure is documented and tested — not just "we'll figure it out if it breaks."
- [ ] Infrastructure is defined as code (Ansible/Terraform/Docker Compose) — production config is not manually SSH'd and tweaked.
- [ ] Dependency versions are pinned (`requirements.txt`/`pyproject.toml` with exact versions or lockfile) — no floating `latest` in production builds.
- [ ] Secrets are injected via a secret manager / environment variables at deploy time, never baked into images or code.
- [ ] A documented, tested disaster-recovery plan exists with defined RPO/RTO targets appropriate to a billion-dollar-scale system (not "we have daily backups" as the entire answer).

## §11. Documentation & Governance

- [ ] Every API is documented (OpenAPI spec or equivalent) with auth requirements, request/response schema, error codes, and rate limits — kept in version control alongside the code, updated in the same PR as the code change.
- [ ] A changelog/versioning record exists for breaking API changes, with a deprecation window communicated to consumers before removal.
- [ ] Architecture decisions (why background job vs sync, why this caching strategy, why this permission model) are recorded (ADR-style) so future engineers/AI don't reverse a deliberate decision by accident.
- [ ] Ownership is clear: every app/module has a named owning team, so security/perf issues have someone to route to.
- [ ] A responsible-disclosure/security-contact process exists for external researchers to report vulnerabilities.

## §12. Frappe-Specific Gotchas Checklist

- [ ] `ignore_permissions=True` is never used in an API-triggered code path unless the permission check has already been explicitly and correctly done manually first — this flag is the single most common way Frappe apps leak data.
- [ ] `frappe.db.commit()` / `frappe.db.rollback()` are used deliberately around multi-step writes — an uncaught exception mid-transaction must not leave partial writes committed.
- [ ] Child table data is fetched with the parent in one call (`frappe.get_doc`) rather than N separate queries per child row when returning nested data.
- [ ] Scheduled jobs (`hooks.py` → `scheduler_events`) have their own error handling and do not silently fail — a failed scheduled job posting financial data is treated with the same severity as a failed API call.
- [ ] `frappe.get_doc(...).save()` vs `frappe.db.set_value(...)` is a deliberate choice: `set_value` skips `validate()`/hooks — used only when that's genuinely intended (bulk background updates), never as a shortcut in user-facing flows that need validation.
- [ ] Naming series / autoname collisions are handled for high-concurrency creation (use Frappe's built-in naming series locking, don't hand-roll a "get max + 1" pattern — classic race condition).
- [ ] Website/portal-facing whitelisted methods (`allow_guest=True` with `xss_safe`) are audited separately since they're the most externally exposed surface.
- [ ] Custom REST integrations don't bypass Frappe's permission engine by talking to the DB directly via a raw psycopg/mysqlclient connection outside the framework.

---

## §13. The Ten Non-Negotiables (if nothing else, these)

1. No unparameterized SQL, ever.
2. No `ignore_permissions=True` without a manual, correct, prior permission check.
3. No synchronous long-running work inside a web request.
4. No `SELECT *`-equivalent field fetching in list APIs.
5. No secrets in version control or in logs.
6. No unindexed queries on tables expected to grow beyond ~100k rows.
7. No endpoint without an automated test for both the happy path and the permission-denied path.
8. No production deploy without a tested rollback path.
9. No client-supplied filter/sort/page-size value trusted without validation/bounding.
10. No silent failure — every error is logged somewhere a human or alert will see it.

---

## §14. Pre-Merge Gate (run on every PR)

| #   | Check                                                      | Pass/Fail |
| --- | ---------------------------------------------------------- | --------- |
| 1   | Whitelisted, method explicitly restricted (GET/POST)       |           |
| 2   | Object-level permission check present                      |           |
| 3   | Input validated (type, bounds, allow-listed filters)       |           |
| 4   | No raw string-interpolated SQL                             |           |
| 5   | No N+1 query pattern introduced                            |           |
| 6   | Explicit`fields=[...]` on all `get_all`/`get_list` calls   |           |
| 7   | Long-running work enqueued, not inline                     |           |
| 8   | Errors caught, logged, generic message to client           |           |
| 9   | Unit + integration tests added, CI green                   |           |
| 10  | No secrets/PII in logs or test fixtures                    |           |
| 11  | API documented (schema, auth, errors)                      |           |
| 12  | Backward compatible, or versioned/deprecation-noted if not |           |

## §15. Production Maturity Audit (quarterly)

| #   | Check                                                  | Evidence Required       |
| --- | ------------------------------------------------------ | ----------------------- |
| 1   | p95/p99 latency within SLA under real peak load        | Dashboard screenshot    |
| 2   | Error rate below defined threshold                     | Dashboard/alert history |
| 3   | Restore-from-backup drill succeeded                    | DR drill log            |
| 4   | No`ignore_permissions` regressions merged              | Code audit/grep         |
| 5   | Rate limiting confirmed effective (test 429 triggers)  | Test log                |
| 6   | Queue backlog stayed within bounds during peak         | Queue metrics           |
| 7   | No PII found in logs (automated scan)                  | Log-scrub report        |
| 8   | Dependency vulnerabilities scanned and patched         | SCA tool report         |
| 9   | Access review: stale API keys/roles revoked            | Access audit log        |
| 10  | Incident postmortems completed for all P1/P2 incidents | Postmortem docs         |

---

**If a rule in this book cannot be satisfied, it must be an explicit, written, reviewed exception with a compensating control — never a silent skip.**
