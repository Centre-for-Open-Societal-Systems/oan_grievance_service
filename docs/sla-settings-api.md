# SLA settings API: frontend integration guide

Audience: frontend developers building the Administration SLA tab (STG-414). Backend ticket: STG-412.

Two cards on that tab are backed by two records:

| Card                                 | Endpoint                     | Record                                                             |
| :----------------------------------- | :--------------------------- | :----------------------------------------------------------------- |
| Global SLA Policy                    | `/api/v1/sla-policy`         | `Grievance Deferral Policy`, a Single (one per installation)       |
| Per-category SLA (`SlaCategoryCard`) | `/api/v1/sla-configurations` | `Grievance SLA Configuration`, one active row per service category |

## 1. Quick reference

| #   | Method  | Endpoint                              | Purpose                            | Roles                 |
| :-- | :------ | :------------------------------------ | :--------------------------------- | :-------------------- |
| 1   | `GET`   | `/api/v1/sla-policy`                  | Read the global SLA policy         | Admin, Review Officer |
| 2   | `PATCH` | `/api/v1/sla-policy`                  | Update the global SLA policy       | Admin                 |
| 3   | `GET`   | `/api/v1/sla-configurations`          | List per-category SLA settings     | Admin, Review Officer |
| 4   | `PATCH` | `/api/v1/sla-configurations/{config}` | Update one category's SLA settings | Admin                 |

**Admin** means Grievance Admin, System Manager or Administrator. **Review Officer** is the read-only Grievance Review Officer role. Every other role, and guests, get 403.

Auth, the success and error envelopes, and the strictness rules (unknown body fields and query parameters return 400) are the same as in [taxonomy-api.md](taxonomy-api.md) section 2. List responses carry `data.pagination`.

## 2. Same data as Category Assignments (STG-404)

`sla_days` and `auto_escalate` on `/api/v1/category-assignments` and on `/api/v1/sla-configurations` are **one stored value**, not two. Both read and write the category's `Grievance SLA Configuration`, except `sla_days`, which is stored on the category's base `Grievance SLA Rule` (STG-514, see [sensitive-urgent-handling.md](sensitive-urgent-handling.md)): the SLA rules are the one source of SLA days, and these endpoints read and write the base rule's. Departments that serve the same category share that row, so:

- a PATCH here shows up in `GET /api/v1/category-assignments` on the next read, and the other way round;
- a configuration lists every department with an active category assignment for its category in `departments`;
- there is one configuration per category, not one per department. The mock's "category + department" cards are one card per category with its departments listed.

`notify_on_breach` is new with this API and lives on the same row. Category assignments do not return it.

Why not a second table: routing reads that row (`sla.resolve_policy`), so any copy would be a setting that looks editable and changes nothing.

## 3. Global policy

### `GET /api/v1/sla-policy`

```json
{
  "status": "success",
  "message": "Global SLA policy retrieved",
  "data": {
    "policy": {
      "max_deferral_days": 30,
      "auto_escalation_threshold": 100,
      "requires_supervisor_approval": true,
      "modified": "2026-10-08T16:29:56.801747"
    }
  }
}
```

An installation that never saved the policy returns the defaults: 30 days, 100 %, and `requires_supervisor_approval` true.

| Field                          | Type | Meaning                                                                                       | Card control                      |
| :----------------------------- | :--- | :-------------------------------------------------------------------------------------------- | :-------------------------------- |
| `max_deferral_days`            | int  | Most days a deferral may add to the SLA clock, 30 at most                                     | Max SLA Deferral (days)           |
| `auto_escalation_threshold`    | int  | Stored and returned. Escalation always waits for the full SLA window (see below)              | Auto-escalate Threshold (%)       |
| `requires_supervisor_approval` | bool | `true`: a senior officer decides a deferral. `false`: the assigned officer approves their own | Deferral Approval Policy (radios) |

### `PATCH /api/v1/sla-policy`

Send any of the three fields. Omitted fields stay as they are. The body must not be empty. Respond with the saved policy in the same shape as the `GET`. The **Save Global Policy** button sends the whole form in one call.

