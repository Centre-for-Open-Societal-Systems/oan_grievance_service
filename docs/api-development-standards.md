# API Development Standards & Best Practices

This document defines the architectural conventions, decorator pipeline, request validation rules, error handling, versioning policy, and test requirements for developing REST API endpoints.

---

## 1. Core Architecture & Design Principles

1. **Thin API Layer, Rich Service Layer:** API handlers act solely as HTTP gateways. They perform authentication, request validation, invoke domain service methods, and format the response envelope. **Do not embed direct database queries or core business logic inside API handlers.**
2. **Immutable Versioning:** All public endpoints live under a versioned package (`api/v1/`, `api/v2/`).
   - Additive, backward-compatible fields remain in `v1`.
   - Breaking changes (field renaming, type changes, narrowing constraints) require opening a new version package (`v2/`).
   - Never modify or break an existing released version.
3. **Consistent Response Envelopes:** Every endpoint returns standard responses via `success_response()`, with version metadata (`meta`) and request IDs automatically attached by `@handle_api_errors`.
4. **Auditable & Non-Bypassing:** All operations that mutate DocType state must route through standard Frappe document methods or service hooks so workflow guards, audit trails, and status histories are preserved.

---

## 2. Directory Structure & URL Mapping

Endpoints support two transports:

1. **REST Transport (Preferred):** Clean HTTP paths mounted into Frappe's URL Map via `@prefixed(...)` / `@route(...)`.
2. **RPC Transport (Backward Compatibility):** Frappe's native method dispatch convention (`/api/method/...`).

```
<app_name>/api/
├── __init__.py                # Version registry, metadata helpers & router re-exports
├── router.py                  # REST route loader & URL map registration
├── middleware.py              # JWT validation & RPC path exemptions
└── v1/                        # Version 1 API package
    ├── __init__.py            # Module index & endpoint catalog
    ├── hello.py               # Example resource controller
    └── ...
```

### REST Resource Naming Standards

All REST endpoints follow standard RESTful conventions:

1. **Plural Resource Nouns:** Top-level and nested collections must use plural nouns (`/api/v1/greetings`, `/api/v1/users`, `/api/v1/items`).
2. **Kebab-Case URL Segments:** Compound resource names must use lowercase kebab-case (`/user-profiles`, `/custom-records`).
3. **Decoupled from DocType Names:** Do not create duplicate URL aliases to mirror internal Frappe DocType conventions (e.g. avoid duplicate singular or snake_case routes).
4. **Clean Root Collection Paths:** Use collection roots directly with query parameters (`GET /api/v1/greetings?status=active`) rather than nested RPC verb suffixes like `/get_greetings`.
5. **State Transition Action Verbs:** For non-CRUD lifecycle actions, use clear POST action sub-paths on item resources (`/api/v1/greetings/<greeting_id>/publish`, `/archive`).

**Example URL Mapping:**

| Endpoint Purpose    | REST Route (Standard)                          | RPC Route (Legacy)                                           | Method |
| ------------------- | ---------------------------------------------- | ------------------------------------------------------------ | ------ |
| Health Check        | `GET /api/v1/health`                           | `GET /api/method/<app_name>.api.router.get_health`           | GET    |
| Ping                | `GET /api/v1/ping`                             | `GET /api/method/<app_name>.api.router.get_ping`             | GET    |
| List Greetings      | `GET /api/v1/greetings`                        | `GET /api/method/<app_name>.api.v1.hello.list_greetings`     | GET    |
| Get Greeting Detail | `GET /api/v1/greetings/<greeting_id>`          | `GET /api/method/<app_name>.api.v1.hello.get_greeting`       | GET    |
| Create / Say Hello  | `POST /api/v1/greetings`                       | `POST /api/method/<app_name>.api.v1.hello.say_hello`         | POST   |
| Publish Greeting    | `POST /api/v1/greetings/<greeting_id>/publish` | `POST /api/method/<app_name>.api.v1.hello.publish_greeting`  | POST   |
| Delete Greeting     | `DELETE /api/v1/greetings/<greeting_id>`       | `DELETE /api/method/<app_name>.api.v1.hello.delete_greeting` | DELETE |

---

## 3. Decorator Pipeline & Execution Order

Every API endpoint must apply decorators in the exact order shown below:

```python
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)

route = prefixed("/api/v1/greetings")

ALLOWED_ROLES = ["System Manager", "Administrator", "Custom Role"]

@route("", methods=("POST",), summary="Create a new greeting")
@frappe.whitelist()                               # 1. Exposes method via HTTP RPC
@handle_api_errors                                # 2. Catches exceptions and formats error JSON
@require_role(ALLOWED_ROLES)                      # 3. Enforces RBAC permissions
@validate_request(HelloRequest)                   # 4. Validates payload schema via Pydantic
def say_hello(**kwargs):
	...
```

### Decorator Responsibilities

