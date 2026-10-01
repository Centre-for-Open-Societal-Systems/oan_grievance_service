# Project Structure & Conventions — OAN Grievance Service

> **For AI agents / contributors:** Read this before creating, modifying, or moving files. It describes the _actual_ layout, architecture, and conventions of **`oan_grievance_service`** (a Frappe Framework v16 Python backend application). Mirror existing patterns — do not invent new directory structures or bypass established architectural layers.

---

## 1. Technology Stack

| Layer / Concern               | Technology in this repo         | Notes                                                  |
| :---------------------------- | :------------------------------ | :----------------------------------------------------- |
| **Framework**                 | Frappe Framework (`version-16`) | Python backend, DocType meta-model                     |
| **Language**                  | Python 3.14+                    | Strict type hinting, Pydantic schemas                  |
| **Database**                  | MariaDB 11.x                    | Relational storage, nested set trees                   |
| **Caching & Queues**          | Redis (Cache & Queue instances) | Caching and background job execution                   |
| **Auth & Routing Dependency** | `oan_auth_service`              | Mandatory dependency for auth, JWT, RBAC, REST routing |
| **API Gateway**               | Kong                            | Route definitions generated from OpenAPI specs         |
| **API Contract**              | OpenAPI 3.1                     | Generated via `openapi/generate_openapi_spec.py`       |
| **Linting & Formatting**      | Ruff & Pre-commit               | `ruff check`, `ruff format`, prettier for markdown     |

---

## 2. Directory Layout

```
oan_grievance_service/
├── oan_grievance_service/              # Primary Python package
│   ├── api/                            # Versioned HTTP REST API Layer
│   │   ├── router.py                   # REST route registration into Frappe URL Map
│   │   ├── middleware.py               # Request hooks & header injection
│   │   └── v1/                         # Version 1 REST controllers (thin handlers)
│   │       ├── grievance.py            # Grievance intake, tracking, lifecycle actions
│   │       ├── category_assignment.py  # Category routing admin CRUD
│   │       ├── administrative_area.py  # Administrative area tree queries
│   │       ├── attachment.py           # Document evidence upload & scan status
│   │       ├── submitter.py            # Submitter profile & lookup endpoints
│   │       ├── draft.py                # Grievance draft save & resume
│   │       └── change_request.py       # Formal grievance change requests
│   ├── services/                       # Rich Domain Service Layer
│   │   ├── routing.py                  # Nearest-ancestor area & RBAC auto-assignment
│   │   ├── sla.py                      # Working-day SLA calculation & breach tracking
│   │   ├── lifecycle.py                # Status machine & workflow transitions
│   │   ├── notification.py             # Multi-channel notification dispatcher
│   │   ├── ticket_number.py            # Canonical ticket formatting (e.g. 3-001-002A-0)
│   │   └── virus_scanner.py            # ClamAV scan pipeline & integrity checks
│   ├── grievance_management/           # Core Grievance Intake & Operations
│   │   └── doctype/                    # Grievance, Grievance Note, Timeline Entry, etc.
│   ├── grievance_routing/              # Routing Rules & Administrative Hierarchy
│   │   └── doctype/                    # Grievance Category Assignment, Administrative Area
│   ├── grievance_sla/                  # SLA Configurations & State Timers
│   │   └── doctype/                    # Grievance SLA Configuration, State Timer, Holiday List
│   ├── grievance_access_control/       # RBAC & Desk Rosters
│   │   └── doctype/                    # Grievance RBAC Assignment, Role Level
│   ├── grievance_masters/              # Master Data & Categories
│   │   └── doctype/                    # Service Category, Department, Channel, Priority
│   ├── grievance_notifications/        # Notification Templates & Logs
│   │   └── doctype/                    # Notification Template, Notification Log
│   ├── permissions.py                  # Deny-by-default query permission filters (FR-01)
│   ├── tasks.py                        # Scheduled background jobs (SLA monitor, escalations)
│   ├── hooks.py                        # Frappe application hooks & cron definitions
│   └── patches.txt                     # Database migration patches (APPEND-ONLY)
├── openapi/                            # OpenAPI 3.1 generator & specs
│   ├── generate_openapi_spec.py        # Single source of truth for API contracts
│   ├── openapi_v1.yaml                 # Internal/Officer OpenAPI specification
│   └── openapi_v1.public.yaml          # Public/Citizen OpenAPI specification
├── kong/                               # Kong API Gateway declarative configurations
│   ├── generate_kong_config_from_spec.py # Generates kong.yml from OpenAPI specs
│   └── kong.yml                        # Declarative Kong gateway configuration
├── postman/                            # Postman integration test suites
│   └── oan_grievance_rest_collection.json # Automated endpoint verification collection
├── docs/                               # Architectural documentation & specifications
└── tests/                              # Global test suites
```

---

## 3. Module & DocType Anatomy

Each DocType lives in its respective module directory with colocated schema, controller, and tests:

```
oan_grievance_service/<module>/doctype/<doctype_name>/
├── __init__.py
├── <doctype_name>.json                 # DocType metadata schema & permissions
├── <doctype_name>.py                   # Python controller (validations & lifecycle hooks)
└── test_<doctype_name>.py              # FrappeTestCase integration tests
```

### Module Responsibilities

