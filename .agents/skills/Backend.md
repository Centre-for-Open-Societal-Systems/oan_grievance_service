# THE BACKEND API RULE BOOK

### Engineering Standard for Building APIs at Billion-Dollar, Billion-User Scale

**Version 1.0 — Language & Framework Agnostic**

---

## HOW TO USE THIS DOCUMENT

This is not advice. This is a **gate**. Every rule uses RFC-2119 style keywords:

- **MUST** — non-negotiable. Violating this is a launch blocker / production incident waiting to happen.
- **SHOULD** — strong default. Deviation requires a written, reviewed justification.
- **MAY** — optional, context-dependent, but worth considering.

This rule book is used in two modes:

1. **BUILD MODE** — while designing/writing the API, check each rule as you implement the related component. Do not move to the next module until the current one's `MUST` items are satisfied.
2. **AUDIT MODE** — after the API is written (or before every release), run through every section as a checklist. An API is only considered "production-grade" when every `MUST` is checked and every unchecked `SHOULD` has a documented reason.

At the end of this document is a **Master Checklist** — the single source of truth for a go/no-go decision.

---

## 0. FIRST PRINCIPLES (apply to everything below)

- [ ] **MUST**: Design contract-first. The API contract (OpenAPI/Protobuf/GraphQL SDL/AsyncAPI) is written and reviewed _before_ implementation begins.
- [ ] **MUST**: Treat the API as a product with external consumers, even if today it only has one internal caller. Never design "just for now."
- [ ] **MUST**: Every design decision must survive the question: _"What happens at 1,000x current load, with 1,000x current data, with a hostile actor in the request path?"_
- [ ] **MUST**: Assume every input is malicious, every network call fails, every downstream service is slow, and every dependency will eventually be unavailable — design defensively as a default posture, not an afterthought.
- [ ] **SHOULD**: Optimize for the on-call engineer at 3 AM who has never seen this code before — clarity and observability beat cleverness.

---

## 1. API DESIGN & CONTRACT

### 1.1 Resource & Endpoint Design

- [ ] **MUST**: Use consistent, predictable naming conventions (nouns for resources, plural collections, consistent casing) across the entire surface.
- [ ] **MUST**: Model resources/operations around business capabilities, not database tables.
- [ ] **MUST**: Every mutating operation that can be retried by a client (network blip, timeout) **MUST** support idempotency (idempotency keys for POST, natural idempotency for PUT/DELETE).
- [ ] **MUST**: Pagination is mandatory on every collection-returning endpoint. No "return everything" endpoints, ever.
- [ ] **MUST**: Support filtering, sorting, and field-selection (sparse fieldsets) on list endpoints to avoid over-fetching at scale.
- [ ] **SHOULD**: Prefer cursor-based pagination over offset-based for large or frequently-mutated datasets.
- [ ] **SHOULD**: Design responses to be forward-compatible — additive fields should never break existing consumers.

### 1.2 Versioning & Evolution

- [ ] **MUST**: Version the API explicitly (URI, header, or content-negotiation) from day one, even at v1.
- [ ] **MUST**: Define and publish a deprecation policy (minimum notice period, sunset headers, migration guides) before deprecating anything.
- [ ] **MUST**: Breaking changes require a new version. Never silently change behavior, types, or semantics of an existing field/endpoint.
- [ ] **SHOULD**: Maintain backward compatibility for at least N major releases/months as defined by org policy; communicate the window clearly.

### 1.3 Contracts & Documentation

- [ ] **MUST**: Maintain a machine-readable spec (OpenAPI/Swagger, Protobuf, GraphQL schema) as the single source of truth, kept in sync with code via CI (contract tests fail the build on drift).
- [ ] **MUST**: Document every error code, its meaning, and remediation guidance.
- [ ] **MUST**: Document rate limits, authentication requirements, and pagination behavior per endpoint.
- [ ] **SHOULD**: Auto-generate client SDKs / Postman collections from the spec.

