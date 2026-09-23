# AI Verification Specification: Grievance Dynamic Workflow Contracts

> **Audience**: AI Coding Agents, Automated Evaluators, and Architecture Reviewers.  
> **Purpose**: This document provides an implementation-agnostic evaluation specification to verify that the dynamic Frappe-native workflow, response-type mapping, and status-change audit requirements are correctly and robustly implemented.

---

## 1. Architectural Compliance Guardrails (Frappe-Native)

An AI inspecting or validating the codebase MUST verify the following design invariants:

| #        | Check Rule                                | Evaluation Standard                                                                                                                                                                                                            | Violation Anti-Pattern (FAIL)                                                                                                                                        |
| :------- | :---------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **AC-1** | **No Hardcoded Status or Response Enums** | Statuses must be defined in Frappe's native `Workflow State`, and response types must be defined in `Grievance Response Type` master records.                                                                                  | Python dictionaries or sets defining legal transitions (e.g. `ALLOWED_TRANSITIONS = {...}`) or response mappings (e.g. `RESPONSE_OUTCOME_NEXT_STATUS = {...}`).      |
| **AC-2** | **Use Standard Workflow Engine**          | State transitions must execute via `frappe.model.workflow.apply_workflow(doc, action)` or declarative UI action buttons.                                                                                                       | Direct `doc.db_set("status", ...)` or `doc.status = ...` bypassing the workflow engine.                                                                              |
| **AC-3** | **Native Terminal Locking**               | Terminal states (`Closed`, `Rejected`) must be permanently locked using Frappe's native `Workflow Document State.doc_status = 1` (`Closed`) and `doc_status = 2` (`Rejected`), with zero outbound transitions.                 | Allowing transitions out of `Closed`/`Rejected` (reopening is strictly during the 7-day `Pending Submitter` grace period) or using custom python edit-guard scripts. |
| **AC-4** | **Centralized Reason Gatekeeping**        | Mandatory reason validation must reside directly in `GrievanceStatusHistory.validate()`. Every status change must insert a history row; if `reason` is missing on restricted transitions, the database transaction must abort. | Scattered `if not reason:` checks across separate API controller endpoints, leaving Desk or background tasks unguarded.                                              |
| **AC-5** | **Zero Duplicate State Storage**          | Grievance must bind its active stage to `workflow_state` (Link $\rightarrow$ `Workflow State`). If `status` is retained for external API backwards compatibility, it must be automatically synchronized.                       | Storing two independent status fields that can drift out of sync.                                                                                                    |

---

## 2. Invariant Contracts & Behavioral Scenarios

### Contract A: Response-Type to Workflow State Mapping (Dynamic Master Resolution)

When an officer files a formal `Grievance Response`, the system dynamically resolves `target_workflow_state` and `sla_behaviour` from the linked `Grievance Response Type` master record (zero Python dictionaries):

```
Input: Grievance in an active working state ('In Progress') + Grievance Response document (linked to Grievance Response Type)
```

1. **Outcome: `Resolved`**

   - **Preconditions**: Response contains non-empty `action_taken` (≤ 500 chars), `resolution_summary`, and `proposed_close_date`.
   - **Postconditions**:
     - Case advances to `Pending Submitter`.
     - 7-day submitter confirmation window opens (`confirmation_deadline` calculated).
     - SLA clock pauses (`Workflow State.sla_behaviour == 'paused'`).
     - `Grievance Response.new_status` is stamped `Pending Submitter`.

2. **Outcome: `Partially Resolved`**

   - **Preconditions**: Same response fields as `Resolved`.
   - **Postconditions**:
     - Case advances to `Pending Submitter`.
     - Confirmation window opens.
     - SLA clock pauses.
     - `Grievance Response.new_status` is stamped `Pending Submitter`.

3. **Outcome: `Referred to another dept`**

   - **Preconditions**: `referred_to_department` MUST be set and valid.
   - **Postconditions**:
     - Case advances to `Assigned`.
     - Nodal officer notified for reassignment routing.
     - `Grievance Response.new_status` is stamped `Assigned`.

4. **Outcome: `Requires further info`**
   - **Preconditions**: Response contains the specific query/clarification needed.
   - **Postconditions**:
     - Case advances to `More Info Needed`.
     - Submitter notified.
     - SLA clock pauses until submitter replies.
     - `Grievance Response.new_status` is stamped `More Info Needed`.

---

### Contract B: Status Change Reason Enforcement

Every state transition must create an entry in `Grievance Status History`. The history document itself enforces justification rules:

1. **Rejection Justification Contract**:

   - **Condition**: Transition where `to_status == 'Rejected'`.
   - **Pass Criteria**: `Grievance Status History.reason` is non-empty.
   - **Fail Criteria**: Missing or whitespace-only `reason` raises `frappe.ValidationError` and cancels the transaction.

2. **Reopen Justification Contract**:

   - **Condition**: Transition from `'Pending Submitter'` to `'In Progress'`.
   - **Pass Criteria**: `Grievance Status History.reason` is non-empty.
   - **Fail Criteria**: Missing or whitespace-only `reason` raises `frappe.ValidationError` and cancels the transaction.

