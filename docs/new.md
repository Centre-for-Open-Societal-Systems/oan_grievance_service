# Grievance Management System — Diagrams (Fixed)

Fixes applied:

- **Diagram 1**: quoted node labels containing `()` and `&` (these break Mermaid flowchart parsing / rendering unquoted).
- **Diagrams 2.1–2.3.8**: added an explicit theme block for high contrast.

---

## 1. Component Architecture

```mermaid
flowchart TD
    A["Farmer / Field Agent"] -->|"Submit / Track"| B["Mobile App"]
    C["Case Officers & Admins"] -->|"Triage / Resolve"| F["Grievance Web Application"]

    B <-->|"Submit / Track"| API["API Gateway (Kong)"]
    F <-->|"Access by role"| API
    IVR["IVR Service"] -->|"SMS channel"| API
    J["Mail Service"] -->|"Get ticket"| API
    TG["Telegram Bot Service"] <-->|"Submit / Track"| API

    subgraph Grievance_Service ["Grievance Service"]
        H["SLA Automation Engine"]
        I["Notification Gateway"]
    end

    API <--> Grievance_Service

    style API fill:#fff3cd,stroke:#d39e00,stroke-width:2px
    style Grievance_Service fill:#f8faff,stroke:#4dabf7,stroke-dasharray: 5 5,stroke-width:1.5px
```

---

## 2.1 — Service Category, Department & SLA Configuration

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant AD as Admin
    participant UI as Grievance Web Application
    participant SV as Grievance Service

    note over AD,SV: Phase 1 — Create Service Category & Grievance Type
    AD->>UI: Define name & code (e.g. Inputs)
    UI->>SV: create category / grievance type
    note over SV: persisted to<br/>grievance_category
    SV-->>UI: Category saved
    UI-->>AD: Category saved

    note over AD,SV: Phase 2 — Create Department
    AD->>UI: Register name + notification email
    UI->>SV: create department
    note over SV: persisted to<br/>grievance_department
    SV-->>UI: Department saved
    UI-->>AD: Department saved

    note over AD,SV: Phase 3 — Category Assignment (Routing Rule)
    AD->>UI: Link Category → Dept → L1 → L2 → SLA days<br/>+ Auto-Escalate & Notify-on-Submit flags
    UI->>SV: create routing rule
    note over SV: persisted to<br/>grievance_routing_rule
    SV-->>UI: Assignment saved
    UI-->>AD: Assignment saved

    note over AD,SV: Phase 4 — Set SLA Policy
    AD->>UI: Set deferral limit + per-category thresholds
    UI->>SV: save SLA configuration
    note over SV: persisted to<br/>grievance_sla_configuration
    SV-->>UI: Policy saved
    UI-->>AD: Policy saved
```

---

## 2.2 — Officer Onboarding (L1 / L2 / Department Head)

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant AD as Admin
    participant UI as Grievance Web Application
    participant SV as Grievance Service
    participant NO as New Officer

    note over AD,SV: Phase 1 — Admin creates the officer account
    AD->>UI: Create officer (name, email) + role L1/L2/Dept Head<br/>+ region / department / category
    UI->>SV: create user + RBAC assignment
    SV-->>UI: account created · temporary password issued

    note over SV,NO: Phase 2 — Credentials handed over
    SV->>NO: temporary password (Email / SMS)

    note over UI,NO: Phase 3 — First sign-in
    NO->>UI: sign in with temporary password
    UI->>SV: validate · force password change
    NO->>UI: set own password
    SV-->>NO: account active

    note over AD,NO: Result — officer is selectable in routing rules (L1 / L2)
```

---

