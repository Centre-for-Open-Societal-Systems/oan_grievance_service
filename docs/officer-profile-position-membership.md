# Officer Model: Position and Desk Membership

## Overview

The officer data model separates the organizational chart post from desk membership:

| Entity                                | What it is                       | One row per                                            |
| ------------------------------------- | -------------------------------- | ------------------------------------------------------ |
| **User (Core)**                       | The human account                | Person (login, credentials, email, phone)              |
| **Grievance Officer Position**        | The seat / post in the hierarchy | Post in the org chart (e.g. "Ada'a Woreda – Crop SMS") |
| **Grievance RBAC Assignment Officer** | Desk membership                  | `(desk, position)` pair                                |

## Field Distribution

- **User**: Authentication, name, email, phone, login enabled/disabled.
- **Grievance Officer Position**:
  - `position_title`: Canonical title of the seat.
  - `role_level`: Link to `Grievance Role Level` (hierarchy rung).
  - `administrative_area`: Geographic jurisdiction of the seat.
  - `occupant`: Link to `User` (the officer currently holding the seat; optional to allow vacant seats).
  - `reports_to_position`: Link to `Grievance Officer Position` (reporting ladder survives personnel transfers).
  - `designation`: Post title.
  - `active`: Whether the post exists and is active.
  - `on_leave`, `leave_from`, `leave_to`: Officer leave status and schedule.
  - `max_open_cases`: Workload capacity limit (0 = uncapped).
- **Grievance RBAC Assignment Officer** (Desk Membership):
  - `position`: Link to `Grievance Officer Position`.
  - `user`: Link to `User` (hydrated/mirrored from position occupant).
  - `role_level`: Link to `Grievance Role Level`.
  - `reports_to`: Direct supervisor.
  - `administrative_area`: Area covered.
  - `is_primary`: Primary officer flag for the desk.
  - `active`: Whether the seat actively serves this desk.
  - `last_assigned_at`: Timestamp for round-robin rotation.

## 4-Rung Hierarchy Ladder

1. **L1 (`nodal_officer`)**: Kebele Nodal Officer / DA
2. **L2 (`senior_nodal_officer`)**: Woreda Subject Matter Specialist
3. **L3 (`zonal_head`)**: Zonal Desk Head / Coordinator
4. **L4 (`department_head`)**: Regional / Federal Department Head
