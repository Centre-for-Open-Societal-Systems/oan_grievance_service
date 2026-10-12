# AGENTS.md — AI Agent Guidelines for OAN Grievance Service

This document provides essential instructions, architectural conventions, and development workflows for AI agents (and human contributors) working on **`oan_grievance_service`**.

---

## 1. Project Overview & Tech Stack

`oan_grievance_service` is the grievance redressal backend for OpenAgriNet Ethiopia, implemented as a **Frappe Framework** application. It provides multi-channel intake, automated routing, SLA computation and breach escalation, status tracking, notifications, and document verification.

| Component                | Technology / Tool               | Notes                                                  |
| :----------------------- | :------------------------------ | :----------------------------------------------------- |
| **Framework**            | Frappe Framework (`version-16`) | Python backend, DocType meta-architecture              |
| **Language**             | Python 3.14+                    | Strict typing hints, Pydantic schemas                  |
| **Database**             | MariaDB 11.x                    | Relational storage, nested set area hierarchy          |
| **Caching & Queues**     | Redis                           | Two instances: cache and background worker queue       |
| **Required App**         | `oan_auth_service`              | Mandatory dependency for auth, JWT, RBAC, REST routing |
| **API Gateway**          | Kong                            | Route definitions in `kong/kong.yml`                   |
| **API Contract**         | OpenAPI 3.1                     | Generated via `openapi/generate_openapi_spec.py`       |
| **Linting & Formatting** | Ruff & Pre-commit               | `ruff check`, `ruff format`, prettier for markdown     |

---

## 2. Directory Structure

```
oan_grievance_service/
├── oan_grievance_service/          # Primary Python package
│   ├── api/                        # Versioned HTTP API Layer
│   │   ├── router.py               # REST route registration into Frappe URL Map
│   │   └── v1/                     # Version 1 REST controllers (thin handlers)
│   ├── services/                   # Core business logic & domain services
│   │   ├── routing.py              # RBAC & area-aware auto-assignment
│   │   ├── sla.py                  # SLA calculation & milestone tracking
│   │   ├── lifecycle.py            # Status transitions & workflow execution
│   │   ├── notification.py         # Multi-channel notification dispatcher
│   │   ├── ticket_number.py        # Canonical ticket format generator/parser
│   │   └── virus_scanner.py        # ClamAV scan pipeline
│   ├── grievance_management/       # Core Grievance intake, tracking & actions
│   ├── grievance_routing/          # Category routing rules & desk assignments
│   ├── grievance_sla/              # SLA configs, holidays & milestones
│   ├── grievance_access_control/   # RBAC assignments & role levels
│   ├── grievance_masters/          # Categories, departments, channels, priorities
│   ├── grievance_notifications/    # Templates, logs & notification configs
│   ├── permissions.py              # Deny-by-default query conditions (FR-01)
│   ├── tasks.py                    # Background scheduled jobs (SLA, escalations)
│   ├── hooks.py                    # Frappe app event hooks & scheduled jobs
│   └── patches.txt                 # Migration patches (APPEND-ONLY)
├── openapi/                        # OpenAPI 3.1 specifications & generator
├── kong/                           # Kong gateway configs & generator
├── postman/                        # Postman integration test collection
├── docs/                           # Architecture guides & specifications
└── tests/                          # Suite-wide integration tests
```

---

## 3. Core Architecture & API Standards

> Refer to [`docs/api-development-standards.md`](docs/api-development-standards.md) for the complete REST API development standards, decorator pipeline execution order, Pydantic validation rules, and error envelope specifications.

### Layer Boundaries & Separation of Concerns

- **API Handlers (`api/v1/`)**: Act strictly as HTTP gateways. They perform route handling, authentication, request schema parsing/validation, call domain services, and return standard JSON response envelopes. **Never write raw database queries or core business logic inside API handlers.**
- **Service Layer (`services/`)**: Dedicated exclusively to **domain business logic** and multi-entity workflows (e.g. SLA clock & working-day calculations, nearest-ancestor tree routing algorithms, status workflow execution, ClamAV virus scan pipelines, notification dispatch orchestration, ticket number parsing/generation). **Services are NOT for input/field validation** (which belongs in Pydantic schemas and DocType `validate()`) **and NOT raw data access/query wrappers.**
- **DocType Controllers (`doctype/<name>/<name>.py`)**: Enforce document-level integrity, invariant rules, and field validations (`validate()`), along with direct document lifecycle hooks (`before_insert()`, `on_update()`).
- **Permissions & Query Layer (`permissions.py` / Frappe Hooks)**: Implements deny-by-default row-level access control and query condition filters.

---

## 4. DocType & Data Model Guidelines

