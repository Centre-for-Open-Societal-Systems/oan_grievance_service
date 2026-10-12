# Backend Code Review Agent

A diagnostic reviewer for backend systems that serve millions of concurrent users across
regions, tenants, currencies, and jurisdictions. It locates, classifies, and explains the
consequence of every defect against a fixed standard. It never remediates.

## Files and load order

| Order | File         | Job                                                                      |
| ----- | ------------ | ------------------------------------------------------------------------ |
| 1     | `ignore.md`  | Removes out-of-scope paths and methods before anything else is read.     |
| 2     | `persona.md` | Identity, tiered workflow, severity model, grading, exact report format. |
| 3     | `rules.md`   | The standard. Every rule has an ID, a default severity, and a source.    |
| 4     | `skills.md`  | Detection techniques and domain knowledge, per domain.                   |
| —     | `SOURCES.md` | Bibliography. Resolves citation keys; not loaded during a review.        |

Precedence on conflict: `ignore.md` (scope) > `rules.md` (what is a violation) >
`persona.md` (process and output) > `skills.md` (technique).

## Domain activation

- Baseline domains 1–20 apply to every review.
- Supplementary domains 21–24 activate when the reviewed code shows any trigger below.
  For a multi-tenant, multinational deployment all four are expected to be active.
- A domain with no trigger is reported as "not applicable" in the Domains Swept count;
  it is never silently skipped.

| #   | Activation trigger (evidenced in reviewed code)                                             |
| --- | ------------------------------------------------------------------------------------------- |
| 21  | Any tenant, organization, or account-partition identifier; more than one deployment region. |
| 22  | Any field identifying or describable to a natural person.                                   |
| 23  | Any amount, price, balance, fee, rate, or payment state.                                    |
| 24  | Any regulated action, statutory deadline, retention period, or audit requirement.           |

## Check domains

### Baseline — any backend

| #   | Domain                              | Primary Concern                                      | Why a separate domain                                                                    |
| --- | ----------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 1   | API Contract & HTTP Semantics       | Current shape and semantics of the exposed interface | Every consumer pays for contract defects; they cannot be fixed without breaking clients. |
| 2   | Request Boundary & Validation       | Conversion of untrusted input into trusted data      | The boundary is the only point where shape can be enforced once for all downstream code. |
| 3   | Authentication & Session            | Correctness of caller identity                       | Every other control assumes identity is already correct.                                 |
| 4   | Authorization & Object Access       | Permission of a principal for an action on an object | Object-level authorization is the top API risk and invisible to scanners.                |
| 5   | Application Security Hardening      | Injection, secrets, cryptography, SSRF, disclosure   | These are exploitable even when input is well-formed.                                    |
| 6   | Data Modeling & Integrity           | Invariants, keys, growth, and naming of stored data  | Only the store sees every writer; invariants held elsewhere eventually break.            |
| 7   | Query Efficiency & Data Access      | Cost of reading data as volume grows                 | Access paths harmless at thousands of rows become outages at hundreds of millions.       |
| 8   | Transactions & Concurrency          | Atomicity and correctness under concurrent writes    | At millions of concurrent users, every possible race eventually occurs.                  |
| 9   | Idempotency & Retry Safety          | Duplicate-safety of every effect                     | Networks and queues deliver duplicates; a duplicated write is corruption.                |
| 10  | Resilience & Dependency Failure     | Surviving failure of what the service calls          | One unbounded wait on a failing dependency cascades into full outage.                    |
| 11  | Background Processing & Messaging   | Reliable asynchronous delivery and execution         | Asynchronous work fails silently unless delivery is engineered explicitly.               |
| 12  | Caching                             | Freshness, scoping, and failure of cached data       | Caches multiply both throughput and the blast radius of stale or leaked data.            |
| 13  | Resource Governance & Performance   | Bounding the service's own consumption               | The system must bound itself before load bounds it.                                      |
| 14  | Observability                       | Telemetry content, correlation, and hygiene          | Unobservable failures are unfixable, and telemetry is a leak channel.                    |
| 15  | Configuration & Runtime Lifecycle   | Config, statelessness, startup, shutdown, health     | Horizontal scaling and zero-downtime deploys require disposable processes.               |
| 16  | Schema & Contract Evolution         | Compatibility of changes over time                   | Old and new code run at once against the same data, clients, and events.                 |
| 17  | Service Architecture & Modularity   | Dependencies and ownership between modules           | Boundaries decide whether hundreds of contributors can change code independently.        |
| 18  | Code Economy & Maintainability      | Duplication, speculative abstraction, dead code      | Every line is attack surface, review cost, and debt; one source of truth per rule.       |
| 19  | Time, Locale & Internationalization | Time zones, calendars, text, and regional formats    | A multinational system runs in every zone, script, and format simultaneously.            |
| 20  | Testing                             | Proof that behavior and rules keep holding           | Tests are the only durable evidence the other 23 domains still hold after change.        |

### Supplementary — high-stakes systems

| #   | Domain                               | Primary Concern                              | Why a separate domain                                                         |
| --- | ------------------------------------ | -------------------------------------------- | ----------------------------------------------------------------------------- |
| 21  | Tenancy & Data Residency             | Structural isolation of tenants and regions  | One missed tenant scope is a cross-customer breach; residency is law.         |
| 22  | Privacy & Personal Data Lifecycle    | Legal lifecycle of personal data             | Personal data carries purpose, retention, and erasure duties beyond security. |
| 23  | Financial Correctness                | Exactness and traceability of money          | Money errors are irreversible, compounding, and audited.                      |
| 24  | Audit Trail & Regulatory Obligations | Provable history and met statutory deadlines | Legal obligations require evidence, not intent.                               |

## Domain boundaries — one concern, one owner

- 1 owns the current contract; 16 owns every change to contracts, schemas, and events.
- 2 owns input shape and binding; 5 owns safe handling of data passed to interpreters and
  destinations, regardless of validation.
- 3 owns who the caller is; 4 owns what that caller may do; 21 owns tenant partitioning of
  every data path. Cross-tenant access is a 21 finding, cross-user access within a tenant
  is a 4 finding.
- 5 owns all secrets and error-message disclosure; 15 owns non-secret configuration and
  unsafe diagnostic defaults.
- 6 owns what is stored: constraints, keys, partitioning, placement, and schema naming;
  7 owns how data is read and written in volume; 8 owns atomicity, concurrency, and bulk
  mutation safety.
- Naming: 1 owns API fields, 6 owns schema objects, 11 owns event types, 19 owns temporal
  naming, 18 owns one concept carrying one name across all layers.
- 9 owns duplicate-safety of every effect (API, message, job); 10 owns retry mechanics for
  outbound calls; 11 owns queue topology, acknowledgement, ordering, and dead-lettering.
- 1 owns pagination of exposed collections; 7 owns bounds on internal queries; 13 owns
  memory, pools, rate limits, and admission control.
- 10 owns surviving others' failure; 13 owns preventing the service's own overload.
- 12 owns all cache scoping, including tenant and permission dimensions.
- 14 owns all operational telemetry, including redaction; 24 owns business audit records;
  22 owns personal data in stores and transfers.
- 17 owns dependencies between modules; 18 owns economy within them; 4 owns authorization
  centralization; 18 owns centralization of other cross-cutting concerns.
- 19 owns time, locale, text, and regional formats; 23 owns currency, rounding, and ledgers.
- 20 owns test sufficiency; no other domain flags missing tests.
