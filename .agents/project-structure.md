# Project Structure & Architectural Conventions — OAN Grievance Service

> **For AI agents / contributors:** Read this before creating, modifying, or organizing code in **`oan_grievance_service`** (a Frappe Framework v16 application). Follow established layer boundaries and conventions.

---

## 1. Technology Stack

| Layer / Concern               | Technology / Framework          | Notes                                                  |
| :---------------------------- | :------------------------------ | :----------------------------------------------------- |
| **Framework**                 | Frappe Framework (`version-16`) | Python backend, DocType meta-architecture              |
| **Language**                  | Python 3.14+                    | Strict type hinting, Pydantic schemas                  |
| **Database**                  | MariaDB 11.x                    | Relational storage, nested set area hierarchy          |
| **Caching & Queues**          | Redis                           | Cache and background worker queue                      |
| **Auth & Routing Dependency** | `oan_auth_service`              | Mandatory dependency for auth, JWT, RBAC, REST routing |
| **API Gateway**               | Kong                            | Gateway routes generated from OpenAPI specifications   |
| **API Contract**              | OpenAPI 3.1                     | Specification generators and schemas                   |
| **Linting & Quality**         | Ruff, Pre-commit & Semgrep      | Code formatting, Frappe hygiene, and security rules    |

---

## 2. Directory Layout & Layer Boundaries

```
oan_grievance_service/
├── oan_grievance_service/              # Primary Python application package
│   ├── api/                            # Versioned HTTP REST API Layer (thin handlers)
│   │   ├── router.py                   # REST route registration into Frappe URL Map
│   │   └── v1/                         # Version 1 REST controllers
│   ├── services/                       # Rich Domain Service Layer (business logic & workflows)
│   ├── grievance_*/                    # Domain DocType modules (masters, routing, SLA, RBAC, etc.)
│   ├── permissions.py                  # Deny-by-default query condition permissions
│   ├── tasks.py                        # Scheduled background worker jobs
│   ├── hooks.py                        # Frappe application hooks & cron schedules
│   └── patches.txt                     # Migration patches (APPEND-ONLY)
├── openapi/                            # OpenAPI 3.1 specifications & generator scripts
├── kong/                               # Kong gateway declarative configuration & generator
├── postman/                            # API integration test collection
├── docs/                               # Architectural documentation & specifications
└── tests/                              # Global & cross-cutting integration test suites
```

---

## 3. Separation of Concerns

- **API Controllers (`api/v1/`)**: Act strictly as HTTP gateways. Responsible for route definition (`@route`), authentication, request payload validation (`@validate_request` with Pydantic), invoking domain services, and returning standard JSON response envelopes. **Never write raw database queries or core domain algorithms inside API handlers.**
- **Domain Services (`services/`)**: Dedicated exclusively to **business logic** and multi-entity workflows (e.g. SLA clock & working-day calculations, routing algorithms, state-machine transitions, virus scanning pipelines, notification orchestration). **Services are NOT for input validation and NOT raw data-access wrappers.**
- **DocType Controllers (`doctype/<doctype_name>/<doctype_name>.py`)**: Responsible for document-level field validation (`validate()`), link integrity, and direct lifecycle hooks (`before_insert()`, `on_update()`).
- **Permissions & Query Layer (`permissions.py`)**: Responsible for deny-by-default access control filters and SQL query conditions based on area, department, and role tier.

---

## 4. Module & DocType Structure

Each DocType is colocated with its schema, Python controller, and test file:

```
oan_grievance_service/<module>/doctype/<doctype_name>/
├── __init__.py
├── <doctype_name>.json                 # DocType metadata schema & permissions
├── <doctype_name>.py                   # Python controller (validations & lifecycle hooks)
└── test_<doctype_name>.py              # Colocated unit tests
```

---

## 5. Testing Strategy & Two-Tier Organization

Tests are organized into two distinct tiers based on scope:

1. **Colocated DocType Unit Tests (`<module>/doctype/<doctype_name>/test_<doctype_name>.py`)**:

   - Dedicated exclusively to unit tests affecting **a single DocType** (field validations, link integrity, mandatory checks, and lifecycle hooks `validate()`, `before_insert()`, `on_update()`).
   - Exactly **one colocated test file per DocType** directory (never create multiple scattered test files inside a DocType folder).

2. **Global Cross-Cutting & Integration Tests (`tests/`)**:
   - Any test involving **cross-cutting workflows**, multiple DocTypes, REST API endpoints, routing engines, SLA calculations, background jobs, or multi-module contracts belongs **only in the global `tests/` folder** (`oan_grievance_service/tests/`).
   - **No PR-specific test files**: Never create temporary or review-specific test files (e.g. `test_pr19_review_fixes.py`). Place regression assertions directly into the appropriate domain test suite (`test_router.py`, `test_draft.py`, etc.).

### ⚠️ Strict Transaction Rule: No `frappe.db.commit()`

- **Never call manual `frappe.db.commit()` in any test.**
- Frappe automatically rolls back test transactions at the end of each test case. Manual commits break test isolation, leak state into subsequent tests, and pollute the test database.

---

## 6. Where Do I Put New Code?

| You are adding...                         | Destination Directory                                        |
| :---------------------------------------- | :----------------------------------------------------------- |
| **A new REST API endpoint**               | `oan_grievance_service/api/v1/`                              |
| **Core business logic or workflow logic** | `oan_grievance_service/services/`                            |
| **A new DocType schema & controller**     | `oan_grievance_service/<module>/doctype/<doctype_name>/`     |
| **DocType-specific unit test**            | `oan_grievance_service/<module>/doctype/<doctype_name>/`     |
| **Cross-cutting / API integration test**  | `oan_grievance_service/tests/`                               |
| **A background scheduled job**            | `oan_grievance_service/tasks.py` (registered in `hooks.py`)  |
| **A database migration patch**            | `oan_grievance_service/patches/` (appended to `patches.txt`) |
| **API contract & gateway updates**        | `openapi/` and `kong/`                                       |

---

## 7. Verification & Quality Commands

```bash
# Run unit & integration tests
bench --site <site> run-tests --app oan_grievance_service

# Run a specific test module
bench --site <site> run-tests --app oan_grievance_service --module oan_grievance_service.tests.test_router

# Code formatting & linting
ruff check oan_grievance_service/
ruff format oan_grievance_service/

# Semgrep architectural & security scan
semgrep scan --metrics=off --config /tmp/frappe-semgrep-rules/rules --config r/python.lang.correctness --config /tmp/oan_auth_service/.semgrep/rules.yml --error .

# Regenerate API contracts & Kong config
python openapi/generate_openapi_spec.py
python kong/generate_kong_config_from_spec.py
```