3. **General Transition Audit Contract**:
   - For all transitions, `from_status`, `to_status`, `changed_by`, `timestamp` must be captured.

---

### Contract C: Transition Guard Invariants

Evaluated in application code inside `before_workflow_action(doc, action)`:

1. **Response Guard**:
   - Transition to `Pending Submitter` MUST fail if no valid `Grievance Response` of type `Resolved` or `Partially Resolved` exists for the current resolution cycle.
   - Transition to `More Info Needed` MUST fail if no valid `Grievance Response` of type `Requires further info` exists.
2. **Assignment Gate**:
   - Assignment is the first operational action during intake/routing immediately following submission; investigation (`In Progress`) cannot start without an assigned department/officer.
3. **Evidence Guard**:
   - Transition where evidence is mandatory before resolution MUST fail if `Grievance Attachment` count for the case is 0.

---

### Contract D: Audit Immutability & Cryptographic Hash Chaining

For every row $k$ inserted into `Grievance Status History`:

1. **Hash Chaining Equation**:
   $$\text{prev\_hash}_k = \begin{cases} 0^{64} & \text{if } k = 1 \\ \text{row\_hash}_{k-1} & \text{if } k > 1 \end{cases}$$
2. **Row Hash Calculation**:
   $$\text{row\_hash}_k = \text{SHA256}(\text{prev\_hash}_k \mathbin{\Vert} \text{grievance} \mathbin{\Vert} \text{from\_status} \mathbin{\Vert} \text{to\_status} \mathbin{\Vert} \text{timestamp} \mathbin{\Vert} \text{changed\_by} \mathbin{\Vert} \text{reason} \mathbin{\Vert} \text{closure\_type} \mathbin{\Vert} \text{is\_automated})$$
3. **Append-Only Guarantee**:
   - Updates and deletes on `Grievance Status History` must be blocked unconditionally.
4. **Timeline Spine Synchronization**:
   - Every history entry MUST emit a corresponding entry on `Grievance Timeline` (`entry_type = 'status_change'`).

---

### Contract E: SLA Clock Dynamics

SLA clock state transitions are driven by `Grievance Response.sla_behaviour` and terminal outcomes:

1. **When a Response Sets `sla_behaviour == 'paused'` (e.g. Requires Further Info, Pending Submitter)**:
   - `on_hold_since` timestamp is recorded.
   - Clock stops counting toward deadline elapsed time.
2. **When Exiting a Paused State (e.g. Submitter replies, moving back to In Progress)**:
   - Elapsed hold duration ($\text{now} - \text{on\_hold\_since}$) is accumulated into `total_hold_time`.
   - `on_hold_since` is cleared to `NULL`.
3. **When Entering a Terminal State (`Closed` / `Rejected`)**:
   - Clock is frozen; no further reminders or escalation jobs process the case.

---

## 3. AI Evaluator Execution Checklist (Prompt Instructions)

When an AI agent is instructed to audit or verify an implementation of this workflow, it should execute the following evaluation steps:

```markdown
### Verification Checklist for AI Reviewer:

- [ ] 1. Schema Inspection:
  - Verify native `Workflow State` and `Workflow Transition` remain clean vanilla Frappe DocTypes (zero custom fields added).
  - Verify `Grievance Response Type` master DocType exists in `grievance_masters` with fields: `response_type_name`, `target_workflow_state`, `sla_behaviour`, `requires_referred_dept`.
  - Verify `Grievance Response.response_type` is a `Link` to `Grievance Response Type`.
  - Verify `Grievance` links to `Workflow State` via `workflow_state`.

- [ ] 2. Terminal State Locking Inspection:
  - Check `Workflow Document State` for 'Closed' (`doc_status = 1`) and 'Rejected' (`doc_status = 2`).
  - Verify zero outbound transitions exist from 'Closed' or 'Rejected' (permanently terminal).
  - Verify active states ('Submitted' through 'Pending Submitter') have `doc_status = 0`.

- [ ] 3. Status History Reason Gatekeeping:
  - Inspect `GrievanceStatusHistory.validate()`.
  - Verify `reason` is required when `to_status == 'Rejected'`.
  - Verify `reason` is required when reopening (`from_status == 'Pending Submitter' and to_status == 'In Progress'`).
  - Verify exception raised is `frappe.ValidationError` or `frappe.throw`.

- [ ] 4. Response Type Correspondence:
  - Inspect response submission hook/controller.
  - Verify target state and SLA behaviour are read directly from the linked `Grievance Response Type` master record.
  - Verify absence of hardcoded `RESPONSE_OUTCOME_NEXT_STATUS` dictionary.

- [ ] 5. Anti-Pattern Scan:
  - Search codebase for direct `doc.db_set("status", ...)` or `ALLOWED_TRANSITIONS` dictionary lookups.
  - Ensure all state progressions route through Frappe's workflow engine or history-validated hooks.
```