```json
{ "max_deferral_days": 21, "auto_escalation_threshold": 80, "requires_supervisor_approval": false }
```

| Field                          | Rule              |
| :----------------------------- | :---------------- |
| `max_deferral_days`            | integer, 1 to 30  |
| `auto_escalation_threshold`    | integer, 1 to 100 |
| `requires_supervisor_approval` | boolean           |

What a change does:

- `max_deferral_days` and `requires_supervisor_approval` apply to the next deferral request or approval. No deferral may exceed 30 days, whatever the ceiling: a higher value is refused with 400.
- `auto_escalation_threshold` no longer changes when a case escalates (STG-516). A case escalates automatically only once its full SLA window has elapsed, so the first escalation is armed at the SLA deadline. Before, a value below 100 armed it that share of the way into the window (3 % of a one-day SLA escalated a case 43 minutes after filing). The warnings before the deadline are the 20 %, 50 % and 80 % reminders. The value is still stored and returned, pending a decision on whether to drop it.

A category can still set its own threshold in the Desk (`Grievance SLA Configuration`, field Auto Escalation Threshold). That is an override, and 0 means "follow the global one". The SLA tab does not edit it.

## 4. Per-category settings

### `GET /api/v1/sla-configurations`

Query parameters, all optional: `service_category` (name or ticket code), `department`, `page`, `page_size`. `department` keeps the categories that department has an active category assignment for. Rows are ordered by service category. Only **active** configurations are listed, because those are the ones routing enforces.

```json
{
  "status": "success",
  "message": "SLA configurations retrieved",
  "data": {
    "sla_configurations": [
      {
        "name": "GR-SLA-0003",
        "service_category": "Inputs",
        "departments": ["Inputs Supply & Distribution Agency"],
        "sla_days": 14,
        "auto_escalate": true,
        "notify_on_breach": true,
        "modified": "2026-10-08T16:28:13.552116"
      }
    ],
    "pagination": { "page": 1, "page_size": 20, "total_count": 1, "total_pages": 1, "has_next": false, "has_prev": false }
  }
}
```

| Field              | Meaning                                                                                                                                 | Card control                 |
| :----------------- | :-------------------------------------------------------------------------------------------------------------------------------------- | :--------------------------- |
| `name`             | The configuration id. Use it in the `PATCH` path                                                                                        |                              |
| `service_category` | Fixed once created                                                                                                                      | Service Category (read-only) |
| `departments`      | Departments with an active category assignment for this category, by name                                                               | department line              |
| `sla_days`         | Working days from the start of the clock to the deadline                                                                                | SLA Days                     |
| `auto_escalate`    | `true`: a case that reaches the threshold is handed up the chain. `false`: the clock, reminders and reporting run, nobody is reassigned | Auto-escalate on breach      |
| `notify_on_breach` | `true`: an escalation queues the SLA breach notification. `false`: the case still escalates, the notification is not sent               | Notify on breach             |

### `PATCH /api/v1/sla-configurations/{config}`

`{config}` is the `name` from the list. Send any of `sla_days` (integer, 1 or more), `auto_escalate`, `notify_on_breach`. Omitted fields stay as they are. The body must not be empty. Responds with the saved record under `data.sla_configuration`, with the same fields as a list row.

```json
{ "sla_days": 10, "auto_escalate": false }
```

An unknown or inactive `{config}` returns 404. Sending `service_category` or any other field returns 400.

Changing `sla_days` does not move the deadline of cases that already have one. It applies to cases whose clock starts, or is recalculated for a category change, after the change. This is the same behaviour as saving an SLA window from the category assignment.

The mock's second toggle, "Notify on escalation", has no field. It repeats the same SLA breach notification as "Notify on breach", so the SLA tab needs one toggle.

## 5. Not covered here

- There is no separate create or delete endpoint. A configuration is created either atomically with a service category (`POST /api/v1/service-categories` with `sla_days`) or with the first category assignment for a category (`POST /api/v1/category-assignments`).
- First-response hours, update cadence, the holiday list and state timers stay on the Desk form.
- The mock's grievance type dropdown has no effect on the SLA. One window applies to every grievance type in a category.