## 2.3 — Grievance Submission → Resolution

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant FA as Farmer /<br/>Field Agent
    participant CH as Intake Channel<br/>App · IVR · Mail · Bot
    participant SV as Grievance<br/>Service
    participant UI as Grievance<br/>Web Application
    participant OF as L1 Officer
    participant SC as SLA<br/>Scheduler

    note over FA,SV: Phase 1 — Submission, dedup and masking
    FA->>CH: Open Submit a Grievance
    CH->>SV: submit grievance payload<br/>(form, evidence, mask identity?)
    SV-->>CH: ack: ticket number · status = Submitted
    note over SV: Dedup hash match marks case Possible Duplicate.<br/>Masked identity shows officers an anonymous id,<br/>while full identity stays inside Grievance Service.

    note over SV,SC: Phase 2 — Routing, investigation and response
    note over SV: Rule match routes directly to owning dept & L1 officer.
    SV->>UI: notify department & officer
    SV->>SC: start SLA clock
    SV-->>CH: notify: status is In Progress
    note over OF,CH: Optional info request loop — officer asks, submitter replies with evidence
    OF->>SV: confirm or reject duplicate flag
    SC-->>OF: 50% reminder &<br/>80% escalation warning
    OF->>SV: submit structured response

    note over SV,OF: Phase 2B — Reassignment or referral
    OF->>SV: refer case to another dept
    note over SV: Re-routes on same category and department rule.<br/>Approval is off by default (configurable per category — see 2.3.4).<br/>The SLA clock keeps running and is not reset.
    SV->>UI: notify receiving dept & officer

    note over FA,SV: Phase 3 — Confirmation, reopening and closure
    SV-->>CH: full response · pending submitter<br/>(7-day response window)
    note over FA,SV: Confirm closes case · Reopen returns to In Progress<br/>· no reply in 7 days auto-closes it
```

---

## 2.3.1 — Triage outcomes: routing, the manual queue and rejection

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant CH as Submitter channel
    participant SV as Grievance Service
    participant OC as Officer for Other
    participant DEPT as Owning department<br/>and L1 officer

    CH->>SV: case arrives, with or without a category
    alt Category given and a routing rule matches
        SV->>DEPT: assign to the owning department and L1 officer
        SV-->>CH: notify: assigned
    else No category given, or no rule matches
        SV->>SV: file under Other, the default category
        SV->>OC: case appears in Other queue, status stays Submitted
        OC->>SV: read case and determine its real category
        alt It belongs to a service category
            OC->>SV: reassign to that category and department
            SV->>DEPT: receiving department and officer notified
            SV-->>CH: notify: assigned
        else Not a grievance this platform handles
            OC->>SV: reject, reason mandatory
            SV-->>CH: notify: rejected, with reason
        end
    end
```

---

## 2.3.2 — The More Info Needed loop

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant OF as Assigned Officer
    participant SV as Grievance Service
    participant CH as Submitter channel
    participant SC as SLA Scheduler

    OF->>SV: request more information, with the question
    SV->>SV: status becomes More Info Needed
    SV-->>CH: notify: information requested
    Note over SC: the SLA clock keeps running<br/>while the case waits
    alt Submitter replies
        CH->>SV: reply with the answer and any evidence
        SV->>SV: status returns to In Progress
        SV-->>OF: notify: submitter responded
    else No reply
        Note over SV,CH: the case stays More Info Needed<br/>and the SLA ladder continues to fire
    end
```

---

## 2.3.3 — Duplicate review

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant SV as Grievance Service
    participant OF as Assigned Officer
    participant CH as Submitter channel

    SV->>SV: dedup hash matches<br/>an open case
    SV->>OF: flag case Possible Duplicate, alongside original
    OF->>SV: review both cases
    alt Confirmed duplicate
        OF->>SV: confirm — link to original & close duplicate
        SV-->>CH: notify: merged into original ticket, which to follow
    else Not a duplicate
        OF->>SV: reject the flag
        SV->>SV: clear flag & resume<br/>normal lifecycle
    end
```

---

## 2.3.4 — Referral and reassignment

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant L1 as L1 Officer
    participant SV as Grievance Service
    participant L2 as L2 Senior Officer
    participant NEW as Receiving officer<br/>or department

    L1->>SV: reassign or refer the case, reason mandatory
    SV->>SV: read the category's approval setting
    alt Approval not required — the default
        SV->>SV: commit immediately, re-routing on category & dept rule
        SV->>NEW: receiving officer or department notified
    else Approval required — enabled for this category
        SV->>L2: request queued for decision, case stays where it is
        Note over NEW: proposed officer gains no rights<br/>while request is open
        alt L2 approves
            L2->>SV: approve
            SV->>NEW: move committed, receiving officer notified
        else L2 rejects
            L2->>SV: reject, with a reason
            SV-->>L1: request declined, case stays with L1
        end
    end
    Note over SV: SLA clock continues in every branch and is never reset
