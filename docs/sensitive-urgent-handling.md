# Sensitive and urgent handling (STG-514)

A grievance sub-type can be **Sensitive**. A case can be **Urgent**, carry a **priority** and get its own **SLA days** from a rule of its category's SLA configuration, for example only during a season. The service decides this once, when the case is filed, and the case carries the answer. Nothing in the portal works out a season.

Source: the Sensitive Flag, Urgent Flag and Urgent Season columns of the Routing Matrix D2 (sheet "Grievance Routing") and its Seasonal Calendar (sheet "Configuration related").

## What a case carries

| Field on Grievance | Values                                   | Set by                                                         |
| ------------------ | ---------------------------------------- | -------------------------------------------------------------- |
| `is_sensitive`     | 0 or 1                                   | the type's `is_sensitive`                                      |
| `handling_class`   | `Standard`, `Urgent`                     | the matching SLA rule's `is_urgent`                            |
| `priority`         | `Normal`, `High`, `Urgent`               | the matching SLA rule's `priority`, or Urgent for urgent rules |
| `sla_days`         | working days                             | the matching SLA rule's `sla_days`, else the category's        |
| `status_pin`       | encrypted 6 digits, sensitive cases only | issued at filing                                               |

`is_sensitive`, `handling_class`, `priority` and `status_pin` are written by `services/classification.py`; `sla_days` and the deadline by the SLA clock (`services/sla.py`), which asks the same module for the rule's days. The first three are read only: a save that edits one is refused. They change only when the case moves to another type or category (an approved change request), and then the rules are still read at the **original filing date**, so moving a case never changes the season it was filed in.

## SLA rules

The rules are a table (`sla_rules`, doctype Grievance SLA Rule) on the category's Grievance SLA Configuration. There is no separate season record and no calendar of its own: a window is a recurring month and day, the same every year, so any country's season can be written in it.

| Field                                              | Meaning                                                                                    |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `rule_label`                                       | a name for administrators, for example "Meher input season"                                |
| `grievance_type`                                   | optional: only this type of the category                                                   |
| `administrative_area`                              | optional: only cases in this area or an area inside it                                     |
| `start_month`, `start_day`, `end_month`, `end_day` | optional: a recurring window; all four or none (none is all year)                          |
| `sla_days`                                         | optional: the turnaround in working days when the rule matches; blank keeps the category's |
| `is_urgent`                                        | the case is handled as Urgent                                                              |
| `priority`                                         | optional: `Normal`, `High` or `Urgent`; blank is Urgent for an urgent rule, else Normal    |

### Which rule applies

```
a rule matches   = its type (if any) is the case's type
                   and its area (if any) contains the case's area
                   and the filing date is inside its window (if any)
the rule applied = the most specific matching rule:
                   a type's rule before a category-wide one,
                   then the narrowest area (no area is the widest),
                   then a windowed rule before an all-year one
no rule matches  = Standard, Normal, the category's sla_days
```

- A window may wrap the year end (October to April). Only month and day are compared, so the year never matters and 29 February needs no special case. Both ends are inside the window.
- A rule that sets nothing still wins where it matches: it says "standard here". A woreda gets its own season instead of the category's with two rules for its area: the windowed urgent one, and an all-year one that sets nothing.
- Two windowed rules, or two all-year rules, for the same type and area may not share a day: the configuration refuses them, so a case never ties between two rules. A windowed rule and an all-year rule for the same type and area are fine.
- **C09 (harvest marketing)** is not Urgent: its rule sets priority High from October to December.
- **C17** has an all-year urgent rule.

### SLA days

When a case's SLA clock starts (`sla.start_clock`), restarts (`reset_clock`) or is recalculated for a new category (`recalculate_sla_on_category_change`), the SLA days are the matching rule's `sla_days` when it sets one, otherwise the category's. A move to another type of the same category re-lengthens the window only when the new type's rule gives other days (`sla.follow_type_change`); the clock keeps its start. Escalation thresholds and reminders work on that window as before.

## Configuration

Everything is data. Nothing needs a code change.

