# Skills — Detection Techniques and Domain Knowledge

Each domain lists **Signals** (Tier 2: cheap, mechanical), **Deep analysis** (Tier 3:
reasoning applied to signal hits and blast-radius zones), and **Knowledge** the reviewer
brings. Domain numbers match `agent.md` and `rules.md`.

## Cross-domain techniques

- **Source-to-sink tracing.** Follow each untrusted value from entry point to every
  interpreter, store, response, log, and outbound call it reaches.
- **Trust-boundary mapping.** Mark where data crosses between client, service, store,
  broker, cache, third party, region, and tenant; every crossing needs a control.
- **Failure-mode enumeration.** For every external interaction, walk refused, slow,
  partial, erroring, duplicated, and malformed outcomes to their end state.
- **Interleaving analysis.** Execute two copies of the same use case step by step against
  the same record and look for lost updates, write skew, and phantoms.
- **Scale projection.** Re-evaluate each access path at 10x and 100x rows, users, tenants,
  and request rate; cost growing with data size on a request path is a finding.
- **Change-radius measurement.** Count how many locations a single business-rule change
  must touch; more than one indicates duplicated knowledge.
- **Blast-radius tagging.** Mark paths touching money, personal data, access control, or
  legal obligations for mandatory deep analysis and severity escalation.

## 1. API Contract & HTTP Semantics

- **Signals:** state mutation reachable from safe methods; success status set on error
  branches; collection handlers without a maximum size; persistence entities returned
  directly; handlers performing long work inline; missing or divergent contract definition.
- **Deep analysis:** compare the declared contract to each handler's actual fields, types,
  statuses, and error paths, including unhandled-failure paths; test pagination stability
  under concurrent inserts and deletes.
- **Knowledge:** method safety and idempotency semantics; status code classes; problem
  details structure; cursor versus offset behavior; long-running operation resources.

## 2. Request Boundary & Validation

- **Signals:** raw request structures passed below the handler; bulk binding of input maps
  to entities; absent size limits; file name or type taken from the client; external
  service responses used without validation.
- **Deep analysis:** trace every input source to first use; enumerate writable versus
  server-controlled fields per entity; probe coercion of empty, null, duplicate, oversized,
  and out-of-range values.
- **Knowledge:** allow-listing; canonicalize-then-validate ordering; mass assignment;
  parser differentials; content sniffing; boundary versus invariant validation.

## 3. Authentication & Session

- **Signals:** routes without an authentication declaration; token decoding without
  verification; algorithm read from the token itself; general-purpose hashes near password
  handling; credential endpoints without attempt counters; no session rotation at login.
- **Deep analysis:** reconcile the public-endpoint declaration with the full route
  inventory; walk logout, reset, and privilege-change paths for invalidation; check how
  internal callers prove identity.
- **Knowledge:** adaptive password hashing; token best practices (algorithm confusion,
  audience and issuer binding); session fixation; credential stuffing; account enumeration
  through messages and timing; zero-trust workload identity.

## 4. Authorization & Object Access

- **Signals:** fetch-by-identifier without an ownership or permission predicate; inline
  role conditionals in handlers; principal or role values read from request data;
  privileged routes guarded only by path; policy lookups with permissive fallback.
- **Deep analysis:** for every object-addressing operation, trace identifier to data access
  and confirm a principal-bound check precedes return or mutation; confirm list operations
  filter by principal; verify asynchronous work re-establishes the principal; compare field
  sensitivity against field-level enforcement.
- **Knowledge:** object-, function-, and property-level authorization failures; role-,
  attribute-, and relationship-based models; confused deputy; fail-safe defaults;
  time-of-check to time-of-use gaps.

## 5. Application Security Hardening

- **Signals:** text construction feeding interpreters; secret-shaped literals; outbound
  destinations built from input; type-resolving deserialization; weak primitives or fixed
  nonces; disabled certificate checks; wildcard or reflected origins with credentials;
  error handlers echoing internal messages; complex patterns applied to unbounded input.
- **Deep analysis:** taint-trace to every sink; follow redirect and name-resolution handling
  on outbound fetches; review key origin and rotation; confirm forgery protection wherever
  ambient credentials authenticate state changes.
