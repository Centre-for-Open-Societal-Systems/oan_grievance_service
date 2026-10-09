# Notification Configuration API (STG-427)

Read-only API behind the Administration Notification tab: the notification events of FSD
Appendix C (EC-001 to EC-019). Editing an event is a separate endpoint and is not part of this
one.

| Endpoint                                   | Purpose                 |
| ------------------------------------------ | ----------------------- |
| `GET /api/v1/notification-configs`         | All 19 events, in order |
| `GET /api/v1/notification-configs/<EC-id>` | One event (`EC-001` …)  |

Roles: Grievance Admin, System Manager, Administrator, Grievance Review Officer. An id that is
not in Appendix C returns 404.

## Response

Each event matches `NotificationConfig` in the frontend's
`notification-config/components/types.ts`. The list is under `data.notification_configs`, a
single event under `data.notification_config`.

```json
{
	"id": "EC-001",
	"title": "Submission Received",
	"eventType": "submission_received",
	"active": true,
	"channel": ["SMS", "Email"],
	"subject": "Submission Received",
	"trigger": "Immediately on save",
	"recipients": ["Submitter"],
	"template": "Your grievance {{id}} has been received under {{category}}. ...",
	"lastEdited": "2026-09-16T09:44:45Z"
}
```

`lastEdited` is UTC. `eventType` is the machine event key the sender uses.

## Where the data comes from

The matrix is not a table of its own. Each event is one core `Notification` record per channel
(seeded by `setup/install.py`, keyed by `method` = event code), and those records are what
`services/notifications.py` reads when it sends. The API reads them, so what it reports is what
goes out, and an edit made on the desk shows up here.

`services/notification_config.py` folds the records into the frontend shape:

- **`active`** is true when at least one channel record is enabled. **`channel`** lists the
  enabled channels; when all are off it lists every configured channel so the card still has
  them to show.
- **`recipients`** come from the records' Grievance Recipient, shown under the Administration
  names (Assigned Officer and Department Officer as "L1 Officer", Department Head as "Dept
  Head", Top Level Authority as "L2 Officer").
- **`subject`** and **`template`** are stored as translatable Jinja
  (`{{ _('Grievance {0} ...', context=...).format(doc.ticket_number) }}`). They are returned as
  the English source with `{{id}}`-style tokens: `ticket_number` is `{{id}}`,
  `service_category` is `{{category}}`, `assigned_dept` is `{{dept}}`, `sla_due_date` is
  `{{slaDeadline}}`, any other field is its camel-cased name. A conditional keeps the branch for
  a set field. Wording that is not in the seeded shape is returned as stored.
- **`title`** and **`trigger`** are not stored on the records; they come from the `EVENTS`
  catalogue in the service, which is also what ties each EC id to its event code.

## Things to know

- **EC-017 (SLA breached, L2)** has no event of its own. The escalation ladder sends a single
  `sla_breached` event whatever rung the case lands on, and that is EC-016's. EC-017 therefore
  reports `active: false`, `eventType: null`, an empty `subject` and `template`, and a null
  `lastEdited`.
- **EC-016 and EC-018** list the one recipient the seeded record carries. The FSD names Dept
  Head and Nodal Officer for both.
- **"Grievance closed"** is in Appendix C but not among the 19 events the frontend and the
  ticket use, so it is not returned. The backend also sends `status_rejected` and
  `anonymity_disclosure_requested`, which are not in Appendix C either.
- The seeded wording is shorter than the frontend's mock text, which is what the API returns.
- The list is a fixed set, so it is not paginated and the frontend's core and escalation arrays
  come back as one list.