```

---

## 2.3.5 — The SLA ladder and escalation

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant SC as SLA Scheduler
    participant SV as Grievance Service
    participant OF as Assigned Officer
    participant DH as Department Head<br/>& Nodal Officer
    participant CH as Submitter channel

    loop Every scheduler run
        SC->>SV: evaluate open cases against category SLA
    end
    SC-->>OF: 50% elapsed — reminder
    SC-->>OF: 80% elapsed — second reminder, case shows as at risk
    alt SLA breached at 100%
        SV->>SV: set Escalated flag
        SV-->>DH: notify: SLA breached
        opt Still open at twice the SLA
            SV-->>DH: escalate to senior authority
        end
    end
    opt Submitter escalates manually after SLA has passed
        CH->>SV: escalate, with a reason
        SV-->>DH: same notifications, marked submitter-triggered
    end
    Note over SV: Escalated flag clears when department files response<br/>— status is untouched throughout
```

---

## 2.3.6 — Closure: confirm, reopen, or the window expiring

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant OF as Assigned Officer
    participant SV as Grievance Service
    participant CH as Submitter channel

    OF->>SV: file the structured response
    SV->>SV: status becomes Pending Submitter, 7-day window opens
    SV-->>CH: full response delivered
    alt Submitter confirms
        CH->>SV: confirm, with optional satisfaction rating
        SV->>SV: Resolved, then Closed
    else Submitter reopens
        CH->>SV: reopen, reason mandatory
        SV->>SV: status returns to In Progress
        SV-->>OF: notify: case reopened
    else No reply within 7 days
        SV->>SV: auto-close as no objection received
        SV-->>CH: notify: closed
    end
```

---

## 2.3.7 — Attachment upload and scan verdict

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant SB as Submitter
    participant SV as Grievance Service
    participant AV as Malware scanner

    SB->>SV: upload a supporting document
    SV->>SV: check type, size and per-case attachment limit
    SV->>AV: queue file for scanning, verdict pending
    SV-->>SB: accepted, scan in progress
    alt Verdict clean
        AV-->>SV: clean
        SV->>SV: attachment becomes downloadable
    else Verdict infected
        AV-->>SV: infected
        SV->>SV: quarantine file and mark attachment blocked
        SV-->>SB: notify: file rejected, upload replacement
    end
    Note over SV: draft attachments re-parent onto case when submitted
```

---

## 2.3.8 — Anonymity

```mermaid
%%{init: {
  "theme": "default",
  "themeVariables": {
    "actorBkg": "#eef2ff",
    "actorBorder": "#4dabf7",
    "actorTextColor": "#1e293b",
    "actorLineColor": "#475569",
    "signalColor": "#334155",
    "signalTextColor": "#0f172a",
    "labelBoxBkgColor": "#e2e8f0",
    "labelBoxBorderColor": "#64748b",
    "labelTextColor": "#0f172a",
    "loopTextColor": "#0f172a",
    "noteBkgColor": "#fff3cd",
    "noteTextColor": "#000000",
    "noteBorderColor": "#d39e00",
    "sequenceNumberColor": "#ffffff"
  },
  "sequence": {
    "noteMargin": 25
  }
}}%%
sequenceDiagram
    autonumber
    participant SB as Submitter
    participant SV as Grievance Service
    participant OF as Officers and staff

    SB->>SV: request anonymity on the case
    SV->>SV: set Anonymized flag, status untouched
    SV->>OF: officer views now show an anonymous id
    Note over SV: full identity stays inside Grievance Service<br/>and never reaches an officer view
    opt Submitter withdraws the request
        SB->>SV: withdraw anonymity
        SV->>OF: officer views show the submitter again
    end
```