- **Knowledge:** injection families (query, command, template, header, log, path,
  expression); request forgery to internal and metadata endpoints, DNS rebinding;
  authenticated encryption; catastrophic backtracking; timing side channels.

## 6. Data Modeling & Integrity

- **Signals:** uniqueness or existence checks in code without a matching constraint;
  optional storage for required values; free-text status attributes; the same attribute
  on several entities; mutable natural keys; references without declared delete behavior;
  narrow integer keys; random keys defining index order; sequential identifiers in
  responses; key type mismatches across references; association entities without pair
  uniqueness; binary payload attributes; append-only stores with no retention or
  partitioning declaration; timestamp or incrementing partition keys; unit-less quantity
  names; schema objects breaking the declared naming convention or left to auto-naming.
- **Deep analysis:** list invariants asserted in domain code and verify store-level
  enforcement for each; identify every writer (jobs, admin paths, other services,
  migrations); assign an owner to each denormalized fact; project key exhaustion and
  partition skew at 100x volume; confirm every derived store has a rebuild path from its
  system of record; compare every schema name against the declared convention.
- **Knowledge:** normal forms and deliberate denormalization; constraint types; key
  strategies and their trade-offs (sequence contention, random-key index locality,
  time-ordered identifiers, enumeration); surrogate versus natural keys; partitioning and
  hot-partition behavior; archival tiers; object storage versus row storage; naming
  conventions as a contract with operators and migrations; explicit state machines.

## 7. Query Efficiency & Data Access

- **Signals:** data access inside iteration; unbounded queries on growable stores; whole
  records loaded for one attribute; filters or sorts on unindexed attributes;
  function-wrapped predicates; aggregations inside request handlers; post-write reads
  routed to replicas; sorts on non-unique attributes without a tie-breaker; single-record
  writes inside iteration.
- **Deep analysis:** express query count and rows scanned per request as a function of
  result size; cross-reference every access path with declared indexes, including
  composite order and selectivity; project growth of each store.
- **Knowledge:** index structures; composite prefix rule; covering indexes; predicate
  sargability; eager versus lazy loading; keyset pagination; replication lag and
  read-your-writes.

## 8. Transactions & Concurrency

- **Signals:** read then write of one record without version or lock; check then insert;
  transactions enclosing outbound calls; module-level mutable state; locks without
  timeouts; transaction demarcation in handlers or data-access code; mass updates or
  deletes without a restricting predicate; bulk mutations in one unbounded transaction.
- **Deep analysis:** interleave concurrent executions to expose anomalies; verify the
  isolation level assumed matches the level configured; trace transaction propagation
  through nested calls; compare lock lease against worst-case work duration.
- **Knowledge:** isolation anomalies (dirty read, lost update, write skew, phantom);
  optimistic versus pessimistic control; atomic conditional updates; compensation sequences;
  fencing tokens; deadlock conditions.

## 9. Idempotency & Retry Safety

- **Signals:** creation or submission operations without an idempotency key; consumers
  producing side effects without a deduplication record; automatic retry around
  non-idempotent calls; jobs without checkpoints.
- **Deep analysis:** simulate duplicate delivery, and failure after the effect but before
  acknowledgement; verify key storage is atomic with the effect and scoped per caller;
  check behavior of the same key with a different payload.
- **Knowledge:** at-least-once delivery; exactly-once effect through deduplication;
  natural versus synthetic idempotency; key retention windows.

## 10. Resilience & Dependency Failure

- **Signals:** outbound calls without timeouts; retries without cap, backoff, or jitter;
  retry logic at several layers; failure handlers returning defaults or success; optional
  dependencies whose failure fails the request; shared pools across unrelated dependencies.
- **Deep analysis:** enumerate failure modes per dependency and follow each to the
  response; sum worst-case latency against the inbound budget; compute retry amplification
  across layers as the product of attempts.
- **Knowledge:** cascading failure; retry storms; circuit breaker states; bulkheads;
  deadline propagation; graceful degradation.

## 11. Background Processing & Messaging

- **Signals:** store write followed by publish outside one commit; acknowledgement before
  processing; no dead-letter destination; consumers assuming order; messages without
  identifier or version; schedules without a single-run guard; event types named as
  commands or without an owning context.