| Module                     | Purpose & Core DocTypes                                                                                                                                                                      |
| :------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `grievance_management`     | Core intake, tracking, timeline, notes, attachments, change requests (`Grievance`, `Grievance Submitter Profile`, `Grievance Change Request`, `Grievance Note`, `Grievance Timeline Entry`). |
| `grievance_routing`        | Administrative boundary tree and routing rules (`Grievance Administrative Area`, `Grievance Category Assignment`).                                                                           |
| `grievance_sla`            | SLA windows, milestone cadences, holiday calendars (`Grievance SLA Configuration`, `Grievance State Timer`, `Grievance Holiday List`).                                                       |
| `grievance_access_control` | Desk assignments, role levels, officer hierarchies (`Grievance RBAC Assignment`, `Grievance Role Level`).                                                                                    |
| `grievance_masters`        | Catalog metadata (`Grievance Service Category`, `Grievance Department`, `Grievance Channel`, `Grievance Priority`, `Grievance Status Mapping`).                                              |
| `grievance_notifications`  | Templates and dispatch audit history (`Grievance Notification Template`, `Grievance Notification Log`, `Grievance Notification Config`).                                                     |

---

## 4. API & Controller Conventions

> Refer to [`docs/api-development-standards.md`](../docs/api-development-standards.md) for the complete REST API development standards, decorator pipeline execution order, Pydantic validation rules, and error envelope specifications.

### 4.1. Layer Responsibilities & Architectural Boundaries

- **API Controllers (`api/v1/`)**: Act strictly as HTTP gateways. Responsible for route definition, authentication, request payload validation (`@validate_request` / Pydantic), invoking services, and building standard JSON response envelopes. Never write raw database queries or core domain algorithms inside API handlers.
- **Domain Services (`services/`)**: Dedicated purely to **business logic** and multi-entity workflows (e.g. SLA clock & working-day calculations, nearest-ancestor area routing algorithms, state-machine transitions, ClamAV virus scanning, notification dispatch orchestration, ticket number generation). **Services are NOT for input/field validation** (handled by Pydantic schemas and DocType `validate()`) **and NOT raw database query wrappers.**
- **DocType Controllers (`doctype/<doctype>/<doctype>.py`)**: Responsible for document-level field validation (`validate()`), link integrity checks, and internal lifecycle projections (`before_insert()`, `on_update()`).
- **Query & Permission Layer (`permissions.py`)**: Responsible for deny-by-default access control filters and SQL query conditions.

### 4.2. URL Mapping & Naming Standards

- Collections use plural nouns in lowercase kebab-case (`/api/v1/category-assignments`, `/api/v1/administrative-areas`).
- Detail endpoints use path parameters (`/api/v1/category-assignments/<assignment>`).
- Non-CRUD lifecycle actions use clear POST action sub-paths (`/api/v1/grievances/<ticket>/reopen`, `/escalate`).

---

## 5. Where Do I Put a New...?

| You are adding...                          | Destination Directory                                                                             |
| :----------------------------------------- | :------------------------------------------------------------------------------------------------ |
| **A new REST API endpoint**                | `oan_grievance_service/api/v1/<resource>.py` (register in `api/router.py` & `api/v1/__init__.py`) |
| **A core business service or calculation** | `oan_grievance_service/services/<domain>.py`                                                      |
| **A new DocType**                          | `oan_grievance_service/<module>/doctype/<doctype_name>/`                                          |
| **A background scheduled job**             | `oan_grievance_service/tasks.py` (wired in `hooks.py` `scheduler_events`)                         |
| **A database migration patch**             | `oan_grievance_service/patches/<patch_name>.py` (appended to `patches.txt`)                       |
| **OpenAPI schema / endpoint definition**   | `openapi/generate_openapi_spec.py`                                                                |
| **Kong gateway route**                     | `kong/generate_kong_config_from_spec.py`                                                          |
| **Postman API test**                       | `postman/oan_grievance_rest_collection.json`                                                      |

---

## 6. Development & Verification Commands

### 6.1. Test Suite Organization Strategy

- **Colocated DocType Unit Tests (`doctype/<name>/test_<name>.py`)**: Exactly one test file per DocType testing invariants and document validations. Avoid multiple test files inside a single DocType folder.
- **Domain & Feature Suites (`tests/`)**: Cross-cutting API and domain tests belong in `oan_grievance_service/tests/`.
- **No PR-Specific Test Files**: Never create temporary regression test files named after pull requests (e.g., `test_pr19_review_fixes.py`). Integrate regression assertions into the relevant domain test suite (`test_router.py`, `test_draft.py`, etc.).

### 6.2. Common Development & Verification Commands

```bash
# Run unit & integration tests
bench --site <site> run-tests --app oan_grievance_service

# Run a specific test suite
bench --site <site> run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_router

# Apply migrations after changing DocType JSON or adding patches
bench --site <site> migrate

# Clear cache after modifying hooks.py or system fixtures
bench --site <site> clear-cache

# Lint & format Python code
ruff check oan_grievance_service/
ruff format oan_grievance_service/

# Regenerate OpenAPI 3.1 specs
/Users/arnav/Code/frappe_local/frappe-bench/env/bin/python openapi/generate_openapi_spec.py

# Regenerate Kong gateway configuration
/Users/arnav/Code/frappe_local/frappe-bench/env/bin/python kong/generate_kong_config_from_spec.py
```

---

## 7. Documentation Index

- [`AGENTS.md`](../AGENTS.md) — Comprehensive AI agent instructions and development standards
- [`README.md`](../README.md) — Project overview, installation, and environment configuration
- [`SETUP.md`](../SETUP.md) — Development environment setup guide
- [`docs/api-development-standards.md`](../docs/api-development-standards.md) — REST API architecture and decorator pipeline
- [`docs/database-schema.md`](../docs/database-schema.md) — Complete relational schema and nested set routing documentation
- [`docs/merge-hygiene.md`](../docs/merge-hygiene.md) — Merge conflict prevention and append-only patch conventions
- [`docs/sla_workflows_and_lifecycle_specification.md`](../docs/sla_workflows_and_lifecycle_specification.md) — SLA calculations and state transition rules