### 1.4 Request/Response Design

- [ ] **MUST**: Use standard, consistent HTTP status codes (or protocol-equivalent) — never 200 with an error payload.
- [ ] **MUST**: Standardize a single error response shape (code, message, trace/correlation ID, optional field-level details) across the entire API surface.
- [ ] **MUST**: Validate and sanitize every field of every request server-side — never trust client-side validation alone.
- [ ] **MUST**: Enforce strict schema validation (reject unknown fields or explicitly define handling) to prevent silent data corruption.
- [ ] **SHOULD**: Cap request/response payload sizes explicitly; reject oversized payloads early (at gateway, not deep in the stack).

---

## 2. SECURITY (NON-NEGOTIABLE)

### 2.1 Authentication & Authorization

- [ ] **MUST**: Every endpoint has an explicit authentication requirement — "no auth" must be a conscious, reviewed decision, never a default/oversight.
- [ ] **MUST**: Implement authorization checks at the resource/object level (object-level authorization) — never assume "authenticated" implies "authorized" (prevents BOLA/IDOR).
- [ ] **MUST**: Enforce least-privilege access — every service, key, and token gets the minimum scope required, nothing more.
- [ ] **MUST**: Use short-lived tokens with refresh flows over long-lived static credentials; support revocation.
- [ ] **MUST**: Re-validate authorization on every request; never cache an authorization decision beyond a short, defined TTL.
- [ ] **SHOULD**: Use a centralized policy engine (e.g., attribute/role-based access control layer) rather than scattering permission logic across handlers.

### 2.2 Input, Output & Injection

- [ ] **MUST**: Parameterize all database queries — zero string-concatenated queries anywhere in the codebase.
- [ ] **MUST**: Sanitize/escape all output rendered in another context (HTML, shell, log, SQL) to prevent injection in any direction.
- [ ] **MUST**: Enforce strict input validation: type, length, range, format, and allow-listed values — reject rather than "clean and continue" where feasible.
- [ ] **MUST**: Disable/guard against mass-assignment — clients must not be able to set fields they shouldn't control (e.g., `isAdmin`, `balance`) via generic object binding.
- [ ] **MUST**: Validate and restrict file uploads: type, size, content-sniffed MIME, storage isolated from execution paths.

### 2.3 Secrets, Encryption & Data Protection

- [ ] **MUST**: Zero secrets, keys, or credentials in source code, config files committed to VCS, or logs.
- [ ] **MUST**: Use a dedicated secrets manager/vault with rotation policies; secrets are injected at runtime, not baked into images.
- [ ] **MUST**: Encrypt data in transit (TLS 1.2+ everywhere, internal service-to-service included) and at rest (disk/DB-level encryption at minimum).
- [ ] **MUST**: Encrypt/tokenize sensitive PII/PCI/PHI fields at the application layer, not just relying on disk encryption.
- [ ] **MUST**: Never log secrets, tokens, passwords, full card numbers, or raw PII — mask/redact at the logging layer by default.

### 2.4 Rate Limiting, Abuse & Network Exposure

- [ ] **MUST**: Enforce rate limiting and quota per client/API key/IP at the gateway layer, with sane defaults and burst handling.
- [ ] **MUST**: Protect against brute force (auth endpoints), enumeration (predictable IDs), and scraping (aggressive pagination abuse).
- [ ] **MUST**: Run behind a WAF / API gateway that filters known attack patterns before requests reach application code.
- [ ] **MUST**: Apply the principle of network segmentation — internal services are not directly reachable from the public internet; only the gateway/edge is.
- [ ] **SHOULD**: Implement anomaly detection on traffic patterns (sudden spikes, geographic anomalies, credential-stuffing signatures).

### 2.5 Dependency & Supply Chain

