# Ticket Export (SOW Annexure form)

Audience: backend and frontend developers adding the Export button to the case view (STG-469).

One grievance, with its timeline, downloaded as the paper complaint form from the SOW Annexure ("Sample Manual Complaint Process Form"), as a PDF or a CSV. The source document is `EAON_PLAN_SOW_Services_Grievance_D2`.

## 1. Endpoint

`GET /api/v1/grievances/{ticket_number}/export?format=pdf|csv&lang=en|am`

| Part     | Value                                                                                                                                                                               |
| :------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Format   | `pdf` (default) or `csv`, any case. Anything else is a `400 VALIDATION_ERROR`                                                                                                       |
| Language | `en` (default) or `am`, any case: the language of the form's own wording (§6). Anything else is a `400 VALIDATION_ERROR`. An Amharic file is named `grievance-<ticket>-am.<format>` |
| Ticket   | As typed: `3-001-002A-0`, `3001002A0` or the lower-case form                                                                                                                        |
| Success  | The file itself, `Content-Disposition: attachment; filename=grievance-<ticket>.<format>`, `Cache-Control: no-store`                                                                 |
| Failure  | The usual JSON error envelope: `403 PERMISSION_DENIED`, `404 NOT_FOUND`, `400 VALIDATION_ERROR`, `500 INTERNAL_ERROR`                                                               |
| Gateway  | `citizen-intake` tier (60 a minute), which also bounds the cost of rendering a PDF                                                                                                  |

The CSV is UTF-8 with a byte-order mark, so a spreadsheet reads Amharic. It has one fact per line, in four columns: `Section`, `Entry`, `Field`, `Value`. A key/value row has no `Entry`; a table row is named by its table and position (`Decisions 2`). A cell that a spreadsheet would run as a formula (`=`, `+`, `-`, `@` followed by anything but a number) is prefixed with `'`.

## 2. Who may export, and what the file holds

Two checks, in this order, and both refuse with `403`:

1. **Role.** The caller holds the Grievance `export` permission. That is Grievance Admin, System Manager, Grievance Officer, Grievance Submitter and Grievance Assisted Submitter. The Review Officer has none: it is read-only oversight and is left out. The answer is the same whether or not the ticket exists.
2. **Case.** The caller may read this case (`has_grievance_permission`): their own case or one they assisted, a case assigned to them or their reporting chain, or a case inside their desk scope.

The file holds what the caller could already read on the case, no more:

| Content                                            | Staff                            | Submitter                      |
| :------------------------------------------------- | :------------------------------- | :----------------------------- |
| Internal notes, internal timeline entries          | Yes                              | No                             |
| Officer names (assignee, authors, decided by)      | Yes                              | No: "Officer" or "Complainant" |
| Status-history `notes`, notification channels used | Yes                              | No                             |
| Anonymous case: name, phone, email                 | Sealed unless `can_see_identity` | Own case only                  |
| Attachment bytes, file URLs                        | Never                            | Never                          |

Attachments are listed with name, type, size, upload time and scan status, including files that are infected or not yet scanned; nothing is embedded.

## 3. Audit

Every attempt writes one `Grievance Access Audit Event`, committed before the response, because the API error handler rolls the request back and a refusal would otherwise leave nothing behind.

| Outcome                                 | `action`        | `decision` | `scope_evaluated`                                                     |
| :-------------------------------------- | :-------------- | :--------- | :-------------------------------------------------------------------- |
| File produced                           | `export`        | `Allowed`  | `format=pdf; lang=en; outcome=success`                                |
| Caller lacks the export role            | `export`        | `Denied`   | `format=pdf; lang=en; reason=role`                                    |
| Caller cannot read the case             | `export`        | `Denied`   | `format=pdf; lang=en; reason=case_access`                             |
| No such ticket                          | `export`        | `Denied`   | `format=pdf; lang=en; ticket=<first 40 characters>; reason=not_found` |
| Cleared, but the file could not be made | `export_failed` | `Allowed`  | `format=pdf; lang=en; reason=generation_error (RuntimeError)`         |

The row links the grievance when there is one, and carries the user, their role and the request path. It never carries anything from the complaint. A request with an invalid `format` is rejected before any of this runs and is not an export attempt.

`export` rows mean a file left the system. To list who took one: `action = export` and `decision = Allowed`.

## 4. How the form is filled

