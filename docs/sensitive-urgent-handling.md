# Sensitive and urgent handling (STG-514)

A grievance sub-type can be **Sensitive**. A case's **SLA days** and **urgency** (with its **priority**, which follows urgency) come from one place, the **SLA rules** of its category, and can differ by sub-type, by area and by season. The service decides this once, when the case is filed, and the case carries the answer. Nothing in the portal works out a season.

The service holds the mechanism only. The Ethiopian values (the Routing Matrix D2's Sensitive Flag, Urgent Flag and Urgent Season columns, and its Seasonal Calendar) are data in the `openg2p-data` seed (`oan-seed/grievance`), not in this app.

## What a case carries

| Field on Grievance | Values                                   | Set by                              |
| ------------------ | ---------------------------------------- | ----------------------------------- |
| `is_sensitive`     | 0 or 1                                   | the type's `is_sensitive`           |
| `handling_class`   | `Standard`, `Urgent`                     | the matching SLA rule's `is_urgent` |
| `priority`         | `Urgent` or blank                        | Urgent when the case is Urgent      |
| `sla_days`         | working days                             | the matching SLA rule's `sla_days`  |
| `status_pin`       | encrypted 6 digits, sensitive cases only | issued at filing                    |

`is_sensitive`, `handling_class`, `priority` and `status_pin` are written by `services/classification.py`; `sla_days` and the deadline by the SLA clock (`services/sla.py`), which asks the same module for the rule's days. The first three are read only: a save that edits one is refused. They change only when the case moves to another type or category (an approved change request), and then the rules are still read at the **original filing date**, so moving a case never changes the season it was filed in.

## SLA rules

The SLA rules (doctype Grievance SLA Rule, `/api/v1/sla-rules`) are the one source of SLA days and urgency. Each rule is its own record linked to a service category, and **every rule carries both**: there is no blank that inherits from somewhere else. **Priority has one value, Urgent**: an urgent rule gives its cases priority Urgent, any other rule no priority. It is never set on its own (Normal and High were removed; the `drop_normal_and_high_priority` patch cleared them).

- The category's **base rule** names no type, no area and no window. It is the category's SLA wherever no narrower rule matches. Every category with an SLA has one: creating a category (or an SLA configuration) with `sla_days` makes it, a narrower rule cannot be added before it, and it cannot be deleted or narrowed. Its `sla_days` is what `/sla-configurations`, `/category-assignments` and `/service-categories` show and set as the category's `sla_days`.
- A **narrower rule** names a type, an area or a recurring window, and replaces the base rule's values where it matches.

`Grievance SLA Configuration` keeps only what is category-wide and does not vary by season or area: the holiday list, `auto_escalate` and its threshold, `notify_on_breach`, `first_response_hours`, `update_cadence_hours`, `remand_execution_hours`, the state timers and the level turnarounds. It has no `sla_days` any more.

There is no separate season record and no calendar of its own: a window is a recurring month and day, the same every year, so any country's season can be written in it.

| Field                                              | Meaning                                                                            |
| -------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `service_category`                                 | required: the category whose cases the rule is for. Fixed once the rule is created |
| `rule_label`                                       | a name for administrators, for example "Meher input season"                        |
| `is_base`                                          | set by the system: the rule names no type, area or window                          |
| `grievance_type`                                   | optional: only this type of the category                                           |
| `administrative_area`                              | optional: only cases in this area or an area inside it                             |
| `start_month`, `start_day`, `end_month`, `end_day` | optional: a recurring window; all four or none (none is all year)                  |
| `sla_days`                                         | required: the turnaround in working days when the rule matches                     |
| `is_urgent`                                        | the case is handled as Urgent                                                      |
| `priority`                                         | read only: `Urgent` for an urgent rule, blank otherwise (set from `is_urgent`)     |

### Which rule applies

```
a rule matches   = its type (if any) is the case's type
                   and its area (if any) contains the case's area
                   and the filing date is inside its window (if any)
the rule applied = the most specific matching rule:
                   a type's rule before a category-wide one,
                   then the narrowest area (no area is the widest),
                   then a windowed rule before an all-year one
                   (the base rule matches every case of the category)
no rule at all   = the category has no SLA: Standard, no priority, no deadline
```

- A window may wrap the year end (October to April). Only month and day are compared, so the year never matters and 29 February needs no special case. Both ends are inside the window.
- A woreda that keeps its own season instead of the category's has its own rows: a windowed rule for its season, and an all-year rule for the rest of its year.
- Two windowed rules, or two all-year rules, for the same type and area may not share a day: the rule's validation refuses the second (on create and on update), so a case never ties between two rules. A windowed rule and an all-year rule for the same type and area are fine.

### SLA days

When a case's SLA clock starts (`sla.start_clock`), restarts (`reset_clock`) or is recalculated for a new category (`recalculate_sla_on_category_change`), the SLA days are the matching rule's (`sla.resolve_policy(category, grievance)`). A move to another type of the same category re-lengthens the window only when the new type's rule gives other days (`sla.follow_type_change`); the clock keeps its start. Escalation thresholds and reminders work on that window as before.

The clock is not restarted when the officer changes: a handoff, a reassignment within the category and an escalation all keep the case's start and deadline. Only a move to another category (develop's R13) and a reopen start a new window. Time per owner is what `first_response_hours` and each role level's `escalation_hours` are for.