- [ ] **MUST**: Run automated dependency vulnerability scanning (SCA) in CI; block builds on critical/high CVEs without an approved exception.
- [ ] **MUST**: Pin dependency versions; avoid unvetted "latest" tags in production builds.
- [ ] **MUST**: Scan container images for vulnerabilities before deployment; use minimal base images.
- [ ] **SHOULD**: Maintain a Software Bill of Materials (SBOM) for every deployed service.

### 2.6 Audit & Compliance

- [ ] **MUST**: Log every authentication event, authorization failure, and sensitive data access with actor, action, target, and timestamp — immutable, tamper-evident audit trail.
- [ ] **MUST**: Apply data retention and deletion policies aligned with relevant regulations (GDPR/CCPA/HIPAA/PCI-DSS as applicable to the domain).
- [ ] **MUST**: Support a "right to erasure"/data export mechanism if handling personal data under applicable law.
- [ ] **SHOULD**: Undergo regular third-party penetration testing and internal security review cadences.

---

## 3. SCALABILITY & PERFORMANCE

### 3.1 Statelessness & Horizontal Scaling

- [ ] **MUST**: Application servers are stateless — no in-memory session state that isn't reconstructable from a shared store; any instance can serve any request.
- [ ] **MUST**: Design for horizontal scale-out first; vertical scaling is a stopgap, not a strategy.
- [ ] **MUST**: Externalize all shared/session state to a distributed store (cache/DB), never local disk or process memory.

### 3.2 Caching

- [ ] **MUST**: Identify and cache expensive/frequently-read, infrequently-changed data at the appropriate layer (CDN, gateway, application, DB query cache).
- [ ] **MUST**: Define explicit cache invalidation strategy per cached resource — stale-data risk is a conscious decision, not an accident.
- [ ] **SHOULD**: Use cache-aside or write-through patterns deliberately; document which pattern is used per data type.
- [ ] **SHOULD**: Guard against cache stampede/thundering herd (request coalescing, jittered TTLs, locks).

### 3.3 Database & Data Access Efficiency

- [ ] **MUST**: Index all columns used in WHERE/JOIN/ORDER BY on high-traffic queries; verify via query plan analysis, not assumption.
- [ ] **MUST**: Eliminate N+1 query patterns — batch or join instead of looping queries per item.
- [ ] **MUST**: Use connection pooling with sane min/max limits tuned to actual concurrency needs; never open unbounded connections.
- [ ] **MUST**: Set explicit query timeouts; a runaway query must not be able to exhaust a connection pool indefinitely.
- [ ] **SHOULD**: Separate read and write paths (read replicas / CQRS) once read load materially exceeds write load.
- [ ] **SHOULD**: Use database-appropriate scaling strategy (sharding/partitioning) planned before it becomes an emergency, not after.

### 3.4 Asynchronous & Decoupled Processing

- [ ] **MUST**: Offload non-critical-path work (emails, notifications, analytics, heavy computation) to async queues/workers — never block the request/response cycle on it.
- [ ] **MUST**: Design queue consumers to be idempotent and safe under at-least-once delivery semantics.
- [ ] **SHOULD**: Use event-driven architecture / message brokers to decouple services that don't need synchronous coupling.
- [ ] **SHOULD**: Apply backpressure (bounded queues, load shedding) rather than allowing unbounded queue growth under load spikes.

### 3.5 Load & Capacity Validation

- [ ] **MUST**: Load-test every API against realistic and peak-multiple traffic (e.g., expected peak × 3–5) before major launches.
- [ ] **MUST**: Establish and document capacity limits per service (max RPS, max concurrent connections) and alert before those limits are approached.
- [ ] **SHOULD**: Run regular chaos/failure-injection tests to validate scaling and failover assumptions under real conditions, not just design docs.

---

## 4. RELIABILITY & RESILIENCE