The PDF is laid out like the Annexure: the letterhead (the federation, then the region and the filing office, named from the case's administrative area), the title, a shaded four-column reference block, and twelve numbered sections of `Label: value` lines. An empty field is a ruled blank to write on, as on paper, and the form's own wording ("Following review and verification, the responsible office determined that:") is printed as it is. The history tables sit under the section they belong to, and the whole timeline follows on its own page. The data model has no record of its own for an investigation, a decision or an appeal, so those are read from what the workflow leaves behind. A field the system never captures is left blank, as it is on paper; nothing stands in for it.

| Annexure                            | Taken from                                                                                                                                                                                                                                                                            |
| :---------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Reference No.                       | `ticket_number`, grouped as in the rest of the API                                                                                                                                                                                                                                    |
| Date Submitted                      | `creation`                                                                                                                                                                                                                                                                            |
| Submission Office                   | The administrative area, as `kebele, woreda, zone, region`                                                                                                                                                                                                                            |
| Grievance Category                  | `service_category`                                                                                                                                                                                                                                                                    |
| Priority                            | **Blank.** The system has no priority                                                                                                                                                                                                                                                 |
| Grievance Status                    | `status`                                                                                                                                                                                                                                                                              |
| Receiving Office                    | `assigned_dept`. Document Type is printed as "Grievance Record"                                                                                                                                                                                                                       |
| 1 Complainant                       | `submitter_name`, submitter type, area, kebele, administrative unit, contact (sealed for an anonymous case). **Occupation: blank**                                                                                                                                                    |
| 2 Subject                           | The grievance type's name                                                                                                                                                                                                                                                             |
| 3 Description                       | `description`                                                                                                                                                                                                                                                                         |
| 4 Key details                       | Service category, service provider, submission channel, anonymous                                                                                                                                                                                                                     |
| 5 Supporting documents              | The six-item checklist, ticked from `Grievance Attachment.document_type` (see §5), and the attached files listed under it, oldest first                                                                                                                                               |
| 6 Requested action                  | `desired_outcome`                                                                                                                                                                                                                                                                     |
| 7 Initial action / assignment       | First move into Assigned, `assigned_dept`, `assigned_to` (staff), `sla_due_date` as the target response date; every Assign and Refer Onward, and every approved reassignment (staff)                                                                                                  |
| 8 Investigation                     | First move to In Progress as the investigation date; work started, information requested, complainant replied, and internal notes (staff). Findings: the resolution summary of the latest response, which FSD Appendix D-1 defines as holding the findings                            |
| 9 Decision                          | The latest decision's date, outcome, action taken and determination on the form's lines, and every Resolve, Partially Resolve and Reject in the table below, with the review round (1 plus the reopens before it). The referral box is ticked when the case was ever referred onward  |
| 10 Communication to the complainant | The first officer response as the response date; the SMS box when an SMS went out, and any other channel under Other (staff only); every public response and message, both directions, with the department response's sequence number (FSD D-3)                                       |
| 11 Appeal / further review          | Further Action Requested is ticked when the case was reopened; the latest rating as given; the reason on the last reopen; every Reopen and escalation entry, `reopen_count`, `escalated`, every `Grievance Feedback` row                                                              |
| 12 Closure                          | Final Status boxes: Resolved and Closed from the status; Escalated from the flag; Referred or Reopened when that was the case's last settling move. The date, reason and author of the move into Closed or Rejected ("System" for an automatic close). **Signature and stamp: blank** |
| Appendix                            | The whole timeline, oldest first                                                                                                                                                                                                                                                      |

Every table is ordered by time, oldest first. A stage that has not happened says so in a note ("No decision has been recorded.") and its table is left out.

## 5. Not covered

- **Wording without Amharic yet** stays in English (§6).
- **Dates are Gregorian** in both languages. The Amharic form uses the Ethiopian calendar, which needs a conversion that is not built.
- **The letterhead** is built from the case's region and filing office (its woreda, or zone) and reads "<name> Administration". The sample's "City" and "Sub-City" are part of its own names and are not added.
- **Document checklist.** `Grievance Attachment.document_type` is free text with no list of values behind it, so an item is ticked only by an attachment whose document type names it (ignoring case, spacing and punctuation), and any other attachment ticks Other Supporting Documents. Until the upload form sends those six names, most files will land under Other.
- **Boxes the system cannot answer.** Correct official record, additional site verification, written clarification, no action required, Written Letter, Telephone, In Person, Satisfied and Not Satisfied are left unticked: the system holds the department's own words as the action taken, and a 1 to 5 rating has no documented line between satisfied and not. They are for whoever completes the printed form by hand.
- **Existing denials.** `audit.log_denied` calls made before this change (the attachment endpoints) are not durable and are lost to the same rollback. Pass `durable=True` there to keep them.
- **Proposed closure date.** FSD Appendix D-1 requires one on every department response. The service does not store it, so it cannot appear in the form.
- **Responder name for a submitter.** FSD D-4 lets a submitter see the responder's name. The timeline API shows a submitter only the officer's role, and the export follows the API, so a submitter sees "Officer". Say if the form should name them.
- **Per-response audit fields.** FSD D-3 lists the response's IP address, prior and new status and the notification sent. The status change is in the Decisions table; the IP address is not recorded per response, so it is not exported.
- **Bulk export.** The System Design lists `POST /api/v1/reports/exports` (a queued CSV or PDF of a filtered selection, staff only). That is the reports export and is not part of this ticket.

## 6. Language

`lang=am` prints the form's own wording in Amharic: the letterhead, the title, the reference block, the twelve section titles, the field labels, the checklists and the wording printed on the paper form. What people wrote on the case (the description, the responses, the notes) is never translated.

The wording is in `oan_grievance_service/translations/am.csv`, which Frappe loads like any app translation, and every label already goes through `_()`. Each entry is the Annexure's own Amharic, copied from the document; the one composed entry is `{0} አስተዳደር` ("{0} Administration"), built from the Annexure's word for administration around the region or office name. An admin can override any entry from the desk with a `Translation` record for `am`.

- **The English text is the key.** Rewording an English label in the code drops its Amharic until `am.csv` is changed to match. The same word with two meanings takes a context (`Telephone` for the phone number, `Telephone` in the Communication Method boxes).
- **Wording with no Amharic stays English** instead of failing: for example Submitter Type, Email, the table column headings, and the notes such as "No decision has been recorded." The Annexure has no Amharic for them. Add rows to `am.csv` when a translator supplies them.
- **Area names** are printed as they are stored, so a region and office stored in English appear in English next to the Amharic word for administration.
- **The PDF needs an Ethiopic font on the server.** Install Noto Sans Ethiopic (SIL Open Font License), for example from the `fonts-noto-core` package, or Abyssinica SIL. Without one, every Amharic character in the PDF, translated labels and the complainant's own words alike, prints as an empty box. The font list in the PDF style is Liberation Sans, Arial, Noto Sans Ethiopic, Abyssinica SIL, Nyala. The CSV does not depend on a font.
