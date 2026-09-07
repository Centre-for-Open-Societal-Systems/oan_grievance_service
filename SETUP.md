# Setup Guide

Local development environment for `oan_grievance_service`, the OpenAgriNet Ethiopia
Grievance Management module.

Frappe is not practical to run natively on Windows, so this project develops inside the
official `frappe_docker` dev container. Every `bench` command below runs **inside** the
container, never on the Windows host.

---

## 1. Tech stack

| Layer | Component | Version used |
| --- | --- | --- |
| Container runtime | Docker Desktop (WSL 2 backend) | 29.7.2 |
| Dev environment | `frappe/frappe_docker` dev container | `frappe/bench:latest` |
| Bench CLI | frappe-bench | 5.31.0 |
| Framework | Frappe | 15.120.0 (`version-15`) |
| Language | Python | 3.14.2 (app requires >= 3.10) |
| Front-end toolchain | Node | 24.13.0 |
| Database | MariaDB | 11.8 |
| Cache / queue | Redis | alpine (two instances) |
| App | `oan_grievance_service` | 0.0.1 |
| Module | Grievance Management | 12 doctypes, 6 roles |

The app itself ships no server, no ORM and no migration engine. Frappe supplies all
three. See `ARCHITECTURE` notes or the published architecture diagram for how the layers
fit together.

---

## 2. Prerequisites

Install these on the Windows host before starting.

- **Docker Desktop** with the WSL 2 backend enabled.
- **Git**. Commands below assume Git Bash.
- **VS Code** with the *Dev Containers* extension. Optional, but it handles port
  forwarding for you.

You do **not** need Python, Node, MariaDB or Redis on the host. They all live in the
container.

### Git Bash path translation

Git Bash rewrites arguments that look like Unix paths, which breaks `docker exec -w`.
Prefix such commands with `MSYS_NO_PATHCONV=1`:

```bash
MSYS_NO_PATHCONV=1 docker exec -w /workspace/development/frappe-bench \
  oan_grievance_dev-frappe-1 bench --site grievance.localhost list-apps
```

Without it you get `OCI runtime exec failed: Cwd must be an absolute path`.

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

| Container | Role |
| --- | --- |
| `oan_grievance_dev-frappe-1` | bench, where you run every command |
| `oan_grievance_dev-mariadb-1` | database |
| `oan_grievance_dev-redis-cache-1` | metadata and session cache |
| `oan_grievance_dev-redis-queue-1` | background jobs and scheduler |

From here on, either open the folder in VS Code and *Reopen in Container*, or shell in:

```bash
docker exec -it oan_grievance_dev-frappe-1 bash
```

The rest of this section assumes you are **inside the container**.

### 3.6 Initialise the bench

This clones the framework, builds a virtualenv and compiles front-end assets. It takes
roughly ten to fifteen minutes on a first run. That is normal, not a hang.

```bash
cd /workspace/development
bench init frappe-bench --skip-redis-config-generation --frappe-branch version-15
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
 "webserver_port": 8000
}
```

Confirm the service names resolve:

```bash
getent hosts mariadb redis-cache redis-queue
```

### 3.8 Add this app to the bench

```bash
cd /workspace/development/frappe-bench
bench get-app https://github.com/<org>/oan_grievance_service.git --branch develop
```