| What                                 | Where                                                                                                                                                                                   |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sensitive sub-type                   | Grievance Type `is_sensitive`, or `PATCH /api/v1/grievance-types/{id}`. Administrators only, on every write path                                                                        |
| Urgency, priority, seasonal SLA days | the category's Grievance SLA Configuration, Rules table, or `PATCH /api/v1/sla-configurations/{config}` with `rules` (replaces every rule). `GET /api/v1/sla-configurations` lists them |
| A woreda's own season                | rules with that woreda's `administrative_area`                                                                                                                                          |

Only an administrator changes a type's `is_sensitive`. The rule is in the type's own validation (`GrievanceType.validate_handling_set_by_an_administrator`), so it holds for the Desk and `/api/resource` as well as the taxonomy API: a Desk Head may write the other fields of the types they head, never this one. The SLA configuration is written by those with write permission on it (Grievance Admin, System Manager). Grievance Type and Grievance SLA Configuration track changes, so every change is in their version history.

A rule change applies to cases filed afterwards. A case keeps the class and SLA days it was filed with.

The matrix values are in `setup/urgency_matrix.py`. A new site gets them from the seed (`openg2p-data`, `oan-seed/grievance`); a site that already holds the types and SLA configurations gets them from the `apply_sensitive_urgent_matrix` patch, which only fills what is empty: a configuration that already has rules is left alone. The patch saves each record through its validation; a record that validation refuses is left as it is and logged. The matrix test reads the workbook itself when it is on the machine.

### What the matrix gives

- **Sensitive**: exactly C13-01 to C13-04, C15-01 to C15-03 and C16-01. C16-02 (wrong personal data) is not.
- **Urgent**: every sub-type of C01, C02, C07 and C17, so each is one category-wide rule.

| Category | Rule                                 | Window                  | Urgent | Priority |
| -------- | ------------------------------------ | ----------------------- | ------ | -------- |
| C01      | Meher input season                   | 15 May – 31 August      | yes    | Urgent   |
| C02      | Irrigation season (dry season)       | 1 October – 30 April    | yes    | Urgent   |
| C07      | Dry / irrigation season              | 1 October – 30 April    | yes    | Urgent   |
| C09      | Harvest marketing peak               | 1 October – 31 December | no     | High     |
| C17      | Pest and disease outbreak (all year) | all year                | yes    | Urgent   |

- **SLA days**: the matrix gives no in-season turnaround, so no seeded rule sets `sla_days`. The category's SLA days apply until the owners set one on the rule.
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

- **In-season SLA days.** The matrix gives none, so the seeded rules set none. The owners should set them per rule (for example 3 days in season).
- **C04 coercion.** "C04 if coercion" is free text in the Confidential path, not a flag. A per-case Sensitive flag set by an officer, DA or administrator is not built (change plan, section 3 item 14). Until decided, a coercion case is filed as C13-03.
- **The subject of a grievance.** No field names the person complained about (change plan, section 3 item 15), so the service cannot exclude them by identity. The Kebele bypass covers the case where they are a DA or Kebele officer; a subject who is a Woreda officer on the same desk is not excluded.
- **Belg woredas and woreda overrides.** No official list exists, so none is seeded. C01's February to April window for Belg woredas, and a woreda's own C02 or C07 window, are rules with that woreda's area, added when the list exists.
- **C15-03 (pesticide safety).** Stays on the Gender and Safety desk, as the matrix has it. Moving it to Crop Protection is only proposed. The desk of a sub-type is the desks' business (`Grievance RBAC Assignment`), so the move needs no code, and it stays sensitive wherever it goes.
- **Priority for Urgent handling.** The matrix does not say. The change report (CR-03) says the case priority is Urgent inside the window, so an urgent rule with no priority gives Urgent.

## Existing records

The new columns default to Standard, Normal and not sensitive, so cases filed before this change stay as they are and nobody loses access. A type that was already sensitive in practice (a desk without Kebele officers) still routes as before, but is only protected by the new rules once its type is flagged and a case is filed under it. A case already filed keeps its deadline: rules apply when a clock starts or is recalculated.