| Decorator                    | Source                        | Purpose                                                                                                                                                                                                  |
| ---------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `@route(...)` / `@rest(...)` | `oan_auth_service.api.router` | Exposes clean RESTful URL endpoint on Frappe's `API_URL_MAP` and registers guest exemptions.                                                                                                             |
| `@frappe.whitelist()`        | `frappe`                      | Whitelists the Python function for HTTP invocation. Use `allow_guest=True` only for public, unauthenticated routes.                                                                                      |
| `@handle_api_errors`         | `oan_auth_service.api.utils`  | Intercepts `frappe.ValidationError`, `frappe.PermissionError`, etc., and returns standard JSON error responses with appropriate HTTP status codes. Dynamically resolves service-specific `version_meta`. |
| `@require_role(...)`         | `oan_auth_service.api.utils`  | Blocks requests if the authenticated user lacks one of the specified roles.                                                                                                                              |
| `@validate_request(Model)`   | `oan_auth_service.api.utils`  | Validates input against a Pydantic schema before executing the handler.                                                                                                                                  |

---

## 4. Request Validation with Pydantic

All `POST` / mutation endpoints must define an explicit `pydantic.BaseModel` schema.

```python
from pydantic import BaseModel, Field
from oan_auth_service.api.utils import RequiredPhone, SafeEmail

class HelloRequest(BaseModel):
	model_config = {"extra": "forbid"}

	recipient_name: str = Field(..., min_length=1, max_length=100, description="Name of the person to greet")
	message: str = Field(..., min_length=5, max_length=500, description="Greeting message body")
	contact_email: SafeEmail | None = Field(None, description="Optional contact email")
	contact_phone: RequiredPhone | None = Field(None, description="Optional validated E.164 phone number")
	is_public: bool = Field(False, description="Whether greeting is visible publicly")
```

### Guidelines for Schemas

- Use `RequiredPhone` and `SafeEmail` utility types from `oan_auth_service.api.utils`.
- Enforce sensible length and boundary constraints using `Field(..., min_length=...)` or `ge`/`le`.
- Set `model_config = {"extra": "forbid"}` for strict parameter policing, or `{"extra": "allow"}` if forward compatibility with arbitrary client parameters is required.

---

## 5. Response Format & Standard Envelopes

All successful responses **MUST** use the `success_response()` helper from `oan_auth_service.api.utils`. `@handle_api_errors` automatically resolves and attaches the `meta` block directly from the module's `version_meta`.

```python
from oan_auth_service.api.utils import handle_api_errors, success_response

@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
@handle_api_errors
def get_options():
	data = {
		"languages": ["en", "am", "or"],
		"themes": ["standard", "festive", "formal"],
	}
	return success_response(
		data=data,
		message=_("Options retrieved successfully"),
	)
```

### Standard Success Response Payload

```json
{
  "status": "success",
  "message": "Greeting created successfully",
  "data": {
    "greeting_id": "GRT-00001",
    "recipient_name": "Alice",
    "message": "Hello, welcome to our platform!",
    "created_at": "2026-09-24T09:00:00Z"
  },
  "meta": {
    "api_version": "v1",
    "status": "current"
  },
  "request_id": "8fa1e19d-b4ef-4bbd-9866-9dc7bc5fec1b"
}
```

---

## 6. Authentication & Public Route Exemption

1. **Authenticated by Default:** Requests hitting `/api/method/<app_name>.*` or `/api/v1/*` are validated via JWT tokens handled by `oan_auth_service`.
2. **Public Routes (Unauthenticated):** If an endpoint must be accessible without authentication (e.g., health check, public lookup options):
   - Add `@frappe.whitelist(allow_guest=True)` with the `# nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method` annotation.
   - Ensure all parameters on whitelisted functions have **explicit type hints** (e.g., `limit: int = 100`).
   - **Explicitly register the endpoint path** in `<app_name>/api/middleware.py`:

```python
EXEMPT_PATHS: list[str] = [
	"/api/method/<app_name>.api.v1.hello.get_options",
	"/api/method/<app_name>.api.router.get_health",
]
```

---

## 7. Error Handling & Validation Failures

Always raise standard Frappe exceptions with clear, localized messages and titles. `@handle_api_errors` automatically catches these and converts them to standardized error envelopes.

```python
# Validation / Bad Input -> Returns HTTP 400
if len(kwargs.get("message", "")) < 5:
	frappe.throw(
		_("The greeting message must be at least 5 characters."),
		exc=frappe.ValidationError,
		title=_("Invalid Message"),
	)

# Record Not Found -> Returns HTTP 404
if not frappe.db.exists("Greeting Record", greeting_id):
	frappe.throw(
		_("Greeting #{0} was not found.").format(greeting_id),
		exc=frappe.DoesNotExistError,
		title=_("Not Found"),
	)

# Permission / Authorization Error -> Returns HTTP 403
if not user_can_edit_greeting(user, greeting_id):
	frappe.throw(
		_("You do not have permission to modify this greeting."),
		exc=frappe.PermissionError,
		title=_("Forbidden"),
	)
```