If you are working from a local checkout instead, see
[section 6](#6-repository-and-bench-relationship) first, because the repository is not
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

Two harmless messages appear here. `MariaDB version 11.8 is more than 10.8 which is not
yet tested` is an upstream version check, and `*** Scheduler is disabled ***` is the
default for a new site.

### 3.10 Install the app and enable developer mode

```bash
bench --site grievance.localhost install-app oan_grievance_service
bench set-config -g developer_mode 1
bench --site grievance.localhost migrate
```

Developer mode is required. Without it, doctype changes are written to the database only
and never exported to JSON files in the app, so your work will not reach git.

### 3.11 Verify

```bash
bench --site grievance.localhost list-apps
```

Expected:

```
frappe                15.120.0 version-15
oan_grievance_service 0.0.1    UNVERSIONED
```

---

## 4. Running the dev server

```bash
cd /workspace/development/frappe-bench
bench start
```

With the port mapping from step 3.4, the site is then at
**http://grievance.localhost:8100**, logging in as `Administrator` / `admin`.

`grievance.localhost` resolves to `127.0.0.1` automatically in modern browsers. If yours
does not, add it to `C:\Windows\System32\drivers\etc\hosts`.

---

## 5. Everyday commands

Run all of these from `/workspace/development/frappe-bench` inside the container.

| Task | Command |
| --- | --- |
| Start the dev server | `bench start` |
| Apply schema changes | `bench --site grievance.localhost migrate` |
| Open a Python shell | `bench --site grievance.localhost console` |
| Open a SQL shell | `bench --site grievance.localhost mariadb` |
| Run the app's tests | `bench --site grievance.localhost run-tests --app oan_grievance_service` |
| Rebuild assets | `bench build --app oan_grievance_service` |
| Clear caches | `bench --site grievance.localhost clear-cache` |
| Tail logs | `bench --site grievance.localhost show-config` then check `logs/` |

### Creating a doctype

Either create it in the browser under **Doctype**, which writes the JSON into the app
automatically while developer mode is on, or write the JSON by hand and run `migrate`.
Prefer the browser: it produces valid metadata and correct field ordering.

Doctypes belong to the **Grievance Management** module so they land in
`oan_grievance_service/grievance_management/doctype/`.

---

## 6. Repository and bench relationship

There are two copies of the app on the host, and they are **not linked**:

```
C:\Users\shail\oan_grievance_service\                  <- git repo, source of truth
C:\Users\shail\oan_grievance_service_devcontainer\     <- mounted as /workspace
    development/frappe-bench/apps/oan_grievance_service/   <- what bench runs
```

The compose file mounts the *devcontainer* folder, not the repository, so changes made
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

## 7. What the app contains

One module, **Grievance Management**, holding twelve doctypes drawn from section 5 of the
Functional Specification Document v1.3 D3.

| Doctype | Purpose |
| --- | --- |
| Grievance | The core ticket |
| Submitter Profile | Farmer, cooperative, FPO, NGO and Woreda/Kebele identity |
| Grievance Response | Immutable department reply |
| Grievance Status History | From/to status audit trail |
| Grievance Routing Rule | Category, type, area and provider to department |
| Grievance Department | Email account, head, senior and nodal officer |
| Grievance SLA Configuration | Per category and type, in days |
| Grievance Notification Log | The notification matrix events |
| Grievance Escalation Log | Level, trigger and stakeholders notified |
| Grievance RBAC Assignment | Region, department and category scope |
| Grievance Reassignment Request | The L2 approval flow |
| Grievance Access Audit Event | Scope evaluated, allow or deny |

Six roles are created on install: Farmer, Assisted-Submissions, L1 Nodal Officer,
L2 Senior Nodal Officer, Department Head and OAN Administrator-ATI. The Farmer role has
desk access disabled, since farmers reach the system through the portal.

### Known incomplete work

`grievance.py` contains two stubs that deliberately raise `NotImplementedError`. Each
depends on a contradiction in the specification that has not been resolved:

- **`start_sla()`** — section 4.2 starts the SLA clock at assignment, while use case
  UC-04 computes the breach from creation date. The two diverge whenever a grievance
  waits in the manual routing queue.
- **`apply_routing()`** — blocked on the same decision.

The ticket-number abbreviation rule is also a documented assumption. The specification's
own example code `AGRN` matches none of its five service categories, so the mapping in
`CATEGORY_CODES` needs confirming against the OAN registry before go-live.

---

## 8. Troubleshooting

**`failed to connect to the docker API at npipe:...`**
Docker Desktop is not running. Start it and wait for the engine.

**`OCI runtime exec failed: Cwd must be an absolute path`**
Git Bash rewrote the path. Prefix the command with `MSYS_NO_PATHCONV=1`.

**`Cannot connect to redis_cache to update assets_json`**
`common_site_config.json` still points at `127.0.0.1`. Redo step 3.7.

**`bench init` looks frozen**
It is not. It clones the framework, installs Python dependencies, then runs `yarn
install` and builds assets. Ten to fifteen minutes is normal on a first run.

**Site loads nothing on port 8100**
Either the `ports` block is missing from the compose file, or `FRAPPE_BIND_ADDR` is
unset so the server bound to loopback inside the container. See step 3.4.

**Doctype changes do not appear as files in git**
Developer mode is off. Run `bench set-config -g developer_mode 1`, then re-save the
doctype to force the export.

**`--no-mariadb-socket is DEPRECATED`**
Use `--mariadb-user-host-login-scope='%'` instead, as shown in step 3.9.

---

## 9. Resetting

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
