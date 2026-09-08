# OAN Grievance Service

Grievance redressal backend, built as a Frappe app.

## Status

Scaffold only — module structure, hooks and packaging are in place; no doctypes
or endpoints yet.

## Relationship to oan_a2c

This app deploys separately from `oan_a2c`: its own site, its own database, its
own `User` table. The two share **code, not identity** — a token minted here is
not valid there, and each deployment owns its own users, roles and refresh
tokens.

The shared code lives in `oan_core`, a plain Python library (not a Frappe app)
consumed as a pinned dependency in `pyproject.toml`. It carries the API response
envelope, request-validation decorators and JWT key resolution. It is pinned by
tag rather than branch so each deployment adopts changes deliberately.

## Development

All commands run from `development/frappe-bench-16/`.

```bash
bench --site <site> install-app oan_grievance_service

bench run-tests --app oan_grievance_service

# After changing doctype JSON or patches.txt
bench migrate

# After changing hooks.py or fixtures
bench clear-cache
```

Lint from the app root:

```bash
ruff check oan_grievance_service/
ruff format oan_grievance_service/
```