### Escalation under a shorter window

A rule changes the case's window, and the first escalation follows it: the case is first handed up at the category's `auto_escalation_threshold` percent of **its own** window (`sla.arm_escalation`), so 3 in-season days escalate after 80% of 3 days, not of 15. The 50% and 80% reminders are percentages of the same window. After the first escalation each rung keeps the case for its role level's `escalation_hours` (Grievance Role Level), the same in and out of season.

The `level_turnarounds` table of the SLA configuration (turnaround days per role level, STG-512) is stored and validated but not read by the SLA engine yet, so no rule adjusts it. Whether an in-season rule should also shorten the per-level turnarounds, or a rung's escalation hours, is an open point (below).

## Configuration

Everything is data. Nothing needs a code change.

| What                                  | Where                                                                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Sensitive sub-type                    | Grievance Type `is_sensitive`, or `PATCH /api/v1/grievance-types/{id}`. Administrators only, on every write path         |
| A category's SLA days                 | its base rule: `PATCH /api/v1/sla-rules/{id}`, or `sla_days` on `/sla-configurations`, `/category-assignments` or create |
| SLA days, urgency, priority by season | narrower Grievance SLA Rules, `/api/v1/sla-rules` (below)                                                                |
| A woreda's own season                 | rules with that woreda's `administrative_area`                                                                           |

Only an administrator changes a type's `is_sensitive`. The rule is in the type's own validation (`GrievanceType.validate_handling_set_by_an_administrator`), so it holds for the Desk and `/api/resource` as well as the taxonomy API: a Desk Head may write the other fields of the types they head, never this one. Rules are created, changed and deleted by Grievance Admin and System Manager (DocPerm on Grievance SLA Rule); a Review Officer reads them. Grievance Type and Grievance SLA Rule track changes, so every change is in their version history, and a deleted rule is kept in Deleted Documents.

A rule change applies to cases filed afterwards. A case keeps the class and SLA days it was filed with.

### Upgrading a site

The `move_sla_days_to_base_rules` patch (last in `patches.txt`) gives every category with an active SLA configuration its base rule, with the `sla_days` the configuration had, Standard and no priority, so every deadline stays what it was. A category that already has a base rule keeps it. The rule is named `<category code>-BASE`, the name the seed gives it, so loading the seed afterwards adds no second copy. Frappe keeps the old column; nothing reads it after the patch.

