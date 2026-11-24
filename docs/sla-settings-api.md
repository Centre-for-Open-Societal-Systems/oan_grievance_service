# SLA settings API: frontend integration guide

Audience: frontend developers building the Administration SLA tab (STG-414). Backend ticket: STG-412.

| Card              | Endpoint             | Record                                                       |
| :---------------- | :------------------- | :----------------------------------------------------------- |
| Global SLA Policy | `/api/v1/sla-policy` | `Grievance Deferral Policy`, a Single (one per installation) |

## 1. Quick reference

| #   | Method  | Endpoint             | Purpose                      | Roles                 |
| :-- | :------ | :------------------- | :--------------------------- | :-------------------- |
| 1   | `GET`   | `/api/v1/sla-policy` | Read the global SLA policy   | Admin, Review Officer |
| 2   | `PATCH` | `/api/v1/sla-policy` | Update the global SLA policy | Admin                 |

**Admin** means Grievance Admin, System Manager or Administrator. **Review Officer** is the read-only Grievance Review Officer role. Every other role, and guests, get 403.

Auth, the success and error envelopes, and the strictness rules (unknown body fields and query parameters return 400) are the same as in [taxonomy-api.md](taxonomy-api.md) section 2.

## 2. Where the per-category settings went

There is no per-category SLA record any more. `Grievance SLA Configuration` and `/api/v1/sla-configurations` were removed:

- **SLA days** are the category's SLA rules (`/api/v1/sla-rules`, see [sensitive-urgent-handling.md](sensitive-urgent-handling.md)). `sla_days` on `/api/v1/category-assignments` and on `POST /api/v1/service-categories` reads and writes the category's base rule, shared by every department that serves it.
- **`auto_escalate` and `notify_on_breach`** are each category assignment's (the desk, `Grievance RBAC Assignment`), read and written on `/api/v1/category-assignments`. A case follows the flags of the desk that routed it (`routing_rule`), read when its escalation is armed and when it breaches. A case no desk has routed has both on.
- **State timers** (how long a case may sit in Resolved or More Info Needed, and what happens then) are on the `Grievance Deferral Policy` Desk form and apply to every case.
- **The holiday list** is the default `Grievance Holiday List` (`is_default`).
- The per-category threshold, first-response hours, update cadence and remand hours are gone. The three hour fields were never read, and the escalation threshold was removed in STG-516 (cases escalate at their SLA deadline).

## 3. Global policy

### `GET /api/v1/sla-policy`

```json
{
  "status": "success",
  "message": "Global SLA policy retrieved",
  "data": {
    "policy": {
      "max_deferral_days": 30,
      "requires_supervisor_approval": true,
      "modified": "2026-10-08T16:29:56.801747"
    }
  }
}
```

An installation that never saved the policy returns the defaults: 30 days and `requires_supervisor_approval` true.

| Field                          | Type | Meaning                                                                                       | Card control                      |
| :----------------------------- | :--- | :-------------------------------------------------------------------------------------------- | :-------------------------------- |
| `max_deferral_days`            | int  | Most days a deferral may add to the SLA clock                                                 | Max SLA Deferral (days)           |
| `requires_supervisor_approval` | bool | `true`: a senior officer decides a deferral. `false`: the assigned officer approves their own | Deferral Approval Policy (radios) |

### `PATCH /api/v1/sla-policy`

Send either field or both. Omitted fields stay as they are. The body must not be empty. Respond with the saved policy in the same shape as the `GET`. The **Save Global Policy** button sends the whole form in one call.

```json
{ "max_deferral_days": 21, "requires_supervisor_approval": false }
```

| Field                          | Rule                |
| :----------------------------- | :------------------ |
| `max_deferral_days`            | integer, at least 1 |
| `requires_supervisor_approval` | boolean             |

What a change does:

- `max_deferral_days` and `requires_supervisor_approval` apply to the next deferral request or approval. `max_deferral_days` is the only limit on a deferral's length; it ships at 30 days, and the `cap_max_deferral_days` patch set any site above 30 back to 30 (STG-516).
- There is no escalation threshold (removed in STG-516). A case escalates automatically only once its full SLA window has elapsed, so the first escalation is armed at the SLA deadline. The warnings before the deadline are the SLA reminders at 20 %, 50 % and 80 % of the window. A request that still sends `auto_escalation_threshold` is refused with 400, like any unknown field.

## 4. Not covered here

- State timers stay on the Desk form of `Grievance Deferral Policy`.
- The mock's grievance type dropdown has no effect here: SLA days by type are SLA rules.
