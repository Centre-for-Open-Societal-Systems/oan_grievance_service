# Operational logging

The grievance service writes one JSON object per line to `logs/oan_grievance_service.log` in the
bench (or to stderr when `FRAPPE_STREAM_LOGGING` is set, which is what a container should do). The
code is in [`services/operational_log.py`](../oan_grievance_service/services/operational_log.py)
and [`api/request_logging.py`](../oan_grievance_service/api/request_logging.py).

It is built on Frappe's own logger, so rotation and stream mode are the framework's. It sits beside
the **Error Log** doctype, which stays the place an administrator reads a traceback in the desk;
the two are written together by `operational_log.log_error`.

## Reading a line

```json
{
  "ts": "2026-11-06T06:10:12.106+00:00",
  "level": "WARNING",
  "logger": "oan_grievance_service",
  "event": "security.auth_failure",
  "correlation_id": "cb272429-f81a-4a6f-9d93-09b7781445de",
  "site": "erpnext16.local",
  "method": "POST",
  "path": "/api/v1/auth/login",
  "status": 401,
  "error_code": "AUTHENTICATION_ERROR",
  "auth_scheme": "none",
  "client_ip": "127.0.0.0",
  "attempted_user": "j***@example.com",
  "attempted_user_ref": "f92cdacbb483"
}
```

`ts` is UTC. `event` is `<area>.<what>`. Every line written during a request or a job carries
`correlation_id`. Fields that are empty are left out.

```bash
# Everything one request did, from the id in its response or in a support ticket
jq -c 'select(.correlation_id == "cb272429-f81a-4a6f-9d93-09b7781445de")' logs/oan_grievance_service.log
# Slow or failing requests
jq -c 'select(.event == "http.request" and (.slow or .status >= 500))' logs/oan_grievance_service.log
# Repeated failed sign-ins for one account (the ref is the same for the same account)
jq -r 'select(.event == "security.auth_failure") | .attempted_user_ref // empty' logs/oan_grievance_service.log | sort | uniq -c | sort -rn
```

## Correlation id

`api/request_logging.begin` is a `before_request` hook. Frappe runs those before the auth hooks, so a
request the auth layer refuses has an id.

Frappe starts the session before it runs `before_request` hooks, and maintenance mode stops a request
before them too. A CSRF or session failure, or a site in maintenance, never reaches `begin`. The
`after_request` hook (`end`) still runs for it: it adopts or mints the id there, returns it in the
header, and logs the request **without `duration_ms`** (nothing started the clock).

- A caller's `X-Request-Id` is adopted when it is 8 to 64 characters of letters, digits and
  `. _ : -`. Anything else (a space, a quote, a line break, too long) is replaced, because an id is
  written into every log line and a free-form one would let a caller forge lines.
- Otherwise a UUID4 is generated.
- It is stored in `frappe.local.request_id`, which `oan_auth_service` already reads. So it is the
  `request_id` in every JSON envelope and the `X-Request-Id` response header, including on 401, 404 and
  framework error pages, which had no id before.
- A scheduled job gets `job-<name>-<12 hex>`, used for every line the job writes.

The APISIX gateway does not generate an id today. Turning on its `request-id` plugin would make the
gateway's own log and this one share ids end to end.

## Events

| Event                                                      | Level   | When                                                                                                                                          |
| ---------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `http.request`                                             | by code | Every request under `/api/v1/`, `/api/method/oan_*`, `/api/resource/Grievance`. See levels below.                                             |
| `security.auth_failure`                                    | WARNING | Response 401. Carries the auth scheme tried and, for a sign-in, the masked account.                                                           |
| `security.permission_denied`                               | WARNING | Response 403.                                                                                                                                 |
| `security.rate_limited`                                    | WARNING | Response 429.                                                                                                                                 |
| `security.temporary_password_reissued`                     | WARNING | An admin issued an officer a temporary password. No password is logged. WARNING so a raised level keeps it.                                   |
| `error`                                                    | ERROR   | Every `operational_log.log_error`: `title`, `exception`, `summary` (last line), `detail` (tail).                                              |
| `sla.clock_started` / `clock_recalculated` / `clock_reset` | INFO    | The SLA window was stamped, moved by a category change, or restarted.                                                                         |
| `sla.clock_paused` / `clock_resumed` / `clock_stopped`     | INFO    | A hold began or ended; the case _entered_ a stopped state (`breached` says whether it was late). Once: Resolved to Closed does not repeat it. |
| `sla.reminder_sent`                                        | INFO    | The 50% or 80% reminder was queued.                                                                                                           |
| `sla.state_timer_expired`                                  | INFO    | A case sat in a state longer than its timer allows (`action` is Escalate or Auto Close).                                                      |
| `escalation.escalated`                                     | INFO    | A case moved up a rung: `rung`, `from_user`, `to_user`, `escalated_at`, `next_escalation_at`, `reason`.                                       |
| `escalation.no_higher_authority`                           | WARNING | A case breached and nobody sits above its assignee.                                                                                           |
| `job.started` / `job.finished` / `job.failed`              | INFO    | The hourly SLA sweeps, with `duration_ms` and `processed`.                                                                                    |

