# Setup Guide

Local development environment for `oan_grievance_service`, the OpenAgriNet Ethiopia
Grievance Management module.

Frappe is not practical to run natively on Windows, so this project develops inside the
official `frappe_docker` dev container. Every `bench` command below runs **inside** the
container, never on the Windows host.

---

## 1. Tech stack

| Layer               | Component                                   | Version used                  |
| ------------------- | ------------------------------------------- | ----------------------------- |
| Container runtime   | Docker Desktop (WSL 2 backend)              | 29.7.2                        |
| Dev environment     | `frappe/frappe_docker` dev container        | `frappe/bench:latest`         |
| Bench CLI           | frappe-bench                                | 5.31.0                        |
| Framework           | Frappe                                      | 16.33.1 (`version-16`)        |
| Language            | Python                                      | 3.14.2 (app requires >= 3.14) |
| Front-end toolchain | Node                                        | v24.13.0                      |
| Database            | MariaDB                                     | 11.8.8                        |
| Cache / queue       | Redis                                       | 8.10.1 (two instances)        |
| Apps                | `oan_auth_service`, `oan_grievance_service` | 0.0.1                         |
| Modules             | 6, split by FSD area                        | 27 doctypes, 3 roles          |

The app itself ships no server, no ORM and no migration engine. Frappe supplies all
three. See `ARCHITECTURE` notes or the published architecture diagram for how the layers
fit together.

---

## 2. Prerequisites

Install these on the Windows host before starting.

- **Docker Desktop** with the WSL 2 backend enabled.
- **Git**. Commands below assume Git Bash.
- **VS Code** with the _Dev Containers_ extension. Optional, but it handles port
  forwarding for you.

You do **not** need Python, Node, MariaDB or Redis on the host. They all live in the
container. Do not run `bench` through `docker exec` from the host: open a shell in the
container (step 3.5) and run every `bench` command from there.

---

## 3. First-time setup

### 3.1 Start Docker Desktop

```bash
"/c/Program Files/Docker/Docker/Docker Desktop.exe" &
```

Wait until the engine answers. This should print server details, not a pipe error:

```bash
docker info
```

### 3.2 Get the dev container

Clone `frappe_docker` into a folder **next to** this repository, not inside it. A bench
carries a virtualenv, a framework clone and site data, none of which belongs in the app
repository.

```bash
cd /c/Users/shail
git clone --depth 1 https://github.com/frappe/frappe_docker.git oan_grievance_service_devcontainer
```

### 3.3 Create the `.devcontainer` folder

Upstream ships the dev container config as an example that you copy into place.

```bash
cd /c/Users/shail/oan_grievance_service_devcontainer
mkdir -p .devcontainer
cp devcontainer-example/devcontainer.json .devcontainer/
cp devcontainer-example/docker-compose.yml .devcontainer/
```

### 3.4 Expose the dev server ports

**Do this before starting the containers.** The upstream example defines no `ports` for
the `frappe` service, because it assumes VS Code will forward them. If you drive the
containers with plain `docker compose`, the site will be unreachable from the browser
until you add the mapping yourself.

Edit `.devcontainer/docker-compose.yml` and extend the `frappe` service:

```yaml
  frappe:
    image: docker.io/frappe/bench:latest
    command: sleep infinity
    environment:
      - SHELL=/bin/bash
      - FRAPPE_BIND_ADDR=0.0.0.0
    ports:
      - "127.0.0.1:8100-8105:8000-8005"
      - "127.0.0.1:9100-9105:9000-9005"
    volumes:
      - ..:/workspace:cached
    working_dir: /workspace/development
```

`FRAPPE_BIND_ADDR` matters: without it the dev server binds to loopback inside the
container and the published port answers nothing.

### 3.5 Start the containers

```bash
cd /c/Users/shail/oan_grievance_service_devcontainer/.devcontainer
docker compose -p oan_grievance_dev up -d
```

Four containers should come up:

```bash
docker ps --filter "name=oan_grievance_dev" --format "table {{.Names}}\t{{.Status}}"
```

| Container                         | Role                               |
| --------------------------------- | ---------------------------------- |
| `oan_grievance_dev-frappe-1`      | bench, where you run every command |
| `oan_grievance_dev-mariadb-1`     | database                           |
| `oan_grievance_dev-redis-cache-1` | metadata and session cache         |
| `oan_grievance_dev-redis-queue-1` | background jobs and scheduler      |

From here on, either open the folder in VS Code and _Reopen in Container_, or shell in:

```bash
docker exec -it oan_grievance_dev-frappe-1 bash
```

The rest of this section assumes you are **inside the container**.

### 3.6 Initialise the bench

This clones the framework, builds a virtualenv and compiles front-end assets. It takes
roughly ten to fifteen minutes on a first run. That is normal, not a hang.

```bash
cd /workspace/development
bench init frappe-bench --skip-redis-config-generation --frappe-branch version-16
```

### 3.7 Point bench at the compose services

`bench init` writes `redis://127.0.0.1:...` and no database host, which is wrong in a
multi-container setup. Nothing will connect until you fix this.

Replace `frappe-bench/sites/common_site_config.json` with:

```json
{
 "background_workers": 1,
 "db_host": "mariadb",
 "db_port": 3306,
 "file_watcher_port": 6787,
 "frappe_user": "frappe",
 "gunicorn_workers": 25,
 "live_reload": true,
 "rebase_on_pull": false,
 "redis_cache": "redis://redis-cache:6379",
 "redis_queue": "redis://redis-queue:6379",
 "redis_socketio": "redis://redis-queue:6379",
 "restart_supervisor_on_update": false,
 "restart_systemd_on_update": false,
 "serve_default_site": true,
 "shallow_clone": true,
 "socketio_port": 9000,
 "use_redis_auth": false,
 "webserver_port": 8000,
 "workers": {
  "grievance_routing": {"timeout": 300},
  "grievance_sla": {"timeout": 1800},
  "grievance_scan": {"timeout": 300},
  "grievance_notifications": {"timeout": 600}
 }
}
```

The `workers` block declares this app's queues (section 4.4). Without it every routing,
SLA, scan and notification job fails to enqueue.

Confirm the service names resolve:

```bash
getent hosts mariadb redis-cache redis-queue
```

### 3.8 Add the apps to the bench

`oan_auth_service` comes first. This app declares it in `required_apps` and imports its
API envelope, role decorator and JWT namespace registration, so every endpoint here
fails at import without it.

```bash
cd /workspace/development/frappe-bench
bench get-app https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service.git --branch develop
bench get-app https://github.com/Centre-for-Open-Societal-Systems/oan_grievance_service.git --branch develop
```

