# Sensitive and urgent handling (STG-514)

A grievance sub-type can be **Sensitive**, **Urgent**, or follow a **season**. The service decides this once, when the case is filed, and the case carries the answer. Nothing in the portal works out a season.

Source: the Sensitive Flag, Urgent Flag and Urgent Season columns of the Routing Matrix D2 (sheet "Grievance Routing") and its Seasonal Calendar (sheet "Configuration related").

## What a case carries

| Field on Grievance | Values                                   | Set by                                                    |
| ------------------ | ---------------------------------------- | --------------------------------------------------------- |
| `is_sensitive`     | 0 or 1                                   | the type's `is_sensitive`                                 |
| `handling_class`   | `Standard`, `Urgent`                     | the type, its season, the filing date and the case's area |
| `priority`         | `Normal`, `High`, `Urgent`               | the season, inside its window                             |
| `status_pin`       | encrypted 6 digits, sensitive cases only | issued at filing                                          |

All four are written by `services/classification.py`. They are read only: a save that edits one is refused. They change only when an officer moves the case to another type (an approved change request), and then the season is still read at the **original filing date**, so moving a case never changes the season it was filed in.

## Classification

```
sensitive = type.is_sensitive
inside    = the season is active and the filing date is inside the window that applies to the case's area
handling  = Urgent  if type.is_urgent and inside and season.handling_class == Urgent, else Standard
priority  = season.priority if inside, else Normal
```

- **C09 (harvest marketing)** is not Urgent. Its season has handling class Standard and priority High, so October to December raises priority and never makes a case Urgent.
- **C17** follows an all-year season, so it is Urgent every day.
- A type with no season is Standard and Normal.
- Outside the window an Urgent type is Standard and Normal.

### Which window applies

A season holds a **Default** window, at most one **Belg Woreda** window, and any number of **Specific Area** overrides. Narrowest first:

1. a Specific Area override whose area contains the case's area (a woreda override also covers its kebeles; the narrowest wins);
2. the Belg window, when the case's area is inside an area marked **Belg**;
3. the Default window.

A window is a start and end month and day. It may wrap the year end (October to April). Only month and day are compared, so the year never matters and 29 February needs no special case. Both ends are inside the window.

### Both calendars

Each window stores a Gregorian window (which decides) and an Ethiopian one (recorded, validated and shown). The two are not the same range for every season: 'Oct - Apr' starts about ten days before Tikimt 1. The Gregorian window decides, because that is the column of the Routing Matrix that carries a start and end for every season.

## Configuration

Everything is data. Nothing needs a code change.

| What                                     | Where                                                                                                                                         |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Sensitive, Urgent, season of a sub-type  | Grievance Type (`is_sensitive`, `is_urgent`, `urgency_season`), or `PATCH /api/v1/grievance-types/{id}`. Administrators only                  |
| Season windows, handling class, priority | Grievance Urgency Season, or `PATCH /api/v1/urgency-seasons/{season}` (`windows` replaces them all). `GET /api/v1/urgency-seasons` lists them |
| Woreda override                          | a Specific Area row on the season                                                                                                             |
| Belg woredas                             | `is_belg` on the area, or `PATCH /api/v1/administrative-areas/{area}/belg`. Mark the woreda: the kebeles inside follow it                     |

An Urgent type needs an active season whose handling class is Urgent. A season used by an active Urgent type cannot be deactivated.

The matrix values are in `setup/urgency_matrix.py`. A new site gets them from the seed (`openg2p-data`, `oan-seed/grievance`); a site that already holds the types gets them from the `apply_sensitive_urgent_matrix` patch, which only fills what is empty. The matrix test reads the workbook itself when it is on the machine.

### What the matrix flags

- **Sensitive**: exactly C13-01 to C13-04, C15-01 to C15-03 and C16-01. C16-02 (wrong personal data) is not.
- **Urgent**: C01-01..08, C02-01..05, C07-01..03, C17-01..04.
- **C04**: no sub-type is flagged. See open points.