- **Deep analysis:** break execution between write and publish, and between effect and
  acknowledgement; follow poison-message handling; analyze ordering keys against the
  business order actually required; walk job interruption and restart.
- **Knowledge:** outbox and inbox patterns; dead-letter handling; partitioned ordering;
  competing consumers; visibility timeouts; leader election and leases.

## 12. Caching

- **Signals:** cache writes without expiry; keys missing user, tenant, locale, or permission
  dimensions; shared-cacheable response metadata on personalized data; authorization or
  financial reads served from cache; cache errors propagated to callers.
- **Deep analysis:** map each cached value to every source write path and verify
  invalidation; derive the full dependency set of each value and compare it with its key;
  model concurrent expiry on hot keys.
- **Knowledge:** cache-aside and write-through; expiry jitter; request coalescing; shared
  versus private cache semantics and response variance; negative caching.

## 13. Resource Governance & Performance

- **Signals:** unbounded collection growth; whole-payload reads of unbounded inputs;
  unbounded pools or worker creation; missing rate limits on reachable endpoints; blocking
  calls in non-blocking contexts; invariant work repeated per request.
- **Deep analysis:** project memory and CPU per request at maximum permitted input; derive
  concurrency ceilings; evaluate admission behavior at 10x load; identify abusable business
  flows and their velocity controls.
- **Knowledge:** queueing theory and Little's law; backpressure; token and leaky buckets;
  priority-based load shedding; streaming.

## 14. Observability

- **Signals:** free-text or concatenated log messages; missing correlation identifiers;
  sensitive attributes in log statements or serialized logged objects; unbounded metric
  label values; log-and-rethrow at every layer; application-managed log files.
- **Deep analysis:** follow one request across every hop, including asynchronous ones, and
  confirm continuous trace context; confirm golden signals per operation and per
  dependency; check redaction coverage for every logged structure.
- **Knowledge:** golden signals; request-rate/errors/duration and utilization/saturation
  methods; service level indicators and objectives; trace context; cardinality cost; log
  injection.

## 15. Configuration & Runtime Lifecycle

- **Signals:** branches on environment name; configuration read lazily without validation;
  local disk or memory holding shared state; no termination handling; liveness checks that
  call dependencies; diagnostic modes enabled by default; flags without defaults.
- **Deep analysis:** walk startup with missing and malformed configuration; walk shutdown
  under in-flight load; replace an instance mid-request and follow state loss.
- **Knowledge:** twelve-factor principles; process disposability; readiness versus liveness;
  feature-flag lifecycle and debt.

## 16. Schema & Contract Evolution

- **Signals:** drop, rename, or type-narrowing migrations shipped with the code change;
  index builds or rewrites that lock large stores; backfills inside schema migrations;
  removed or renamed response fields; new required inputs; removed enumeration values;
  removed or repurposed event fields.
- **Deep analysis:** run each migration mentally against the previous code version
  executing concurrently; estimate lock duration as a function of data size; check every
  consumer's tolerance of new fields and values.
- **Knowledge:** expand and contract; online schema change; backward and forward
  compatibility; tolerant reader; deprecation and sunset signalling.

## 17. Service Architecture & Modularity

- **Signals:** business modules referencing transport or persistence types; handlers with
  business conditionals or storage calls; access to another module's stored data;
  dependency cycles; references to other modules' internal types; shared libraries holding
  one service's domain model.
- **Deep analysis:** build the module dependency graph and check direction and cycles;
  build the data ownership map; build the synchronous inter-service call graph for cycles
  and depth; test whether separate read and write models have a declared justification.
- **Knowledge:** ports and adapters; the dependency rule; bounded contexts; dependency
  inversion and single responsibility at module level; criteria that justify separate
  read and write models; distributed-monolith indicators.

## 18. Code Economy & Maintainability

- **Signals:** near-identical logic in several places; interfaces with one implementation;
  pass-through layers; copied branches per country, tenant, or plan; cross-cutting code
  repeated per handler; hand-built utilities duplicating platform capabilities; long
  navigation chains across collaborators; one concept under several names.