---

## 8. Complete Boilerplate Template for a New API

Here is a full, generic template to use when creating a new API file:

```python
# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Hello / Greetings resource endpoints demonstrating REST standards."""

import frappe
from frappe import _
from pydantic import BaseModel, Field

from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import (
	SafeEmail,
	handle_api_errors,
	require_role,
	success_response,
	validate_request,
)

route = prefixed("/api/v1/greetings")

ALLOWED_ROLES = [
	"System Manager",
	"Administrator",
	"Greeting User",
]


class CreateGreetingRequest(BaseModel):
	model_config = {"extra": "forbid"}

	recipient_name: str = Field(..., min_length=1, max_length=100, description="Name of recipient")
	message: str = Field(..., min_length=5, max_length=500, description="Greeting content")
	contact_email: SafeEmail | None = Field(None, description="Recipient contact email")


@route("", methods=("POST",), summary="Create a new greeting")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_ROLES)
@validate_request(CreateGreetingRequest)
def create_greeting(recipient_name: str, message: str, contact_email: str | None = None, **kwargs):
	"""Create a greeting record and return the standard response."""
	doc = frappe.get_doc(
		{
			"doctype": "Greeting Record",
			"recipient_name": recipient_name,
			"message": message,
			"contact_email": contact_email,
			"sender": frappe.session.user,
		}
	).insert(ignore_permissions=True)

	return success_response(
		data={
			"greeting_id": doc.name,
			"recipient_name": doc.recipient_name,
			"message": doc.message,
			"created_at": doc.creation.isoformat() if hasattr(doc.creation, "isoformat") else str(doc.creation),
		},
		message=_("Greeting created successfully"),
	)


@route("/<greeting_id>", methods=("GET",), summary="Get a greeting by ID")
@frappe.whitelist()
@handle_api_errors
@require_role(ALLOWED_ROLES)
def get_greeting(greeting_id: str):
	"""Fetch details of a single greeting."""
	doc = frappe.db.get_value(
		"Greeting Record",
		greeting_id,
		["name", "recipient_name", "message", "creation"],
		as_dict=True,
	)
	if not doc:
		frappe.throw(
			_("Greeting #{0} not found.").format(greeting_id),
			exc=frappe.DoesNotExistError,
			title=_("Not Found"),
		)

	return success_response(
		data={
			"greeting_id": doc.name,
			"recipient_name": doc.recipient_name,
			"message": doc.message,
			"created_at": doc.creation.isoformat() if hasattr(doc.creation, "isoformat") else str(doc.creation),
		},
		message=_("Greeting retrieved successfully"),
	)
```

---

## 9. Automated Testing for APIs

Every API endpoint must have automated unit/integration tests validating:

1. **Happy Path:** Correct parameters return status 200 with matching `meta` and `data` structures.
2. **Invalid Input:** Missing mandatory fields or malformed data trigger 400 validation errors.
3. **Role Guards:** Requests from unauthorized users fail with 403 permission errors.
4. **Public vs. Protected Checks:** Guest access is permitted on exempt paths and blocked on protected paths.

### Example API Test Case

```python
import json
import unittest
import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request


def make_test_request(path: str, method: str = "GET", data: dict | None = None) -> Request:
	builder_kwargs = {
		"path": path,
		"method": method.upper(),
		"base_url": "http://testsite.localhost",
	}
	if data is not None:
		builder_kwargs["json"] = data

	req = Request(EnvironBuilder(**builder_kwargs).get_environ())
	frappe.local.request = req
	frappe.local.form_dict = frappe._dict(data or {})
	return req


class TestGreetingEndpoints(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_create_and_fetch_greeting_flow(self):
		import frappe.api

		# 1. Create Greeting
		payload = {"recipient_name": "Alice", "message": "Hello world from unit test!"}
		req = make_test_request("/api/v1/greetings", method="POST", data=payload)
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)

		res_json = json.loads(res.get_data(as_text=True))
		self.assertEqual(res_json["status"], "success")
		greeting_id = res_json["data"]["greeting_id"]
		self.assertTrue(bool(greeting_id))

		# 2. Retrieve Greeting
		req_get = make_test_request(f"/api/v1/greetings/{greeting_id}", method="GET")
		res_get = frappe.api.handle(req_get)
		self.assertEqual(res_get.status_code, 200)

		get_json = json.loads(res_get.get_data(as_text=True))
		self.assertEqual(get_json["data"]["recipient_name"], "Alice")
```

---

## 10. Postman Collection Synchronization

When introducing a new API or modifying parameters:

1. Add the request definition under the appropriate folder in the Postman collection.
2. Configure:
   - Method (`POST` / `GET` / `DELETE` / `PUT`).
   - URL: `{{base_url}}/api/v1/<endpoint>`.
   - Headers: `Authorization: Bearer {{auth_token}}`, `Content-Type: application/json`.
   - Sample request payload and example response body.