The app does not set the Sensitive flags or the seasonal rules of the matrix on an existing site: they are in the `openg2p-data` seed. A site that already holds the types gets them through the API (or by loading the seed's `Grievance_SLA_Rule.json`).

### API

All under the Administration tag. Reads: Grievance Admin, System Manager, Review Officer. Writes: DocPerm on Grievance SLA Rule (Grievance Admin, System Manager).

| Method and path                      | What it does                                                                                                                                                                                             |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/sla-rules`              | list, paginated; filters `service_category` (id, name or code), `grievance_type`, `administrative_area`, `is_urgent`, `is_base`                                                                          |
| `POST /api/v1/sla-rules`             | create one rule; `service_category` and `sla_days` are required, `is_urgent` defaults to false (`true` = Urgent, priority Urgent); `priority` is not sent. A category's first rule must be its base rule |
| `GET /api/v1/sla-rules/{rule_id}`    | one rule                                                                                                                                                                                                 |
| `PATCH /api/v1/sla-rules/{rule_id}`  | change any field but `service_category`; omitted fields stay, `null` clears a scope field (all four window fields `null` is all year). The base rule stays a base                                        |
| `DELETE /api/v1/sla-rules/{rule_id}` | delete one rule; returns it as it was. The base rule cannot be deleted                                                                                                                                   |

Each returns the rule as `{name, service_category, is_base, rule_label, grievance_type, administrative_area, start_month, start_day, end_month, end_day, sla_days, is_urgent, priority, modified}`, `priority` being `"Urgent"` or `null` (`rule`, or `rules` with `pagination` for the list). A window that is not a window, a type of another category, an unknown area or category, a rule that would tie with another, a narrower rule without a base rule, and deleting or narrowing the base rule are `VALIDATION_ERROR`; an unknown id is `NOT_FOUND`.

`/sla-configurations`, `/category-assignments` and `/service-categories` keep their `sla_days` field: it reads and writes the category's base rule.

### What the seed gives

The values of the Routing Matrix D2 are in the `openg2p-data` seed, not here:

- **Sensitive**: exactly C13-01 to C13-04, C15-01 to C15-03 and C16-01. C16-02 (wrong personal data) is not.
- **Base rules**: one per category, with the matrix's turnaround. C17 (pest and disease outbreak) is urgent all year, so its base rule is urgent.
- **Seasonal rules**: C01 Meher (15 May to 31 August, urgent), C02 and C07 dry / irrigation season (1 October to 30 April, urgent), C09 harvest marketing peak (1 October to 31 December, not urgent; it lost its High priority when priority became Urgent only). The matrix gives no in-season turnaround, so each carries its category's days until the owners set them.
- **C04**: no sub-type is flagged. See open points.

## Sensitive cases

Enforced on the server, never only in the portal.

| Rule                                     | Where it is enforced                                                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Anonymous filing                         | unchanged: `is_anonymous` works on every type, and a sensitive case still keeps the submitter's profile link for the submitter's own access                                                                                                                                                                                                                                                                              |
| Bypass the Kebele                        | routing never picks an officer whose seat on the desk is at Kebele level or below (`routing.pick_officer_by_strategy`); escalation never lands on one (`sla.escalate`); a reassignment to a sensitive type neither keeps nor picks one (`reassignment.resolve` reads the type the case moves to); an assignee that is one is refused, also when the case has just become sensitive (`Grievance.guard_assignee_has_desk`) |
| Kebele officers never see them           | `permissions.grievance_query_conditions` and `has_grievance_permission`, so the list, the case, the timeline, attachments and export all refuse. Dashboard figures are counts, not cases                                                                                                                                                                                                                                 |
| Not through whoever filed it for someone | a development agent who filed a sensitive case for a farmer does not keep access afterwards: the case is reached through the submitter's own profile, or the account that filed it for itself. The submit response gives someone filing on the submitter's behalf no PIN and only the submitter's view of the case                                                                                                       |
| Route to the focal person                | existing desks: the lowest rung seated at Woreda level or above takes the case. The Ethics, Female/Safety and Data Protection focal persons are the officers of the C13, C15 and C16 desks                                                                                                                                                                                                                               |
| Officer identity                         | the API shows a submitter no officer, no desk (neither `assigned_dept` nor `department`) and no classification; notifications to the submitter say none either                                                                                                                                                                                                                                                           |
| SMS                                      | a sensitive case sends its submitter only `Grievance <reference>. PIN: <pin>`, on every channel, whatever the template says (`notifications.sensitive_submitter_text`). Officer notices skip Kebele-level officers                                                                                                                                                                                                       |

A **Kebele-level officer** is a user whose every active desk seat has, on the officer's row, an area at Kebele level or below (Kebele, Village, Ward). A seat with no area, or at Woreda level and above, makes the user Woreda-level. Someone with no seat is not a Kebele officer.

### PIN

A sensitive case gets a random six digit PIN at filing, stored encrypted on the **Grievance** itself, not on the Grievance Submitter Profile: the profile is a lasting identity that desk officers can read, and the PIN is a claim check for one case, so keeping it on the case keeps one complaint's PIN from opening another. A submitter who files for themselves gets it in the submit response (`status_pin`), so a submitter without a phone has it. Someone filing on the submitter's behalf (a development agent, an administrator) does not: the submitter gets it by message.

The PIN is never stored in clear by this app. The Grievance Notification Log, which officers can read, holds the message with the PIN masked (`Grievance <reference>. PIN: ******`), and so does the Communication an email leaves on the case. The PIN is put back only into the text that leaves the system (`notifications._outbound_message`): the SMS, the email and the submitter's own in-app alert. The gateway's own records (SMS Log, Email Queue) are System Manager only. The PIN is recoverable server-side by design, since every message carries it. A PIN status check endpoint is not part of this ticket (CR-16).

## Officer view

`GET /api/v1/grievances` rows and the case (`timeline`, `current_state`) carry `is_sensitive`, `handling_class` and `priority` for staff only. A submitter is sent none of them.

## Open points

- **In-season SLA days.** The matrix gives none, so the seeded seasonal rules carry their category's days. The owners should set them per rule (for example 3 days in season), in the one list of `/sla-rules`.
- **Per-level turnarounds in season.** A rule shortens the case's window, and the first escalation with it (a percentage of the window). It does not change `level_turnarounds` (not read by the engine yet) or a rung's `escalation_hours`. Whether an in-season rule should scale those or set its own is for the owners to decide.
- **C04 coercion.** "C04 if coercion" is free text in the Confidential path, not a flag. A per-case Sensitive flag set by an officer, DA or administrator is not built (change plan, section 3 item 14). Until decided, a coercion case is filed as C13-03.
- **The subject of a grievance.** No field names the person complained about (change plan, section 3 item 15), so the service cannot exclude them by identity. The Kebele bypass covers the case where they are a DA or Kebele officer; a subject who is a Woreda officer on the same desk is not excluded.
- **Belg woredas and woreda overrides.** No official list exists, so none is seeded. C01's February to April window for Belg woredas, and a woreda's own C02 or C07 window, are rules with that woreda's area, added when the list exists.
- **C15-03 (pesticide safety).** Stays on the Gender and Safety desk, as the matrix has it. Moving it to Crop Protection is only proposed. The desk of a sub-type is the desks' business (`Grievance RBAC Assignment`), so the move needs no code, and it stays sensitive wherever it goes.
- **Priority for Urgent handling.** The matrix does not say. The change report (CR-03) says the case priority is Urgent inside the window, so an urgent rule gives priority Urgent. Priority has no other value since Normal and High were removed.

## Existing records

The new columns default to Standard, no priority and not sensitive, so cases filed before this change stay as they are and nobody loses access. A type that was already sensitive in practice (a desk without Kebele officers) still routes as before, but is only protected by the new rules once its type is flagged and a case is filed under it. A case already filed keeps its deadline: rules apply when a clock starts or is recalculated, and the migration patch gives each category a base rule with the SLA days it had.