- **Deep analysis:** separate duplicated knowledge (same reason to change) from incidental
  similarity; measure change radius of each business rule; test each abstraction for a
  second implementation or an active seam.
- **Knowledge:** DRY as knowledge, not text; avoid-hasty-abstraction heuristics; YAGNI;
  Law of Demeter; ubiquitous language; the cost of indirection.

## 19. Time, Locale & Internationalization

- **Signals:** unzoned timestamps; fixed offsets; host clock, zone, or locale read in
  business logic; day arithmetic by fixed hour counts; user-facing text assembled from
  literals; single-country format validators; unnormalized identifier comparison;
  one attribute type used for both instants and calendar dates.
- **Deep analysis:** evaluate every deadline across daylight-saving transitions and date
  boundaries; identify whose zone governs each calendar rule; check equality and
  uniqueness of identifiers under normalization and case folding.
- **Knowledge:** instant versus local versus zoned time; named zone database; transition
  gaps and overlaps; Unicode normalization forms, case folding, and confusable characters;
  locale-aware formatting; global variation in names and addresses.

## 20. Testing

- **Signals:** changed units without tests; authorization without negative tests; tests
  using real clocks, sleeps, unseeded randomness, or networks; persistence tests on
  substitute stores with different semantics; assertions on internal calls; realistic
  personal data or secrets in fixtures.
- **Deep analysis:** map every blast-radius path to tests for success and each failure;
  identify concurrency and duplicate-execution tests for guarded operations; estimate
  coverage as tested behavior units over changed behavior units.
- **Knowledge:** test pyramid and its cost profile; contract testing; causes of
  flakiness; mutation testing and property-based testing as concepts.

## 21. Tenancy & Data Residency

- **Signals:** tenant-owned queries without tenant scope; tenant identity from request data;
  tenant-agnostic storage paths, index names, or topics; cross-tenant administrative paths
  without audit; no per-tenant quotas; region-agnostic storage of regulated data.
- **Deep analysis:** enumerate every access path to tenant-owned data (queries, reports,
  exports, search, jobs, backups) and confirm a non-omittable scoping mechanism; trace
  regulated data through replicas, backups, and processors against permitted regions.
- **Knowledge:** silo, pool, and bridge tenancy models; store-enforced row scoping; noisy
  neighbor effects; data residency and cross-border transfer regimes.

## 22. Privacy & Personal Data Lifecycle

- **Signals:** personal fields without classification; collected fields never read; no
  retention mechanism; soft deletion as the only deletion; plaintext high-risk identifiers;
  processing without a consent check; full records sent to third parties.
- **Deep analysis:** trace each personal field through every copy (replicas, caches,
  indexes, analytics, backups, processors) and verify erasure, export, and retention reach
  all of them; bind each field to a declared purpose.
- **Knowledge:** lawful bases; purpose, minimization, and storage limitation principles;
  pseudonymization versus anonymization; special categories; processor obligations;
  statutory response windows.

## 23. Financial Correctness

- **Signals:** floating-point amount types; amounts without currency; implicit rounding;
  mutable balance attributes; updates or deletes on posted records; fixed two-place
  precision; conversions without a rate record; provider interactions without stored
  references.
- **Deep analysis:** verify conservation of totals across splits and allocations; verify
  ledger balance per transaction; walk provider lifecycles (pending, failed, reversed,
  disputed) for reconciliation; combine with interleaving analysis on balances.
- **Knowledge:** currency minor units; rounding modes including banker's rounding;
  double-entry bookkeeping; reversal entries; authorization versus capture versus
  settlement.

## 24. Audit Trail & Regulatory Obligations

- **Signals:** regulated actions without audit emission; audit stores writable by
  application identities; audit records mixed into operational logs; deadlines computed on
  read; deletion routines without legal-hold checks; sensitive values copied into audit.
- **Deep analysis:** enumerate regulated actions and verify each record's completeness;
  evaluate tamper evidence; verify deadline computation against governing time zone and
  business calendar; follow the escalation path for missed deadlines.
- **Knowledge:** tamper-evident logging (hash chaining, write-once storage); retention
  schedules; legal hold; breach and response notification windows.
