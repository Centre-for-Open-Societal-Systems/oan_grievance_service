# Production Setup via Official frappe_docker

This guide follows the official **`frappe_docker` production setup** using upstream's pre-built compose manifests and overrides (`compose.yaml` + `overrides/`).

No custom compose files need to be written from scratch. You will use upstream `frappe_docker` templates directly to deploy `oan_grievance_service` and `oan_auth_service` with MariaDB, Redis, and Traefik (with automatic Let's Encrypt SSL).

Two kinds of command appear below:

- **Host commands** (`docker ...`) build the image and manage the containers. Run them from `~/frappe_docker`.
- **Bench commands** (`bench ...`) run **inside the `backend` container**, never through `docker compose exec` from the host. Open a shell first (section 6.2) and run every `bench` command from there.

---

## 1. Stack Dependencies & Version Matrix

| Component / Layer      | Name                    | Version / Branch               | Purpose                                                                             |
| ---------------------- | ----------------------- | ------------------------------ | ----------------------------------------------------------------------------------- |
| **Base Framework**     | `frappe`                | `version-16` (`16.0.0`)        | Full-stack low-code framework, ORM, Desk, and migrations engine                     |
| **Auth Service**       | `oan_auth_service`      | `develop` (`>=0.0.1`)          | Core dependency: JWT authentication, signing key resolution, user self-registration |
| **Grievance Service**  | `oan_grievance_service` | `develop` (`0.0.1`)            | Grievance intake, auto-routing, SLA tracking, and escalations                       |
| **Runtime Language**   | Python                  | `3.14.2`                       | Backend execution environment                                                       |
| **Frontend Toolchain** | Node.js / Yarn          | `v24.13.0` / `Yarn 1.22+`      | Client assets and SocketIO WebSocket service                                        |
| **Database**           | MariaDB                 | `11.8.8`                       | Persistent relational database with `utf8mb4_unicode_ci`                            |
| **Cache & Queues**     | Redis                   | `8.10.1` (or `8.x` / `alpine`) | Session cache, metadata cache, and RQ background queues                             |
| **Edge Reverse Proxy** | Traefik                 | `v3.1`                         | Ports 80/443, automated Let's Encrypt SSL certificates, routing                     |

---

## 2. Prerequisites on Linux Host

Ensure Docker and Docker Compose v2 are installed on your host:

```bash
sudo apt update && sudo apt install -y curl git ca-certificates gnupg

# Add Docker's official GPG key & repository
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable running docker without sudo
sudo usermod -aG docker $USER
newgrp docker
```

---

## 3. Clone `frappe_docker` & Build Custom Image

### 3.1 Clone Upstream `frappe_docker`

```bash
git clone https://github.com/frappe/frappe_docker.git ~/frappe_docker
cd ~/frappe_docker
```

### 3.2 Define Custom Apps in `apps.json`

Create `apps.json` in `~/frappe_docker`. List only the extra apps; frappe itself comes from the `FRAPPE_BRANCH` build argument. `oan_auth_service` must be listed, because `oan_grievance_service` declares it in `required_apps`.

```json
[
  {
    "url": "https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service",
    "branch": "develop"
  },
  {
    "url": "https://github.com/Centre-for-Open-Societal-Systems/oan_grievance_service",
    "branch": "develop"
  }
]
```

### 3.3 Build Custom Production Image

Build with upstream's layered `Containerfile`, the same build the Jenkins pipeline runs:

```bash
DOCKER_BUILDKIT=1 docker build \
  --secret id=apps_json,src=apps.json \
  --build-arg CACHE_BUST="$(date +%s)" \
  --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg FRAPPE_BRANCH=version-16 \
  --file images/layered/Containerfile \
  --tag custom-oan-grievance:latest \
  .

# Refuse to continue with an image that is missing the OAN apps
docker run --rm custom-oan-grievance:latest \
  bash -c "test -d apps/oan_auth_service && test -d apps/oan_grievance_service && echo OK"
```

- Current `frappe_docker` reads `apps.json` from the BuildKit secret `apps_json`. The old `APPS_JSON_BASE64` build argument is silently ignored and produces an image with plain frappe only.
- BuildKit leaves secret contents out of its cache key. `CACHE_BUST` forces `bench init` to fetch the apps again; without it a cached layer can ship old app code.

---

## 4. Configure Production Environment (`.env`)

Upstream `frappe_docker` provides `example.env`. Copy it to `.env`:

```bash
cp example.env .env
```

Open `.env` and configure:

```bash
# Point all services to your custom built image
CUSTOM_IMAGE=custom-oan-grievance
CUSTOM_TAG=latest
# The image exists only locally, so do not try to pull it
PULL_POLICY=never

# MariaDB Root Password
DB_PASSWORD=YourSecureDatabasePassword123

# Traefik & Let's Encrypt SSL
LETSENCRYPT_EMAIL=admin@example.com

# Domain name of your site
SITES=`grievance.example.com`
FRAPPE_SITE_NAME_HEADER=grievance.example.com
```

If the image is pushed to a registry (for example ECR), set `CUSTOM_IMAGE` to the full repository path and remove `PULL_POLICY=never`.

---

## 5. Generate Production Compose Using Upstream Overrides

Merge upstream's override files into a single `docker-compose.yml`:

```bash
docker compose \
  --env-file .env \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.https.yaml \
  config > docker-compose.yml
```

_(Note: If you are behind an external load balancer or Cloudflare and do not need Traefik SSL, substitute `compose.https.yaml` with `overrides/compose.noproxy.yaml`)._

### 5.1 ClamAV for attachment scanning

Uploads stay `Pending` and cannot be downloaded until ClamAV marks them `Clean`. With no scanner, they settle at `Failed`. Add a `clamav` service to `docker-compose.yml` before starting the stack:

```yaml
  clamav:
    image: clamav/clamav:stable
    restart: unless-stopped
```

The site points at it in section 7.4. No port needs publishing on the host; the backend and workers reach it as `clamav:3310` on the compose network. See [`SETUP.md` section 4.1](SETUP.md#41-clamav--attachment-scanning).

### 5.2 Workers for the grievance queues

Routing, SLA checks, ClamAV scans and notification dispatch run on their own queues: `grievance_routing`, `grievance_sla`, `grievance_scan` and `grievance_notifications`. Upstream's `queue-short` and `queue-long` services do not listen on them, so jobs would sit in Redis forever. Add one worker service per queue to `docker-compose.yml`, copying the `queue-short` service and changing only the command:

```yaml
  queue-grievance-routing:
    # same image, volumes, environment and depends_on as queue-short
    command: ["bench", "worker", "--queue", "grievance_routing"]
  queue-grievance-sla:
    command: ["bench", "worker", "--queue", "grievance_sla"]
  queue-grievance-scan:
    command: ["bench", "worker", "--queue", "grievance_scan"]
  queue-grievance-notifications:
    command: ["bench", "worker", "--queue", "grievance_notifications"]
```

The queues must also be declared in `common_site_config.json` (section 7.4).

---

## 6. Start the Stack & Create Site

### 6.1 Start Containers (host)

```bash
docker compose -p oan_grievance -f docker-compose.yml up -d
```

Verify all containers are up and healthy:

```bash
docker compose -p oan_grievance ps
```

The `configurator` container exits after it writes `db_host` and the Redis URLs into `sites/common_site_config.json`. That is expected.

### 6.2 Open a Shell in the Backend Container (host)

```bash
docker compose -p oan_grievance exec backend bash
```

The shell starts in `/home/frappe/frappe-bench`. **Every remaining step runs in this shell.**

### 6.3 Create the Production Site (backend shell)

```bash
SITE="grievance.example.com"

bench new-site "$SITE" \
  --db-root-password YourSecureDatabasePassword123 \
  --admin-password YourAdminPasswordHere123 \
  --mariadb-user-host-login-scope='%'
```

`new-site` writes a fresh `encryption_key` into the site config. Back it up with the database: without it, encrypted fields and passwords in a restored database cannot be read.

---

## 7. Configure `site_config.json` (backend shell)

_(A complete template is provided in [`site_config.example.json`](site_config.example.json). Every key is described in [`SETUP.md` section 4.3](SETUP.md#43-site-configuration-keys).)_

### 7.1 Production Mode

```bash
SITE="grievance.example.com"

bench --site "$SITE" set-config developer_mode 0 --parse
bench --site "$SITE" set-config allow_tests false --parse
```

### 7.2 JWT Signing Key (required)

`oan_auth_service` signs access tokens with RS256. It refuses every login until `jwt_private_keys` names a readable, unencrypted RSA key of at least 2048 bits. The retired `jwt_secrets` key is no longer read.

Create the key inside the site folder, so it lives on the persistent `sites` volume:

```bash
mkdir -p "sites/$SITE/keys"
env/bin/python - "sites/$SITE/keys/jwt_v1.pem" <<'EOF'
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
with open(sys.argv[1], "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
EOF
chmod 600 "sites/$SITE/keys/jwt_v1.pem"

bench --site "$SITE" set-config jwt_private_keys '{"v1": "keys/jwt_v1.pem"}' --parse
bench --site "$SITE" set-config jwt_current_kid "v1"
```

The key is made with the bench virtualenv's `cryptography` package, so it works whether or not the image has an `openssl` binary.

Kong and other verifiers need only the public half. The site publishes it as a JWKS at `/api/v1/auth/keys`. See `oan_auth_service/kong/README_kong_onboarding.md`.

To rotate: add `v2` to `jwt_private_keys`, wait for verifiers to load the new JWKS, set `jwt_current_kid` to `v2`, then remove `v1` after the longest refresh window has passed.

### 7.3 Registration Roles, Token Lifetimes, HTTPS

```bash
# Without jwt_self_registerable_roles, guest self-registration is refused
bench --site "$SITE" set-config jwt_default_registration_role "Grievance Submitter"
bench --site "$SITE" set-config jwt_self_registerable_roles '["Grievance Submitter"]' --parse

bench --site "$SITE" set-config jwt_access_token_ttl 900 --parse
bench --site "$SITE" set-config jwt_refresh_token_ttl 604800 --parse
bench --site "$SITE" set-config jwt_refresh_token_ttl_remember_me 7776000 --parse
bench --site "$SITE" set-config jwt_issuer "oan-grievance-prod"
bench --site "$SITE" set-config jwt_enforce_https true --parse
```

Give each environment its own `jwt_issuer`, so a staging token fails closed against production.

### 7.4 Grievance Settings

```bash
bench --site "$SITE" set-config grievance_clamav_host clamav
bench --site "$SITE" set-config grievance_clamav_port 3310 --parse
bench --site "$SITE" set-config grievance_confirmation_window_days 7 --parse
bench --site "$SITE" set-config grievance_change_request_hours 24 --parse
bench --site "$SITE" set-config grievance_auto_escalation_enabled true --parse
bench set-config -g workers '{"grievance_routing": {"timeout": 300}, "grievance_sla": {"timeout": 1800}, "grievance_scan": {"timeout": 300}, "grievance_notifications": {"timeout": 600}}' --parse
```

The last line declares the grievance queues bench-wide. Without it `frappe.enqueue` rejects every routing, SLA, scan and notification job. Restart the stack after setting it so the workers pick it up.

The maximum deferral is set on the **Grievance Deferral Policy** doctype in Desk, not in site config. `grievance_max_deferral_days` and `grievance_sla_clock_start` are no longer read.

### 7.5 Delegated Login via OpenG2P IAM (optional)

Only if logins are delegated to IAM:

```bash
bench --site "$SITE" set-config iam_base_url "https://iam.example.org"
bench --site "$SITE" set-config iam_application "oan-grievance"
```

`iam_application` names the IAM client whose roles are granted on login. Without it, delegated logins are rejected. `iam_base_url` must be `https://`.

---

## 8. Install Applications & Run Migrations (backend shell)

```bash
SITE="grievance.example.com"

# 1. Install applications, auth service first
bench --site "$SITE" install-app oan_auth_service
bench --site "$SITE" install-app oan_grievance_service

# 2. Run migrations and seed data
bench --site "$SITE" migrate

# 3. Turn on the scheduler (off by default on a new site)
bench --site "$SITE" enable-scheduler
```

The `scheduler` container runs in the stack, but it skips any site whose scheduler is disabled. Without step 3, SLA reminders, escalation, notifications, routing and attachment scanning never run. The job list is in [`SETUP.md` section 4.4](SETUP.md#44-scheduled-jobs).

The `migrate` command runs the patches and then the seeders that provision:

- 21,028 Ethiopian Administrative Area tree nodes.
- Roles: `Grievance Submitter`, `Grievance Assisted Submitter`, `Grievance Officer`, `Grievance Admin`, `Grievance Desk Head`, `Grievance Review Officer` (read-only), `Grievance Dashboard Reader`.
- Role Levels: `nodal_officer` (L1), `senior_nodal_officer` (L2), `department_head` (L3, shown as "Category Admin").
- Service categories, grievance types, submitter and submission types, SLA configurations, the workflow, and Appendix C notifications.

Seeders are safe to rerun. They skip records that already exist, including service categories, submitter types and submission types that an admin renamed but whose `code` is unchanged. The `grant_desk_head_role` patch gives `Grievance Desk Head` to existing L3 officers; no manual role assignment is needed.

---

## 9. Verification

In the backend shell:

```bash
bench --site "$SITE" list-apps
bench --site "$SITE" doctor
```

`doctor` must report the scheduler as enabled and workers online. Tests are not run in production; `allow_tests` is `false`.

From the host:

```bash
curl -s https://grievance.example.com/api/method/ping
```

Expected: `{"message":"pong"}`.

---

## 10. Everyday Maintenance & Operations

### Host commands

Run from `~/frappe_docker`:

| Task                       | Command                                                    |
| -------------------------- | ---------------------------------------------------------- |
| **Open a Backend Shell**   | `docker compose -p oan_grievance exec backend bash`        |
| **View Live Logs**         | `docker compose -p oan_grievance logs -f backend frontend` |
| **Check Container Status** | `docker compose -p oan_grievance ps`                       |
| **Restart All Services**   | `docker compose -p oan_grievance restart`                  |
| **Stop All Services**      | `docker compose -p oan_grievance down`                     |

### Bench commands

Run inside the backend shell:

| Task                    | Command                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| **Backup Site & Files** | `bench --site grievance.example.com backup --with-files`                                   |
| **Clear App Cache**     | `bench --site grievance.example.com clear-cache`                                           |
| **Open Python Shell**   | `bench --site grievance.example.com console`                                               |
| **Open MariaDB Shell**  | `bench --site grievance.example.com mariadb`                                               |
| **Run One Job Now**     | `bench --site grievance.example.com execute oan_grievance_service.tasks.escalate_breached` |

A backup does not include `sites/<site>/keys/`. Copy the JWT key with it, or every issued token stops verifying after a restore.

### Updating App Code in Production

When new code is pushed to your repositories:

```bash
# 1. Rebuild the custom image (host)
DOCKER_BUILDKIT=1 docker build \
  --secret id=apps_json,src=apps.json \
  --build-arg CACHE_BUST="$(date +%s)" \
  --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg FRAPPE_BRANCH=version-16 \
  --file images/layered/Containerfile \
  --tag custom-oan-grievance:latest \
  .

# 2. Recreate containers (host)
docker compose -p oan_grievance -f docker-compose.yml up -d --force-recreate

# 3. Open a backend shell (host)
docker compose -p oan_grievance exec backend bash
```

Then, in the backend shell:

```bash
# 4. Run database migrations and clear the cache
bench --site grievance.example.com migrate
bench --site grievance.example.com clear-cache
```

A migration cannot be undone by going back to the old image. Take a backup before step 4.