- [ ] **MUST**: Set explicit timeouts on every outbound call (DB, cache, downstream API) — no call waits indefinitely.
- [ ] **MUST**: Implement retries with exponential backoff and jitter for transient failures; cap retry attempts to avoid retry storms.
- [ ] **MUST**: Implement circuit breakers around unreliable/downstream dependencies to prevent cascading failures.
- [ ] **MUST**: Apply bulkheading — isolate resource pools (threads/connections) per dependency so one slow dependency can't starve the whole service.
- [ ] **MUST**: Define graceful degradation behavior per feature — what does the API return when a non-critical dependency is down? (Never a full 500 for a partial failure.)
- [ ] **MUST**: Expose liveness and readiness health-check endpoints distinct from each other, used by orchestration/load balancers.
- [ ] **MUST**: Define and publish SLIs/SLOs (latency, availability, error rate) per API, with an error budget policy tied to release decisions.
- [ ] **SHOULD**: Practice regular game days / chaos engineering exercises (kill a node, saturate CPU, sever network) to validate resilience assumptions.
- [ ] **SHOULD**: Design multi-region/multi-AZ failover for tier-0/critical services, with regularly tested failover runbooks.

---

## 5. DATA MANAGEMENT & CONSISTENCY

- [ ] **MUST**: Every schema change goes through versioned, reversible migrations — never manual, untracked schema edits in production.
- [ ] **MUST**: Wrap multi-step writes that must succeed/fail together in transactions; understand and document the isolation level in use.
- [ ] **MUST**: Explicitly define the consistency model per data path (strong vs. eventual) and communicate it to consumers where it affects correctness.
- [ ] **MUST**: Maintain automated, tested backups with a documented and rehearsed restore procedure (a backup that's never been restored is not a backup).
- [ ] **MUST**: Classify data sensitivity (public/internal/confidential/restricted) and apply handling rules accordingly (encryption, access, retention).
- [ ] **SHOULD**: Design schemas to support zero-downtime migrations (expand/contract pattern) for high-traffic tables.
- [ ] **SHOULD**: Avoid distributed transactions where possible; prefer sagas/compensating actions for cross-service consistency.

---

## 6. OBSERVABILITY

- [ ] **MUST**: Emit structured (JSON or equivalent), machine-parseable logs — never unstructured free-text logging in production paths.
- [ ] **MUST**: Attach a correlation/trace ID to every request and propagate it across all downstream service calls.
- [ ] **MUST**: Instrument distributed tracing across service boundaries so a single request's full path is reconstructable.
- [ ] **MUST**: Expose the golden signals per service — Rate, Errors, Duration (RED) or Utilization/Saturation/Errors (USE) — as metrics, not just logs.
- [ ] **MUST**: Configure alerting on SLO breaches and error-budget burn rate, not just raw thresholds, to reduce alert fatigue.
- [ ] **MUST**: Ensure no alert exists without a linked runbook describing diagnosis and remediation steps.
- [ ] **SHOULD**: Maintain real-time dashboards per service covering traffic, errors, latency percentiles (p50/p95/p99), and saturation.
- [ ] **SHOULD**: Sample and retain enough tracing/logging data to debug incidents after the fact without re-deploying instrumentation.

---

## 7. MAINTAINABILITY & CODE QUALITY

- [ ] **MUST**: Apply clear separation of concerns / layered architecture (transport, business logic, data access) — business logic must not be entangled with framework/transport code.
- [ ] **MUST**: Apply SOLID principles and avoid duplication (DRY) at the module boundary level, not just line-by-line.
- [ ] **MUST**: Maintain automated test coverage across the pyramid: unit tests (fast, majority), integration tests (service boundaries), and a thin layer of end-to-end/contract tests.
- [ ] **MUST**: Enforce mandatory peer code review before merge — no direct pushes to main/production branches.
- [ ] **MUST**: Run linting, static analysis, and security scanning automatically in CI on every change.
- [ ] **MUST**: Keep configuration external to code (env vars/config service) and environment-specific — no hardcoded environment values.
- [ ] **SHOULD**: Keep functions/modules small and single-purpose; flag and refactor high-complexity/high-churn code proactively.
- [ ] **SHOULD**: Maintain up-to-date architecture decision records (ADRs) explaining _why_, not just _what_.
- [ ] **SHOULD**: Track and pay down technical debt explicitly (tracked backlog item, not tribal knowledge).

---

## 8. DEPLOYMENT & INFRASTRUCTURE

- [ ] **MUST**: Define all infrastructure as code (IaC) — no manually-clicked, undocumented infrastructure changes in production.
- [ ] **MUST**: Maintain environment parity (dev/staging/prod as similar as feasible) to catch environment-specific bugs before production.
- [ ] **MUST**: Deploy via automated CI/CD pipelines with mandatory automated tests as a gate — no manual, ad-hoc deployments to production.
- [ ] **MUST**: Support zero-downtime deployment strategies (rolling, blue-green, or canary) with automated rollback on failure signals.
- [ ] **MUST**: Ensure every deployment is instantly and safely reversible (rollback plan tested, not theoretical).
- [ ] **SHOULD**: Use feature flags to decouple deployment from release, enabling safe progressive rollout.
- [ ] **SHOULD**: Enforce immutable infrastructure (rebuild, don't patch running instances).

---

## 9. GOVERNANCE, COMPLIANCE & API LIFECYCLE

- [ ] **MUST**: Register every API in a central catalog/registry — no "shadow" or undocumented APIs in production.
- [ ] **MUST**: Assign clear ownership (team/on-call) to every API, discoverable by anyone in the org.
- [ ] **MUST**: Apply consistent security, rate-limiting, and logging policy centrally via API gateway, not reimplemented ad hoc per service.
- [ ] **MUST**: Review and re-certify third-party/partner API access on a defined cadence (access should expire by default, not persist forever).
- [ ] **SHOULD**: Track API usage analytics per consumer to inform deprecation, capacity planning, and monetization decisions.

---

## MASTER CHECKLIST — GO/NO-GO GATE

An API is **not production-ready for enterprise scale** until every box below is checked, or every unchecked box has a written, approved exception.

**Design & Contract**

- [ ] Contract-first spec exists, reviewed, and enforced in CI
- [ ] Versioning & deprecation policy defined
- [ ] Pagination, filtering, idempotency implemented on all relevant endpoints
- [ ] Standardized error format across the whole surface

**Security**

- [ ] AuthN + object-level AuthZ on every endpoint
- [ ] All inputs validated, all outputs encoded, zero string-built queries
- [ ] Secrets in vault, encryption in transit + at rest
- [ ] Rate limiting, WAF/gateway protection, dependency scanning in place
- [ ] Full audit trail on sensitive actions

**Scalability**

- [ ] Stateless services, horizontally scalable
- [ ] Caching strategy + invalidation defined
- [ ] No N+1 queries, indexes verified, pooled + timed-out DB connections
- [ ] Async offload for non-critical-path work
- [ ] Load-tested at 3–5x expected peak

**Reliability**

- [ ] Timeouts, retries w/ backoff+jitter, circuit breakers, bulkheads in place
- [ ] Graceful degradation defined per feature
- [ ] SLIs/SLOs/error budgets published
- [ ] Liveness/readiness health checks live

**Data**

- [ ] Versioned migrations, transactional integrity, documented consistency model
- [ ] Backups tested via real restore, not assumed
- [ ] Data classified, retention/deletion policy enforced

**Observability**

- [ ] Structured logs + correlation IDs + distributed tracing
- [ ] RED/USE metrics live, SLO-based alerting, linked runbooks

**Maintainability**

- [ ] Layered architecture, tests across the pyramid, mandatory code review
- [ ] CI enforces lint/static analysis/security scans
- [ ] Config externalized per environment

**Deployment & Governance**

- [ ] IaC, environment parity, automated CI/CD with rollback
- [ ] API registered in catalog with clear ownership
- [ ] Access/compliance review cadence defined

---

**Rule of thumb:** if you can't answer _"what happens when this fails, gets attacked, or gets 1000x traffic?"_ for any given line of this API, it is not done yet.
