# Kong Gateway Setup

How OAN services publish their routes to a shared Kong gateway, the two ways Kong can store that configuration (DB-less and database-backed), and the CI/CD pipeline for each.

This document assumes several services (`oan_auth_service`, `oan_grievance_service`, and more later) sit behind **one** Kong, and each service owns the Kong configuration for its own routes.

---

## 1. Ownership model

Every service repo owns a **slice** of the gateway. A slice is the set of Kong entities carrying that service's tags.

```
                         ┌───────────── Kong Gateway ─────────────┐
 oan_auth_service   ──►  │ slice: [oan, auth]       routes, issuer │
 oan_grievance_service ► │ slice: [oan, grievance]  routes, clients │
 <future service>   ──►  │ slice: [oan, <service>]  routes          │
                         └─────────────────────────────────────────┘
```

Rules:

1. **Each repo generates its own `kong/kong.yml`** from its public OpenAPI spec (`kong/generate_kong_config_from_spec.py`). Never hand-edit `kong.yml`.
2. **Each `kong.yml` declares its slice** in `_info.select_tags`:

   ```yaml
   _format_version: '3.0'
   _info:
     select_tags:
     - oan
     - grievance
   ```

   decK only reads, diffs and deletes entities that carry **all** of these tags. Without `select_tags`, decK treats the file as the whole gateway and deletes every other service's routes on sync.