`http.request` is INFO for 2xx and 3xx, WARNING for 4xx and for any request slower than
`grievance_slow_request_ms`, ERROR for 5xx. The two uptime endpoints (`/grievances/health`, `/ping`)
are DEBUG when they succeed, so a monitor polling every few seconds does not fill the log.

A rung with nobody on it, a failed escalation, a failed sweep item and the other existing failure
paths are `error` events with the title they always had, and also Error Log rows.

**Error rows and lines.** `log_error` is called from `except` blocks, so it never raises: if the Error
Log insert fails, the line says `error_log_failed` instead. The title is cut to the 140 characters the
column holds, marker included. With no message the row holds what `frappe.log_error` would have stored,
the traceback _with each frame's variables_, scrubbed: variables named for a credential or a personal
field (`submitter_name`, `contact_email`, `description`, `password`...) are blanked, as are personal keys
inside a dict dump. When text is too long the **end** is kept (the exception message is a traceback's last
line): up to 30,000 characters in the row, and in the line the last line as `summary` plus up to 4,000
characters of the plain traceback as `detail`.

## What is never logged

- Request or response **bodies**. Only the _names_ of the fields sent (`param_names`) and the byte sizes.
- **Header values**, except `User-Agent` (trimmed to 120 characters and scrubbed). The `Authorization`
  and `Cookie` headers are never read beyond the scheme word (`bearer`, `token`, `basic`, `cookie`).
- **Query and form values**, and the response `message` (an error message can echo what the caller sent).
  An error response contributes only its `code`.
- Passwords, tokens (access, refresh, API key and secret, CSRF), OTPs, session ids, `Authorization`
  and `Cookie` values, in any field. This holds even if a caller passes one by mistake.

What is logged is masked:

| Data                              | Logged as                                                                           |
| --------------------------------- | ----------------------------------------------------------------------------------- |
| User id, officer, assignee        | `j***@example.com`, `***67` for a phone-derived id, plus `user_ref`                 |
| Email, mobile number              | `j***@example.com`, `***44` (last two digits)                                       |
| Submitter and other names         | `A*** K***`                                                                         |
| Fayda ID, registration, account   | `***56` (last two characters)                                                       |
| Description, address, message ... | `[MASKED]`                                                                          |
| Client address                    | IPv4 to its /24 (`196.188.12.0`), IPv6 to its /48                                   |
| Path                              | emails and runs of 10 or more digits are masked (an officer is `/officers/<email>`) |

`user_ref` is the first 12 hex characters of an HMAC-SHA256 of the lower-cased user id, keyed with
the site's `encryption_key` (the database name if a site has none). It is stable (the same account gives the same ref, so failed sign-ins can be
counted per account) and cannot be reversed or computed for a known address without the key.

Every user field gets its ref automatically: `assigned_to`, `from_user`, `to_user`, `target_user`, `user`,
`attempted_user` and the rest of the user keys are written masked, with `<field>_ref` beside them. Two
officers who mask alike (`a***@example.com`) have different refs, so you can tell who a case moved between.

Free text (exception messages, tracebacks, paths) goes through `scrub_text`, which finds things by
their shape:

- **Credentials:** a JWT; an `Authorization` header or dict key with its value (`Bearer`, `Basic` or
  `token` scheme); a long opaque `Bearer <token>`; and `password=...`-style pairs for password, secret,
  token, api key, OTP and similar names. The plain words _token_, _basic_ and _bearer_ in a sentence
  ("Invalid token provided") are left alone.