If you are working from a local checkout instead, see
[section 7](#7-repository-and-bench-relationship) first, because the repositories are not
mounted into the container by default.

### 3.9 Create a site

```bash
cd /workspace/development/frappe-bench
bench new-site grievance.localhost \
  --db-root-password 123 \
  --admin-password admin \
  --mariadb-user-host-login-scope='%'
```

The MariaDB root password `123` comes from the compose file. A site is a database, so
each site you create gets its own schema.

`MariaDB version 11.8 is more than 10.8 which is not yet tested` is an upstream
version check and is harmless.

`*** Scheduler is disabled ***` is **not** harmless for this app. It is the default
for a new site, and step 3.10 turns it back on. SLA reminders, escalation,
notification dispatch and attachment scanning are all scheduled jobs: with the
scheduler off the site accepts grievances and then does nothing with them.

### 3.10 Configure site, install the apps, and migrate

1. Turn on developer mode and tests:

```bash
cd /workspace/development/frappe-bench
bench --site grievance.localhost set-config developer_mode 1
bench --site grievance.localhost set-config allow_tests true --parse
```

2. Create the JWT signing key. `oan_auth_service` signs access tokens with RS256 and
   refuses every login until `jwt_private_keys` names a readable RSA key of at least
   2048 bits. A relative path resolves against the site folder.

```bash
mkdir -p sites/grievance.localhost/keys
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 \
  -out sites/grievance.localhost/keys/jwt_v1.pem
chmod 600 sites/grievance.localhost/keys/jwt_v1.pem

bench --site grievance.localhost set-config jwt_private_keys '{"v1": "keys/jwt_v1.pem"}' --parse
bench --site grievance.localhost set-config jwt_current_kid "v1"
```

The old shared-secret key `jwt_secrets` is no longer read. A site that still has only
that key cannot issue tokens.

3. Set the registration roles. Without `jwt_self_registerable_roles`, guest
   self-registration is refused.

```bash
bench --site grievance.localhost set-config jwt_default_registration_role "Grievance Submitter"
bench --site grievance.localhost set-config jwt_self_registerable_roles '["Grievance Submitter"]' --parse
```

Leave `jwt_enforce_https` unset locally, because the dev server serves plain HTTP. See
[section 4.3](#43-site-configuration-keys) for every other key, and
[`site_config.example.json`](site_config.example.json) for a full template.

4. Install `oan_auth_service` first, then `oan_grievance_service`, and run migrations:

```bash
bench --site grievance.localhost install-app oan_auth_service
bench --site grievance.localhost install-app oan_grievance_service
bench --site grievance.localhost migrate
bench --site grievance.localhost enable-scheduler
```

`migrate` runs the patches and then the seeders in `setup/install.py`. The seeders
create the roles, role levels, service categories, workflow and notifications, and
they are safe to rerun. They skip records that already exist, including categories,
submitter types and submission types that an admin renamed but whose `code` is
unchanged.

Developer mode is required. Without it, doctype changes are written to the database only
and never exported to JSON files in the app, so your work will not reach git.

### 3.11 Verify

```bash
bench --site grievance.localhost list-apps
```

Expected:

```
frappe                16.33.1  version-16
oan_auth_service      0.0.1    UNVERSIONED
oan_grievance_service 0.0.1    UNVERSIONED
```

---

## 4. External services and site configuration

Everything below is optional for a site that only needs to accept and route
grievances. Two features stay switched off until the service behind them exists,
and both fail closed rather than quietly degrading.

### 4.1 ClamAV — attachment scanning

Uploads are stored as `scan_status = Pending` and withheld from officers until a
scanner marks them `Clean`. With no scanner reachable they become `Failed`, never
`Clean`: an outage makes attachments unavailable, it does not make them trusted.

So **until ClamAV is running, no uploaded file can be downloaded by anyone.**

Add the daemon as a sidecar next to MariaDB and Redis:

```yaml
  clamav:
    image: clamav/clamav:stable
    ports:
      - "3310:3310"
```

Then point the site at it:

```bash
bench --site grievance.localhost set-config grievance_clamav_host clamav
bench --site grievance.localhost set-config -p grievance_clamav_port 3310
```

The container downloads its signature database on first start, which takes a few
minutes. Until it finishes, `clamd` refuses connections and scans report `Failed`.

No Python package is involved. The app talks to the daemon over a TCP socket using
the standard library, so nothing is added to `requirements.txt` for this.

**Frappe Cloud.** `[deploy.dependencies.apt]` in `pyproject.toml` is left empty on
purpose. Listing `clamav-daemon` there would only install the binaries: nothing
starts `clamd`, and the Ubuntu default listens on a unix socket while this app
connects over TCP. The daemon also holds its signature database in memory
(roughly 1-2 GB), which would compete with the bench workers. Run ClamAV as a
separate service and set `grievance_clamav_host` and `grievance_clamav_port` to
it. If you do run `clamd` on the bench itself, add the package, set `TCPSocket 3310`
and `TCPAddr 127.0.0.1` in `clamd.conf`, and start it under supervisor.

### 4.2 SMS gateway

Acknowledgement SMS is code complete and dispatches through core's
`SMS Settings` doctype. Configure a gateway there — **Setup → SMS Settings** — and
the notification queue starts sending. Until then SMS rows queue and fail; email
is unaffected.

### 4.3 Site configuration keys

Set each key with `bench --site <site> set-config <key> <value>`. Add `--parse` for
numbers, booleans, lists and objects.

**Required by `oan_auth_service`**

| Key                             | Example                     | Effect                                                                           |
| ------------------------------- | --------------------------- | -------------------------------------------------------------------------------- |
| `jwt_private_keys`              | `{"v1": "keys/jwt_v1.pem"}` | kid to RSA PEM path (relative to the site folder) or PEM text. No key, no tokens |
| `jwt_current_kid`               | `v1`                        | Key that signs new tokens. Must exist in `jwt_private_keys`                      |
| `jwt_self_registerable_roles`   | `["Grievance Submitter"]`   | Roles a guest may register with. Empty refuses self-registration                 |
| `jwt_default_registration_role` | `Grievance Submitter`       | Role given when an admin creates a user without naming roles                     |

**Optional, `oan_auth_service`**

| Key                                 | Default   | Effect                                                    |
| ----------------------------------- | --------- | --------------------------------------------------------- |
| `jwt_issuer`                        | site name | `iss` claim. Keep distinct per environment                |
| `jwt_access_token_ttl`              | `900`     | Access token lifetime in seconds                          |
| `jwt_refresh_token_ttl`             | `2592000` | Refresh token lifetime in seconds                         |
| `jwt_refresh_token_ttl_remember_me` | `7776000` | Refresh token lifetime with "remember me"                 |
| `jwt_enforce_https`                 | `false`   | Refuse auth calls over plain HTTP. Set `true` outside dev |
| `password_reset_otp_ttl`            | `600`     | Password reset code lifetime in seconds                   |
| `password_reset_otp_length`         | `6`       | Digits in the password reset code                         |
| `password_reset_otp_max_attempts`   | `5`       | Wrong guesses before the code is burned                   |
| `iam_base_url`                      | unset     | OpenG2P IAM base URL. Unset disables delegated login      |
| `iam_application`                   | unset     | IAM client whose roles are granted. Required with IAM     |
| `iam_auth_cookie_prefix`            | empty     | Prefix on IAM auth cookie names                           |
| `iam_allow_insecure_http`           | `false`   | Allow an `http://` `iam_base_url`. Local testing only     |

**Optional, `oan_grievance_service`**

| Key                                 | Default | Effect                                                      |
| ----------------------------------- | ------- | ----------------------------------------------------------- |
| `grievance_clamav_host`             | unset   | ClamAV daemon host. **Unset disables scanning.**            |
| `grievance_clamav_port`             | `3310`  | ClamAV daemon port                                          |
| `grievance_auto_escalation_enabled` | `true`  | Set `false` to stop automatic escalation site-wide          |
| `grievance_change_request_hours`    | `24`    | Hours before an unanswered change request moves one rung up |

The maximum deferral is no longer a site config key. Set it on the **Grievance Deferral
Policy** doctype. `grievance_max_deferral_days` is ignored.

### 4.4 Scheduled jobs

Wired in `hooks.py`, all defined in `tasks.py`. They require
`bench --site <site> enable-scheduler`.

| Frequency  | Job                             | Does                                                |
| ---------- | ------------------------------- | --------------------------------------------------- |
| Hourly     | `enqueue_sla_sweeps`            | Queues the four SLA jobs below on `grievance_sla`   |
| (SLA)      | `send_sla_reminders`            | Officer reminders at 50% and 80% of the SLA window  |
| (SLA)      | `escalate_breached`             | Moves overdue cases one rung up the chain           |
| (SLA)      | `expire_state_timers`           | Acts on cases that stayed in a state past its timer |
| (SLA)      | `forward_stale_change_requests` | Moves unanswered change requests one rung up        |
| Hourly     | `enqueue_notification_sweep`    | Queues `dispatch_notifications`                     |
| Hourly     | `enqueue_scan_sweep`            | Queues `scan_pending_attachments` (ClamAV)          |
| Hourly     | `enqueue_routing_sweep`         | Queues `drain_routing_queue` for unrouted cases     |
| Every 15 m | `refresh_dashboard_rollup`      | Updates dashboard rollups for the last few days     |
| Daily long | `rebuild_dashboard_rollup`      | Rebuilds every day of the dashboard rollup          |

To run one job now without waiting for the scheduler:

```bash
bench --site grievance.localhost execute oan_grievance_service.tasks.escalate_breached
```

**Background queues.** Routing, SLA checks, ClamAV scans and notification dispatch each run on their own RQ
queue, so a scan backlog or a long SLA sweep does not delay the others:

| Queue                     | Jobs                                                                                              | Timeout |
| ------------------------- | ------------------------------------------------------------------------------------------------- | ------- |
| `grievance_routing`       | `route_grievance_job` on submit, hourly `drain_routing_queue`                                     | 300 s   |
| `grievance_sla`           | `send_sla_reminders`, `escalate_breached`, `expire_state_timers`, `forward_stale_change_requests` | 1800 s  |
| `grievance_scan`          | `scan_attachment` on upload, hourly `scan_pending_attachments`                                    | 300 s   |
| `grievance_notifications` | hourly `dispatch_notifications`                                                                   | 600 s   |

Each queue must appear under `workers` in `common_site_config.json` (section 3.7).
`bench worker` with no `--queue` listens on every declared queue, which is enough in
development. In production run one worker per queue, for example
`bench worker --queue grievance_scan`, and run `bench setup procfile` (or
`bench setup supervisor`) after editing `workers`.

### 4.5 Realtime / Socket.IO

The backend emits socket.io events (e.g. `attachment_scanned`) on port `9000`.
To connect a local frontend (e.g., `oan_grievance_ui`), you cannot connect a browser
directly to port `9000` because WebSockets cannot set the `Authorization: Bearer`
header required by the backend. Instead, run the realtime gateway provided in the frontend
repo (which translates the cookie into a Bearer token) and point the frontend at the
gateway on port `3001`. The gateway will proxy connections to `ws://localhost:9000`.

---

## 5. Running the dev server

```bash
cd /workspace/development/frappe-bench
bench start
```

With the port mapping from step 3.4, the site is then at
**http://grievance.localhost:8100**, logging in as `Administrator` / `admin`.

`grievance.localhost` resolves to `127.0.0.1` automatically in modern browsers. If yours
does not, add it to `C:\Windows\System32\drivers\etc\hosts`.

---

## 6. Everyday commands

Run all of these from `/workspace/development/frappe-bench` inside the container.

| Task                 | Command                                                                  |
| -------------------- | ------------------------------------------------------------------------ |
| Start the dev server | `bench start`                                                            |
| Apply schema changes | `bench --site grievance.localhost migrate`                               |
| Open a Python shell  | `bench --site grievance.localhost console`                               |
| Open a SQL shell     | `bench --site grievance.localhost mariadb`                               |
| Run the app's tests  | `bench --site grievance.localhost run-tests --app oan_grievance_service` |
| Rebuild assets       | `bench build --app oan_grievance_service`                                |
| Clear caches         | `bench --site grievance.localhost clear-cache`                           |
| Tail logs            | `bench --site grievance.localhost show-config` then check `logs/`        |

### Creating a doctype

Either create it in the browser under **Doctype**, which writes the JSON into the app
automatically while developer mode is on, or write the JSON by hand and run `migrate`.
Prefer the browser: it produces valid metadata and correct field ordering.

Doctypes belong to the **Grievance Management** module so they land in
`oan_grievance_service/grievance_management/doctype/`.

---

## 7. Repository and bench relationship

There are two copies of the app on the host, and they are **not linked**:

```
C:\Users\shail\oan_grievance_service\                  <- git repo, source of truth
C:\Users\shail\oan_grievance_service_devcontainer\     <- mounted as /workspace
    development/frappe-bench/apps/oan_grievance_service/   <- what bench runs
```

The compose file mounts the _devcontainer_ folder, not the repository, so changes made
by bench land in the bench copy and must be copied back to the repository by hand.

### Recommended: mount the repository instead

Add a second bind mount to the `frappe` service in
`.devcontainer/docker-compose.yml`:

```yaml
    volumes:
      - ..:/workspace:cached
      - ../../oan_grievance_service:/workspace/repo:cached
```

Recreate the containers, then replace the bench copy with a link to the repository:

```bash
docker compose -p oan_grievance_dev up -d --force-recreate
docker exec -it oan_grievance_dev-frappe-1 bash
cd /workspace/development/frappe-bench/apps
rm -rf oan_grievance_service && ln -s /workspace/repo oan_grievance_service
```

After this the repository is the only copy, edits in VS Code are live in the container,
and the manual copy step disappears. This mirrors how the sibling `oan_a2c` project is
set up.

---

## 8. What the app contains

Twenty-five doctypes across six modules, split along the FSD's own functional
decomposition rather than one flat module. Moving a doctype between modules after
deployment means a patch on every site, so the split is worth getting right early.

| Module                   | Doctypes                                                                                                                                                                                                | FSD area                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Grievance Management     | Grievance, Grievance Response, Grievance Timeline, Grievance Status History, Grievance Duplicate, Grievance Attachment, Grievance Submitter Profile                                                     | FR-02/04/05/06 — the case and its records                         |
| Grievance Requests       | Grievance Change Request, Grievance Change Request Item, Grievance Change Request Approval                                                                                                              | 3.3.1, 3.11.7, 9.2 — changes to a case, approved up the hierarchy |
| Grievance Masters        | Grievance Service Category, Grievance Type, Grievance Administrative Area, Grievance Department, Grievance Submitter Type, Grievance Submission Type, Grievance Role Level, Grievance Response Template | 3.2.2, 3.11.8, Appendix A — reference data                        |
| Grievance SLA            | Grievance SLA Configuration, Grievance Deferral Policy                                                                                                                                                  | FR-07, 3.11.7 — windows and escalation rules                      |
| Grievance Notification   | Grievance Notification Log                                                                                                                                                                              | FR-08, Appendix C — delivery log                                  |
| Grievance Access Control | Grievance RBAC Assignment, Grievance RBAC Assignment Officer, Grievance Access Audit Event                                                                                                              | FR-01, 3.1.1, FR-10 — scope and audit                             |

`grievance_management/` also holds the FR-09 SLA Compliance report, three FR-11.2
dashboard charts and the FR-11.1 workspace, since those are cross-module views.

Seven roles are created on install and on every migrate:

| Role                           | Purpose                                                                                       |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| `Grievance Submitter`          | Files and tracks own grievances                                                               |
| `Grievance Assisted Submitter` | Files on behalf of citizens (development agent, call centre). Granted through Keycloak        |
| `Grievance Officer`            | Works cases inside the desks its RBAC Assignment rows cover                                   |
| `Grievance Admin`              | Platform administration                                                                       |
| `Grievance Desk Head`          | Manages officers and grievance types on its own desks. Granted with the top rung, not by hand |
| `Grievance Review Officer`     | Read-only oversight. Refused on every mutating endpoint                                       |
| `Grievance Dashboard Reader`   | Machine user for the OAN dashboards. Reads the public charts only                             |

A role carries capability only — what actions exist for you. Which cases you may touch
comes from Grievance RBAC Assignment scope, and seniority comes from the role level
(`nodal_officer` L1, `senior_nodal_officer` L2, `department_head` L3, shown as
"Category Admin"). Submitter covers every filing actor, distinguished by
`submitter_type` rather than by role.

`Grievance Desk Head` needs no manual setup. The officer API grants and revokes it
with the L3 level, and the `grant_desk_head_role` patch gives it to existing L3 officers
during `migrate`. See [`docs/desk-scoping-and-authority.md`](docs/desk-scoping-and-authority.md).

### Application layers

| Path               | Holds                                                                                                             |
| ------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `api/v1/`          | The versioned public contract. Breaking changes ship as `api/v2/` alongside; see `api/__init__.py` for the policy |
| `services/`        | Domain logic: routing, SLA, lifecycle, notifications, audit                                                       |
| `permissions.py`   | FR-01 deny-by-default RBAC query conditions                                                                       |
| `tasks.py`         | FR-07 scheduled jobs, wired in `hooks.py`                                                                         |
| `setup/install.py` | Seed data: roles, categories, the Appendix C events                                                               |

### Known incomplete work

Not built yet: the portal pages (`www/` and `templates/pages/` are empty — FR-11.5
submit wizard), Amharic translations (3.11.8), and Fayda ID authentication (FR-01),
which is expected from `oan_auth_service`. This app covers authorization, not
authentication.

Code complete but waiting on infrastructure — neither is a code change:

- **Attachment scanning.** The pipeline runs, but no ClamAV daemon is provisioned
  in any environment, so every upload settles at `Failed` and nothing is served.
  See section 4.1.
- **SMS acknowledgement.** Dispatch is wired to core's `SMS Settings`, which has no
  gateway configured. See section 4.2.

### Two specification conflicts, resolved

They are recorded here because the FSD contradicts itself and the call was made during
the build.

- **SLA clock start.** FSD 4.2 starts the timer at assignment; UC-04 computes the breach
  from creation date. These diverge for any grievance waiting in the manual routing
  queue. Implemented per 4.2, since 1.3 defines the SLA as the time an _assigned_ agency
  must act. The old `grievance_sla_clock_start` site config key is no longer read; see
  `services/sla.py` for the current rule.
- **Reassignment SLA.** FSD 3.3.1 requires a configured policy and says it "shall not be
  implicit"; Appendix D-2 hardcodes a reset. Implemented as the explicit `sla_treatment`
  field on the reassignment request; unset means the clock continues.

The ticket-number segment codes are a documented assumption. The specification's own
example code `AGRN` matches none of its five service categories, so the codes seeded on
the Administrative Area and Service Category masters need confirming against the OAN registry
before go-live.

---

## 9. Troubleshooting

**`failed to connect to the docker API at npipe:...`**
Docker Desktop is not running. Start it and wait for the engine.

**`No jwt_private_keys in site_config.json`**
The site has no RSA signing key, or still uses the retired `jwt_secrets`. Redo step 3.10.

**`DuplicateEntryError` on `Grievance Service Category` during `migrate`**
The app version predates the seeder fix that matches seeded categories by `code`.
Update the app, then run `migrate` again.

**`Cannot connect to redis_cache to update assets_json`**
`common_site_config.json` still points at `127.0.0.1`. Redo step 3.7.

**`bench init` looks frozen**
It is not. It clones the framework, installs Python dependencies, then runs `yarn install` and builds assets. Ten to fifteen minutes is normal on a first run.

**Site loads nothing on port 8100**
Either the `ports` block is missing from the compose file, or `FRAPPE_BIND_ADDR` is
unset so the server bound to loopback inside the container. See step 3.4.

**Doctype changes do not appear as files in git**
Developer mode is off. Run `bench set-config -g developer_mode 1`, then re-save the
doctype to force the export.

**`--no-mariadb-socket is DEPRECATED`**
Use `--mariadb-user-host-login-scope='%'` instead, as shown in step 3.9.

---

## 10. Resetting

Drop the site and start over, keeping the bench:

```bash
cd /workspace/development/frappe-bench
bench drop-site grievance.localhost --db-root-password 123 --force
```

Tear the whole environment down, including the database volume:

```bash
cd /c/Users/shail/oan_grievance_service_devcontainer/.devcontainer
docker compose -p oan_grievance_dev down -v
```

`down -v` deletes the MariaDB volume and every site in it. The bench itself lives in the
devcontainer folder on the host and survives.