1. **DocType Colocation**: DocType schema (`.json`), Python controller (`.py`), and test file (`test_*.py`) must reside together in `doctype/<doctype_name>/`.
2. **Audit Trails & Soft Deletion**:
   - For auditable master/configuration records (e.g. `Grievance Category Assignment`), disable hard delete (`"delete": 0` in permissions) and implement soft deactivation (`active = 0`).
3. **Database Constraints & Link Validation**:
   - Use `unique: 1` in DocType JSON for business uniqueness (e.g. `service_category`).
   - Validate that linked documents exist and are active using `frappe.db.exists()` and status flags.
4. **Patches are Append-Only**:
   - `oan_grievance_service/patches.txt` is **strictly append-only**. Never reorder existing lines or tidy older entries.
5. **Deny-by-Default RBAC (`permissions.py`)**:
   - Frappe query conditions must enforce that officers only access cases assigned to their department, area, or role level unless granted admin override (`is_unrestricted(user)`).

---

## 5. Specification & Gateway Sync Requirements

Whenever you add, modify, or deprecate an API endpoint:

1. **Update OpenAPI Generator**: Add/update route and schema definitions in `openapi/generate_openapi_spec.py`.
2. **Regenerate OpenAPI Specifications**:
   ```bash
   python openapi/generate_openapi_spec.py
   ```
   Ensures `openapi/openapi_v1.yaml` and `openapi/openapi_v1.public.yaml` stay synchronized.
3. **Update Kong Generator**: Add the route in `kong/generate_kong_config_from_spec.py`.
4. **Regenerate Kong Configuration**:
   ```bash
   python kong/generate_kong_config_from_spec.py
   ```
5. **Update Postman Collection**: Maintain endpoint examples and automated tests in `postman/oan_grievance_rest_collection.json`.

---

## 6. Coding Standards & Hygiene

- **Internationalization (i18n)**: Wrap all user-facing error and response strings in `frappe._()`:
  ```python
  frappe.throw(_("Service category '{0}' does not exist.").format(name), frappe.ValidationError)
  ```
- **No Console/Debug Artifacts**: No `print()`, `console.log`, or `debugger` statements in committed code.
- **Child Tables**: Reset child table rows idiomatically via `doc.set("child_table_field", [])` before repopulating.
- **In-Memory Document Sync**: When using `self.db_set("field", value, update_modified=False)`, synchronize the in-memory attribute (`self.field = value`) to avoid stale state in ORM instances.
- **Deterministic Sorting**: Always add a unique secondary tie-breaker for paginated queries (e.g., `order_by="modified desc, name desc"`).

---

## 7. Testing & Quality Verification

### 7.1. Test Suite Organization Strategy

- **DocType Unit Tests (`doctype/<name>/test_<name>.py`)**: Keep a single colocated test file per DocType for document-level field validations (`validate()`), link integrity, and direct lifecycle hooks. **Do not create multiple scattered test files inside a DocType directory** (e.g., avoid `test_list_*.py`, `test_timeline_*.py` inside `doctype/`).
- **Domain & API Integration Suites (`tests/`)**: High-level cross-cutting tests (API endpoints, routing, SLA engine, workflow contracts, background tasks) belong in `oan_grievance_service/tests/` (e.g., `test_router.py`, `test_sla_engine.py`, `test_workflow_contracts.py`).
- **No PR-Specific Test Files**: **Never create one-off test files named after pull requests or review tickets** (e.g. `test_pr19_review_fixes.py`). Place regression test cases directly into the appropriate domain test file (`test_router.py`, `test_draft.py`, etc.).

### 7.2. Test Scenarios to Cover

- **Happy Path**: Successful CRUD, state transitions, and expected side-effects.
- **Entity Projections**: Verifying automated updates on linked SLA configs, RBAC desks, and timeline logs.
- **Negative & Boundary Paths**: Zero/negative values, empty strings, duplicate keys, boundary pagination limits (`page_size > 100`), invalid enum values.
- **Permission & Security Checks**: Unauthenticated calls (Guest -> 401) and unauthorized staff roles (e.g., `Grievance Officer` on admin endpoints -> 403).
- **Immutability Constraints**: Verifying fixed fields (e.g., `service_category` on routing rules) cannot be modified via PATCH.

### 7.3. Running Tests & Quality Verification

```bash
# Run entire test suite
bench --site <site> run-tests --app oan_grievance_service

# Run a specific test module
bench --site <site> run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_router

# Linting & code formatting
ruff check oan_grievance_service/
ruff format oan_grievance_service/
```

---

## 8. Git & Merge Discipline

- **Do Not Reformat Untouched Code**: Let formatting tools handle formatting. Avoid hand-formatting tables or adding whitespace in files you are not modifying to minimize merge conflicts.
- **Safety First**:
  - Never use `git reset --hard` or `git checkout <file>` to undo changes.
  - Always use `git stash push -m "Agent undo" -- <file>` or `git reset --soft`.
  - Always run `gh repo sync` before git operations.
  - Never commit or push without explicit user confirmation.
