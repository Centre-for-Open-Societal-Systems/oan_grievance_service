# Production Setup via Official frappe_docker

This guide follows the official **`frappe_docker` production setup** using upstream's pre-built compose manifests and overrides (`compose.yaml` + `overrides/`).

No custom compose files need to be written from scratch. You will use upstream `frappe_docker` templates directly to deploy `oan_grievance_service` and `oan_auth_service` with MariaDB, Redis, and Traefik (with automatic Let's Encrypt SSL).

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

Create `apps.json` in `~/frappe_docker`:

```json
[
  {
    "url": "https://github.com/<org>/oan_auth_service.git",
    "branch": "develop"
  },
  {
    "url": "https://github.com/<org>/oan_grievance_service.git",
    "branch": "develop"
  }
]
```

### 3.3 Build Custom Production Image

Build the image using upstream's official production `Containerfile`:

```bash
export APPS_JSON_BASE64=$(base64 -w 0 apps.json)

docker build \
  --build-arg=FRAPPE_BRANCH=version-16 \
  --build-arg=PYTHON_VERSION=3.14.2 \
  --build-arg=NODE_VERSION=24.13.0 \
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 \
  --tag custom-oan-grievance:latest \
  --file images/production/Containerfile .

```

---

## 4. Configure Production Environment (`.env`)

Upstream `frappe_docker` provides `example.env`. Copy it to `.env`:

```bash
cp example.env .env
```

Open `.env` and configure:

```bash
# Point all services to your custom built image
FRAPPE_VERSION=custom-oan-grievance:latest

# MariaDB Root Password
DB_PASSWORD=YourSecureDatabasePassword123

# Traefik & Let's Encrypt SSL
LETSENCRYPT_EMAIL=admin@example.com

# Domain name of your site
SITES=`grievance.example.com`
FRAPPE_SITE_NAME_HEADER=grievance.example.com
```

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

---

## 6. Start the Stack & Create Site

### 6.1 Start Containers

```bash
docker compose -p oan_grievance -f docker-compose.yml up -d
```

Verify all containers are up and healthy:

```bash
docker compose -p oan_grievance ps
```

### 6.2 Create the Production Site

```bash
docker compose -p oan_grievance exec backend bench new-site grievance.example.com \
  --db-root-password YourSecureDatabasePassword123 \
  --admin-password YourAdminPasswordHere123 \
  --mariadb-user-host-login-scope='%'
```

---

## 7. Configure `site_config.json`

_(A complete template is provided in [`site_config.example.json`](site_config.example.json))._

---

### 7.1 Apply Configuration via Bench CLI

```bash
SITE="grievance.example.com"

# 1. Production Mode
docker compose -p oan_grievance exec backend bench --site $SITE set-config developer_mode 0
docker compose -p oan_grievance exec backend bench --site $SITE set-config allow_tests false --parse

# 2. Generate secure 64-char JWT secret
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

# 3. Configure JWT keys & Active Key ID
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_secrets "{\"v1\": \"$JWT_SECRET\"}" --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_current_kid "v1"

# 4. Configure Public Registration Roles for oan_auth_service
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_default_registration_role "Grievance Submitter"
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_self_registerable_roles '["Grievance Submitter"]' --parse

# 5. Configure Token Lifetimes and Force HTTPS
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_access_token_ttl 900 --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_refresh_token_ttl 604800 --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_refresh_token_ttl_remember_me 7776000 --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_issuer "oan-grievance"
docker compose -p oan_grievance exec backend bench --site $SITE set-config jwt_enforce_https true --parse

# 6. Configure Grievance SLA Settings
docker compose -p oan_grievance exec backend bench --site $SITE set-config grievance_sla_clock_start "assignment"
docker compose -p oan_grievance exec backend bench --site $SITE set-config grievance_confirmation_window_days 7 --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config grievance_max_deferral_days 30 --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config grievance_auto_escalation_enabled true --parse
docker compose -p oan_grievance exec backend bench --site $SITE set-config grievance_sla_paused_statuses '["Pending Submitter", "Draft", "Withdrawn"]' --parse
```

---

## 8. Install Applications & Run Migrations

Install the apps baked into the container image and execute migrations:

```bash
SITE="grievance.example.com"

# 1. Install applications
docker compose -p oan_grievance exec backend bench --site $SITE install-app oan_auth_service
docker compose -p oan_grievance exec backend bench --site $SITE install-app oan_grievance_service

# 2. Run migrations and seed data
docker compose -p oan_grievance exec backend bench --site $SITE migrate
```

The `migrate` command executes seeders that provision:

- 21,028 Ethiopian Administrative Area tree nodes.
- Roles: `Grievance Submitter`, `Grievance Officer`, `Grievance Admin`.
- Role Levels: `nodal_officer`, `senior_nodal_officer`, `department_head`.
- Service categories, submission channels, and Appendix C notifications.

---

## 9. Verification

```bash
SITE="grievance.example.com"

# Verify installed apps on the site
docker compose -p oan_grievance exec backend bench --site $SITE list-apps

# Run automated tests
docker compose -p oan_grievance exec backend bench --site $SITE run-tests --app oan_grievance_service
```

---

## 10. Everyday Maintenance & Operations

Run all commands from `~/frappe_docker`:

| Task                       | Command                                                                                               |
| -------------------------- | ----------------------------------------------------------------------------------------------------- |
| **View Live Logs**         | `docker compose -p oan_grievance logs -f backend frontend`                                            |
| **Check Container Status** | `docker compose -p oan_grievance ps`                                                                  |
| **Restart All Services**   | `docker compose -p oan_grievance restart`                                                             |
| **Stop All Services**      | `docker compose -p oan_grievance down`                                                                |
| **Backup Site & Files**    | `docker compose -p oan_grievance exec backend bench --site grievance.example.com backup --with-files` |
| **Clear App Cache**        | `docker compose -p oan_grievance exec backend bench --site grievance.example.com clear-cache`         |
| **Open Python Shell**      | `docker compose -p oan_grievance exec -it backend bench --site grievance.example.com console`         |
| **Open MariaDB Shell**     | `docker compose -p oan_grievance exec -it backend bench --site grievance.example.com mariadb`         |

### Updating App Code in Production

When new code is pushed to your repositories:

```bash
# 1. Rebuild the custom image
export APPS_JSON_BASE64=$(base64 -w 0 apps.json)
docker build \
  --build-arg=FRAPPE_BRANCH=version-16 \
  --build-arg=PYTHON_VERSION=3.14.2 \
  --build-arg=NODE_VERSION=24.13.0 \
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 \
  --tag custom-oan-grievance:latest \
  --file images/production/Containerfile .


# 2. Recreate containers
docker compose -p oan_grievance -f docker-compose.yml up -d --force-recreate

# 3. Run database migrations
docker compose -p oan_grievance exec backend bench --site grievance.example.com migrate
```