- **Emails.**
- **Phone numbers:** Ethiopian mobiles (`0911223344`, `+251911223344`), an international number with a
  `+` (spaced, dashed or run together), a national number written in groups (`0911 223 344`), and a
  16-digit Fayda id. Epoch times, other long ids, dates and a nine-character ticket number are left alone.

Identifying a case by `grievance` and `ticket_number` is intentional: neither is personal data. A
case's own `submitter_name`, `contact_mobile` and `contact_email` are never passed to a log call.

## Adding a log line

```python
from oan_grievance_service.services import operational_log as oplog

oplog.info("notification.dispatched", grievance=g.name, channel="SMS")
oplog.sla_event("clock_started", grievance, sla_days=5)           # adds the case's identifying fields
oplog.escalation_event("escalated", grievance, rung="Desk Head")
oplog.security_event("something_odd", user=frappe.session.user)  # WARNING by default

try:
    ...
except Exception:
    oplog.log_error(title="What failed")                         # not frappe.log_error
```

Use `oplog.log_error` instead of `frappe.log_error`: it stores the same Error Log row, but scrubbed and
with the correlation id on its first line, and writes the structured line. Do not log a body, a
header, a whole document or `frappe.form_dict`; pick the identifying fields. Values you do pass are
redacted by key name and pattern, which is a safety net, not a licence.

A sweep or other background entry point is wrapped with `@oplog.logged_job("name")`.

## Configuration

| Key                          | Default  | Meaning                                                                     |
| ---------------------------- | -------- | --------------------------------------------------------------------------- |
| `grievance_log_level`        | `INFO`   | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Frappe's own default would be ERROR. |
| `grievance_log_max_bytes`    | 10000000 | Size of one file before it rotates.                                         |
| `grievance_log_backup_count` | 10       | Rotated files kept (about 110 MB at the defaults).                          |
| `grievance_slow_request_ms`  | 2000     | A request at least this slow is logged as slow, at WARNING.                 |

The first three apply to the one log file every site on the bench shares, so they are read from
**`common_site_config.json`**, once per process; a value in one site's `site_config.json` is ignored,
because otherwise whichever site logged first would set them for all. `grievance_slow_request_ms` is
per site (`site_config.json` or the common file). A level other than `DEBUG`, `INFO`, `WARNING` or `ERROR`
(`NOTSET`, `CRITICAL`, a typo) falls back to `INFO`, and a size or count that is not a sensible number
falls back to its default. Setup is thread-safe: concurrent first requests configure one set of handlers.

`WARNING` is the highest level that is sensible: security events and the password-reissue record are
WARNING, and `ERROR` hides them.

Frappe rotates by size, not by age, so retention is "the last N files". For a time-based policy, ship the
files with logrotate, or set `FRAPPE_STREAM_LOGGING=1` and let the container runtime and log collector
own rotation and retention. The settings are read when the process first logs; restart workers to
change them.

The log is a single file for the bench, not one per site; every line has `site`.

## Cost

Measured on the dev bench, per request over the cost of building the request: about 0.05 ms when the
level means nothing is written (an id, a clock, and a level check), and about 0.55 ms at INFO with a
synchronous file write. A response body is read only for a 4xx or 5xx, and only when it is a small
JSON document.

## Known gaps

- The reason `oan_auth_service` refuses a token (expired, forged, revoked) stays in its Error Log; the
  response and this log say only `401`. Having its `_reject` store a reason code on `frappe.local`
  would let `security.auth_failure` carry it.
- Every refusal still writes an Error Log row in `oan_auth_service` (a database write an
  unauthenticated caller can trigger at will).
- A request that fails before `before_request` hooks run (CSRF, session errors, maintenance mode) is
  logged and given an id by the `after_request` hook, but has no `duration_ms`.
- A 500 raised outside `handle_api_errors` is logged by Frappe in the Error Log without the correlation
  id; the matching `http.request` line (status 500) carries it, and the two can be paired by time and path.
- `oan_auth_service` and the desk write their own logs in their own format.
- Failed and successful logins _inside_ `oan_auth_service` are seen from outside, as HTTP outcomes.
  Account lockout and similar events there are not instrumented by this service.
