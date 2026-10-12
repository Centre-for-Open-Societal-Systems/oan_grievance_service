An organisation representative can onboard their entity (NGO, FPO, or cooperative) so it can operate on the platform and manage collective workflows.

## 2.3.9 — Organisation Onboarding (NGO / FPO / Cooperative)

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
    participant REP as Org Representative
    participant UI as Grievance Web Application
    participant SV as Grievance Service
    participant ADM as Admin

    note over REP,SV: Phase 1 — Representative Sign-on
    REP->>UI: Submit personal registration details
    UI->>SV: POST /api/v1/auth/register
    note over SV: User account created &<br/>token issued
    SV-->>UI: Account ready (auth token)
    UI-->>REP: Logged in

    note over REP,SV: Phase 2 — Organisation Registration
    REP->>UI: Provide legal name & org type (NGO / FPO / Coop)
    UI->>SV: POST /v1/orgs
    note over SV: persisted to org master<br/>user permission bound<br/>status = "In Review"
    SV-->>UI: Org created (In Review)
    UI-->>REP: Org registered

    note over REP,SV: Phase 3 — Editing / Updating Additional Information
    REP->>UI: Update brand logo, address, contacts & KYC documents
    UI->>SV: Save profile metadata, contacts & compliance PDFs
    note over SV: persisted to org_profile,<br/>org_contacts & org_documents
    SV-->>UI: Additional information saved
    UI-->>REP: Profile & KYC dossier updated

    note over ADM,SV: Phase 4 — Admin Audit & Activation
    ADM->>UI: Inspect organization details & KYC documents
    UI->>SV: Fetch org dossier
    SV-->>UI: Return org profile & documents
    ADM->>UI: Approve organization
    UI->>SV: PATCH /v1/orgs/me/status (status: "Active")
    note over SV: lifecycle state updated<br/>status = "Active"
    SV-->>UI: Organization activated
    UI-->>ADM: Activation confirmed
```

### Endpoints & Access Matrix

| Method  | Path                    | Access            | Purpose                                                                                                                                                                  |
| :------ | :---------------------- | :---------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST`  | `/api/v1/auth/register` | `Public`          | Register a personal account — the route an organization representative uses before requesting an organization account. (Not a path a farmer or Development Agent takes). |
| `POST`  | `/v1/orgs`              | `Registered User` | Registers a new organization, creates user permission binding, and sets status to`"In Review"`.                                                                          |
| `GET`   | `/v1/orgs/me`           | `Registered User` | Retrieves caller’s organization details, brand info, address, compliance contacts, and KYC status.                                                                       |
| `PATCH` | `/v1/orgs/me`           | `Registered User` | Updates organization profile, registered address, brand name, website, and logo reference.                                                                               |
| `POST`  | `/v1/orgs/images`       | `Registered User` | Uploads public PNG/JPEG/WebP images (max 5MB) for brand logos.                                                                                                           |
| `POST`  | `/v1/orgs/me/documents` | `Registered User` | Uploads private PDF regulatory and KYC compliance documents.                                                                                                             |
| `GET`   | `/v1/orgs/me/documents` | `Registered User` | Streams/downloads the private KYC document (`?view=1` for inline display).                                                                                               |
| `PUT`   | `/v1/orgs/me/contacts`  | `Registered User` | Sets organization contacts (compliance, operational, emergency).                                                                                                         |
| `GET`   | `/v1/orgs/`             | `Admin`           | Retrieves all organization details, brand info, address, compliance contacts, and KYC status.                                                                            |
| `GET`   | `/v1/orgs/{org-name}`   | `Admin`           | Retrieves specific organization details, brand info, address, compliance contacts, and KYC status.                                                                       |
| `PATCH` | `/v1/orgs/me/status`    | `Admin`           | Platform operator action to transition lifecycle (`In Review` $\rightarrow$ `Active` / `Suspended`).                                                                     |

---

Organisation onboarding is an administrative gate before active participation. It binds the representative's account to the entity, gathers compliance assets and private KYC documentation, and holds the organisation In Review until a platform administrator audits the dossier and transitions it to Active.