3. **Tags must be unique per service.** A new service picks `[oan, <service>]` and never reuses another service's tag pair.
4. **One owner per entity.** Kong entity names are global (service names, route names, consumer usernames). If two slices declare the same entity, their syncs conflict. Shared entities have exactly one owner:

   | Entity                                      | Owner              | Why                                                           |
   | :------------------------------------------ | :----------------- | :------------------------------------------------------------ |
   | `oan-auth-jwt-issuer` consumer + JWT secret | `oan_auth_service` | Auth issues the tokens every other service verifies           |
   | Service-specific client consumers           | That service       | e.g. `oan-citizen-mobile-client` in grievance                 |
   | Client consumers used by several services   | `oan_auth_service` | Move them there once a second service needs them              |
   | Gateway-wide plugins (global CORS, logging) | Platform repo      | Not owned by any service; see [Section 4.2](#42-db-less-mode) |

   Other services' `jwt` plugins still match the issuer consumer through the token's `iss` claim, so they never need to declare it.

5. **Name entities with the service prefix.** Kong service `oan-<service>-v1`; route names are generated from method and path, and paths are already unique per service because each service owns its own `/api/v1/<resource>` prefix.

---

## 2. DB-less vs database-backed Kong

Kong runs in one of two storage modes, set by `KONG_DATABASE`.

|                               | DB-less (`KONG_DATABASE=off`)                                     | Database (`KONG_DATABASE=postgres`)                    |
| :---------------------------- | :---------------------------------------------------------------- | :----------------------------------------------------- |
| Where config lives            | In memory on each node, loaded from one declarative file          | Postgres; nodes cache it in memory                     |
| Admin API                     | Read-only, except `POST /config`                                  | Read-write                                             |
| How config changes            | `POST /config` **replaces the whole config**                      | Entity by entity; decK sends only the differences      |
| Per-service slices            | Not independently deployable; slices must be merged into one file | Each repo syncs its own slice with `select_tags`       |
| Extra infrastructure          | None                                                              | Postgres (backups, HA, `kong migrations` on upgrade)   |
| Failure behaviour             | Each node independent; nothing shared to fail                     | Nodes keep serving from cache during a short DB outage |
| Plugins needing a DB          | Not available (e.g. OAuth2, `cluster` rate-limit policy)          | All available                                          |
| Kong Manager / manual changes | Not possible                                                      | Possible, **but forbidden here** (see Section 5)       |
| Scaling                       | Every node must receive the same file                             | Nodes read the same DB                                 |

A third option, **hybrid mode**, runs one control plane with Postgres and many DB-less data planes that receive config from it. It combines per-slice syncing with independent data-plane nodes. It is worth it only with many gateway nodes or regions.

### Which to use

- **Several services deploying independently to one Kong: database mode.** It matches the ownership model in Section 1: every repo syncs its own slice from its own pipeline.
- **DB-less** is fine when one central job owns the whole gateway config. It needs the merge step in Section 4.2, and a bad merge takes down every service's routes at once.

### Switching later

Git is the source of truth, not Kong, so either direction is a small job.

- **DB-less to database:** run Postgres, `kong migrations bootstrap`, set `KONG_DATABASE=postgres`, restart Kong, then run `deck gateway sync` for every service's `kong.yml`.
- **Database to DB-less:** `deck gateway dump -o kong.yml` for a single combined file, then start Kong with `KONG_DECLARATIVE_CONFIG` pointing at it.

Only runtime state is lost. Rate-limit counters use the `redis` policy, so they live in Redis and survive the switch.

---

## 3. CI: the same in both modes

CI runs on every push and every pull request in each service repo. It never talks to a live gateway.

| Step               | Command                                                                                    | Catches                                             |
| :----------------- | :----------------------------------------------------------------------------------------- | :-------------------------------------------------- |
| Regenerate         | `python openapi/generate_openapi_spec.py && python kong/generate_kong_config_from_spec.py` | —                                                   |
| Drift check        | `git diff --exit-code -- openapi/ kong/`                                                   | New endpoints committed without regenerated configs |
| Offline validation | `deck file validate kong/kong.yml`                                                         | Malformed entities and plugin config                |
| Slice check        | Assert `_info.select_tags` is set and every consumer name is owned by this repo            | A slice that would delete or clash with others      |

In this repo, the regenerate and drift-check steps run in `.github/workflows/generated-files.yml`. The OpenAPI generator imports the app's endpoint modules to discover routes, so the job sets up a bench with `frappe` and `oan_auth_service` (no site or database) and fails if any endpoint module fails to import. Pin PyYAML in CI (`6.0.3`) so line wrapping in the generated YAML is identical on every machine.

---

## 4. CD: depends on the mode

### 4.1 Database mode

Each service repo deploys its own slice. No central job is needed.

```
service repo push ──► CI green ──► deploy pipeline
                                      │
                                      ├─ app deploy (migrate, restart)
                                      └─ Kong sync  (only if kong/** changed)
                                            deck gateway diff  kong/kong.yml
                                            deck gateway sync  kong/kong.yml
```

Pipeline stage (Jenkins example):

```groovy
stage('Kong sync') {
    when {
        allOf {
            changeset 'kong/**'
            expression { env.KONG_ADDR?.trim() }
        }
    }
    steps {
        sh 'deck gateway diff --kong-addr "$KONG_ADDR" kong/kong.yml'
        sh 'deck gateway sync --kong-addr "$KONG_ADDR" kong/kong.yml'
    }
}
```

Notes:

- **Order.** Sync Kong **after** the app deploy succeeds when adding routes, so Kong never routes to an endpoint that does not exist yet. When removing routes, the order does not matter much: a removed route simply returns 404 from Kong instead of the app.
- **No restart.** decK uses the Admin API. Kong applies the changed entities live; when nothing changed, sync does nothing.
- **Concurrency.** Two services syncing at the same time is safe because their slices do not overlap. Two pipelines for the **same** service must not run at once (`disableConcurrentBuilds()`).
- **Admin API access.** Only the deploy agent can reach the Admin API (`KONG_ADDR`). Protect it with network rules or mTLS; never expose port 8001 publicly.
- **Rollback.** Revert the commit and redeploy; the sync restores the previous slice.

### 4.2 DB-less mode

`POST /config` replaces the whole gateway config, so no single service can deploy alone. A central **gateway config** job assembles every slice and pushes the result.

```
auth repo push ─────┐
grievance repo push ┼──► trigger gateway job ──► fetch every service's kong.yml (pinned refs)
<service> push ─────┘                           │
                                                ├─ deck file merge  → combined.yml
                                                ├─ add platform-wide entities (global plugins)
                                                ├─ deck file validate combined.yml
                                                ├─ deck file render combined.yml → rendered.json
                                                └─ POST rendered.json to /config on every Kong node
```

Notes:

- **Where the job lives.** A separate platform repo (for example `oan_gateway_config`) that lists each service and the git ref of its `kong.yml`. Service pipelines trigger it (GitHub `repository_dispatch`, or a downstream Jenkins job).
- **Pinned refs.** Record which commit of each service's `kong.yml` went into each combined config, so a rollback is "redeploy the previous combined file".
- **Blast radius.** One invalid slice fails the whole merge. Validation must pass before anything is pushed; on failure, the gateway keeps its last good config.
- **Every node.** Push the same rendered config to every Kong node, or mount it as a file and roll the nodes. Nodes that miss the push serve stale routes.
- **No restart** is needed for `POST /config`, but it rebuilds the router for every route, not only the changed ones.
- `select_tags` is still worth keeping in each slice: it keeps slices separable and makes a later move to database mode a no-op.

---

## 5. Rules for both modes

1. **Git is the only source of truth.** No changes through Kong Manager or direct Admin API calls. The next sync silently overwrites them.
2. **No secrets in `kong.yml`.** Use Kong vault references, for example `secret: "{vault://env/OAN_JWT_SECRET}"`, so the file is safe to commit and the secret comes from the gateway's environment or secret manager.
3. **Generated files are committed and checked.** The CI drift check fails a pull request that changes endpoints without regenerating `openapi/` and `kong/`.
4. **Each new endpoint needs a throttling tier.** The generator refuses to run when a spec route has no entry in `TIER_OVERRIDES`.

5. **Only generated routes are exposed.** Kong routes match exact API paths (for example `~/api/v1/grievances$`). Anything else on the proxy port, including `/`, Frappe Desk (`/app`, `/login`) and `/api/method/*`, returns Kong's `404 {"message":"no Route matched with those values"}`. Staff reach Frappe Desk on its internal URL, not through Kong. Kong Manager and the Admin API run on separate ports (8002 and 8001) and must never be exposed publicly.

---

## 6. Adding a new service

1. Copy the generator. Set the upstream URL, Kong service name `oan-<service>-v1`, and `SELECT_TAGS = ["oan", "<service>"]`.
2. Declare only consumers this service owns. Reuse `oan-auth-jwt-issuer` through the `jwt` plugin; do not redeclare it.
3. Add the CI workflow from Section 3.
4. **Database mode:** add the Kong sync stage from Section 4.1. **DB-less mode:** register the service and its `kong.yml` path in the gateway config repo.

---

## 7. Known gaps (as of 2026-10-01)

- `oan_auth_service`'s `kong.yml` has no `select_tags` yet. Until it does, a sync from the auth side can delete grievance's routes.
- The rate-limiting plugins use `policy: redis` but set no Redis host. Kong's plugin schema is expected to reject this on sync; configure the Redis connection (per plugin, or through a shared Redis partial) before the first live sync.
- The auth repo's `README_kong_onboarding.md` describes its setup as DB-less but uses `deck sync` per repo. Those two do not combine: pick a mode from Section 2 and align that document.
- No pipeline syncs Kong yet; the Jenkins stage in Section 4.1 is the proposed shape.
- The generators are duplicated across repos. Moving the shared logic into `oan_auth_service` (with each service keeping a small config file) is proposed separately.
