# OAN Grievance Service

Grievance redressal backend for OpenAgriNet Ethiopia, built as a Frappe app.
Implements the Grievance Management FSD v1.3 D3: multi-channel intake, routing,
SLA tracking, escalation and notifications.

## Status

Under active development. 26 doctypes across six modules, a versioned REST API
under `api/v1/`, deny-by-default RBAC, and scheduled SLA/escalation jobs.

Not yet wired: the SMS gateway (code complete, credentials pending), the ClamAV
scanner (code complete, service not provisioned), Amharic translations, and the
portal pages under `www/`.

## Requirements

| | |
| --- | --- |
| Python | 3.14 or newer |
| Frappe | `version-16` |
| MariaDB | 11.x |
| Redis | two instances — cache and queue |

### Required app

This app does **not** run standalone. It declares
[`oan_auth_service`](https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service)
in `required_apps` and imports its API envelope, role decorator and JWT
namespace registration. Install it into the same bench **before** this app, or
every endpoint fails at import.

### Python dependencies

Declared in `pyproject.toml`, mirrored in `requirements.txt`:

| Package | Why |
| --- | --- |
| `filetype` | Magic-byte type sniffing on attachment upload |
| `Pillow` | EXIF/GPS stripping before an image is stored |
| `pypdf` (dev) | Builds a valid PDF fixture for the upload tests |

`PyJWT` and `pydantic` are **not** listed here. They belong to
`oan_auth_service`, which this app pulls in through `required_apps`.

### External services

| Service | Needed for | Status |
| --- | --- | --- |
| ClamAV (`clamd`) | Attachment malware scanning | **Not provisioned** |
| SMS gateway | Acknowledgement SMS | **Not configured** |

Without ClamAV every upload stays at `scan_status = Failed` and no attachment is
ever served — the scanner fails closed by design. See [SETUP.md](SETUP.md)
section 4.

## Install

```bash
bench get-app https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service.git
bench get-app https://github.com/Centre-for-Open-Societal-Systems/oan_grievance_service.git

bench --site <site> install-app oan_auth_service
bench --site <site> install-app oan_grievance_service
bench --site <site> migrate
bench --site <site> enable-scheduler
```

`enable-scheduler` is not optional. SLA reminders, escalation, notification
dispatch and attachment scanning are all scheduled jobs; a site with the
scheduler off accepts grievances and then does nothing with them.

## Site configuration

Every key is optional and falls back to a documented default. Set them in the
site's `site_config.json`.

| Key | Default | Effect |
| --- | --- | --- |
| `grievance_clamav_host` | unset | ClamAV daemon host. **Unset means no scanning.** |
| `grievance_clamav_port` | `3310` | ClamAV daemon port |
| `grievance_sla_clock_start` | `assignment` | Start the SLA clock at `assignment` or `creation` |
| `grievance_sla_paused_statuses` | `More Info Needed`, `Pending Submitter` | Statuses that pause the SLA clock |
| `grievance_confirmation_window_days` | `7` | Days a submitter has to confirm a resolution |
| `grievance_auto_escalation_enabled` | `true` | Set `false` to stop automatic escalation site-wide |

## Setup & Configuration Guides

- **[SETUP_DOCKER.md](SETUP_DOCKER.md)** — Production setup using **`frappe_docker`** with multi-container Docker Compose, image builds with custom apps, and containerized `site_config.json` setup.
- **[SETUP.md](SETUP.md)** — Development setup using the official `frappe_docker` dev container (Windows/WSL 2).

## Development

```bash
bench --site <site> run-tests --app oan_grievance_service

# after changing doctype JSON or patches.txt
bench --site <site> migrate

# after changing hooks.py or fixtures
bench --site <site> clear-cache
```

Lint from the app root:

```bash
ruff check oan_grievance_service/
ruff format oan_grievance_service/
```

Local environment setup, including the dev container, is in [SETUP.md](SETUP.md).

## Layout

| Path | Holds |
| --- | --- |
| `api/v1/` | The versioned public contract; see `api/__init__.py` for the versioning policy |
| `services/` | Domain logic — routing, SLA, lifecycle, notifications, audit, scanning |
| `permissions.py` | Deny-by-default RBAC query conditions (FR-01) |
| `tasks.py` | Scheduled jobs, wired in `hooks.py` |
| `setup/install.py` | Seed data — roles, masters, the Appendix C notification matrix |