## Sensitive cases

Enforced on the server, never only in the portal.

| Rule                                     | Where it is enforced                                                                                                                                                                                                                                                        |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Anonymous filing                         | unchanged: `is_anonymous` works on every type, and a sensitive case still keeps the submitter's profile link for the submitter's own access                                                                                                                                 |
| Bypass the Kebele                        | routing never picks an officer whose seat on the desk is at Kebele level or below (`routing.pick_officer_by_strategy`, also used by reassignment); escalation never lands on one (`sla.escalate`); an assignee that is one is refused (`Grievance.guard_assignee_has_desk`) |
| Kebele officers never see them           | `permissions.grievance_query_conditions` and `has_grievance_permission`, so the list, the case, the timeline, attachments and export all refuse. Dashboard figures are counts, not cases                                                                                    |
| Not through whoever filed it for someone | a development agent who filed a sensitive case for a farmer does not keep access afterwards: the case is reached through the submitter's own profile, or the account that filed it for itself                                                                               |
| Route to the focal person                | existing desks: the lowest rung seated at Woreda level or above takes the case. The Ethics, Female/Safety and Data Protection focal persons are the officers of the C13, C15 and C16 desks                                                                                  |
| Officer identity                         | the API shows no officer, desk or classification to a submitter; notifications to the submitter say none either                                                                                                                                                             |
| SMS                                      | a sensitive case sends its submitter only `Grievance <reference>. PIN: <pin>`, on every channel, whatever the template says (`notifications.sensitive_submitter_text`). Officer notices skip Kebele-level officers                                                          |

A **Kebele-level officer** is a user whose every active desk seat has, on the officer's row, an area at Kebele level or below (Kebele, Village, Ward). A seat with no area, or at Woreda level and above, makes the user Woreda-level. Someone with no seat is not a Kebele officer.

### PIN

A sensitive case gets a random six digit PIN at filing, stored encrypted and returned to the submitter in the submit response (`status_pin`), so a submitter without a phone has it. The PIN is also sent in each SMS, so it is recoverable server-side by design. A PIN status check endpoint is not part of this ticket (CR-16).

## Officer view

`GET /api/v1/grievances` rows and the case (`timeline`, `current_state`) carry `is_sensitive`, `handling_class` and `priority` for staff only. A submitter is sent none of them.

## Open points

- **Calendar boundaries.** Gregorian decides. The Ethiopian window is stored but does not drive classification. Matrix owners should confirm, because C02, C07 and C09 differ by 8 to 10 days between the two.
- **C04 coercion.** "C04 if coercion" is free text in the Confidential path, not a flag. A per-case Sensitive flag set by an officer, DA or administrator is not built (change plan, section 3 item 14). Until decided, a coercion case is filed as C13-03.
- **The subject of a grievance.** No field names the person complained about (change plan, section 3 item 15), so the service cannot exclude them by identity. The Kebele bypass covers the case where they are a DA or Kebele officer; a subject who is a Woreda officer on the same desk is not excluded.
- **Belg woredas.** No official list exists, so none is seeded. Maintain them with `is_belg`. This is a dependency on the source list.
- **C15-03 (pesticide safety).** Stays on the Gender and Safety desk, as the matrix has it. Moving it to Crop Protection is only proposed. The desk of a sub-type is the desks' business (`Grievance RBAC Assignment`), so the move needs no code, and it stays sensitive wherever it goes.
- **Priority for Urgent handling.** The matrix does not say. The change report (CR-03) says the case priority is Urgent inside the window, so the seasons use Urgent. It is a field on the season.
- **What Urgent changes operationally.** The classification is recorded and shown. It does not shorten SLA days or change escalation timers: those stay on the category's SLA configuration.

## Existing records

The new columns default to Standard, Normal and not sensitive, so cases filed before this change stay as they are and nobody loses access. A type that was already sensitive in practice (a desk without Kebele officers) still routes as before, but is only protected by the new rules once its type is flagged and a case is filed under it.
