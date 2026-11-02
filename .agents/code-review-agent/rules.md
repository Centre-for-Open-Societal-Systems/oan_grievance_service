# Rules — The Standard

- **ID** `R{domain}.{n}` — domain numbers match `agent.md` and `skills.md`.
- **Sev** is the default severity before escalation (see `persona.md`).
- **Source** is a key in `SOURCES.md`, or `Proposed — house standard` where no external
  source exists.
- Any deviation from a rule is a violation. Where a rule requires a declaration (policy,
  contract, classification, owner), absence of that declaration in the reviewed code is
  the violation.

## 1. API Contract & HTTP Semantics

| ID    | Rule                                                                                                                                         | Sev | Source                             |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------- | --- | ---------------------------------- |
| R1.1  | Operations exposed through safe methods must not change server state.                                                                        | 🟡  | RFC9110 §9.2.1                     |
| R1.2  | Response status codes must match outcome class; an error outcome must never be returned with a success status.                               | 🟡  | RFC9110 §15                        |
| R1.3  | All error responses of an API must use one machine-readable problem format carrying a stable type identifier, status, and title.             | 🟡  | RFC9457; ZAL; AIP-193; CONV-ASPNET |
| R1.4  | Every collection endpoint must be paginated with a server-enforced maximum page size.                                                        | 🟡  | AIP-158; ZAL; API4                 |
| R1.5  | Collections that grow without bound or change during traversal must paginate by opaque cursor, not numeric offset.                           | 🟡  | ZAL; AIP-158                       |
| R1.6  | Operations whose work can exceed the synchronous response budget must be exposed as long-running operations with a pollable status resource. | 🟡  | AIP-151; MSREST                    |
| R1.7  | Responses must be produced from explicit output models; persistence entities must not be serialized directly.                                | 🟡  | API3; CONV-NEST                    |
| R1.8  | Every endpoint must be declared in a machine-readable contract whose request, response, and error schemas match the implementation.          | 🟡  | API9; ZAL                          |
| R1.9  | Full replacement and partial modification must be distinct operations with defined semantics for absent and null fields.                     | 🟢  | RFC9110 §9.3.4; RFC7396            |
| R1.10 | Field naming and casing must be uniform across all endpoints of an API.                                                                      | 🟢  | GAPI; ZAL                          |

## 2. Request Boundary & Validation

| ID   | Rule                                                                                                                                                                                           | Sev | Source                         |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------------ |
| R2.1 | Every externally sourced input (body, path, query, headers, cookies, files, message payloads) must be validated against an explicit schema before business logic uses it.                      | 🔴  | ASVS·Validation; CONV-LARAVEL  |
| R2.2 | Validation must be allow-list based on type, range, length, format, and enumeration; unknown fields must be rejected.                                                                          | 🟡  | ASVS·Validation; CONV-NEST     |
| R2.3 | Binding input to entities must use an explicit allow-list of writable fields; server-controlled fields (identity, ownership, role, status, price, timestamps) must not be settable from input. | 🔴  | API3; CONV-RAILS; CONV-LARAVEL |
| R2.4 | Maximum sizes must be enforced for request bodies, individual fields, embedded collections, nesting depth, and uploaded files.                                                                 | 🟡  | API4; ASVS·API                 |
| R2.5 | Invalid input must be rejected; it must not be silently truncated, coerced, or defaulted.                                                                                                      | 🟡  | ASVS·Validation                |
| R2.6 | Uploaded files must be validated by content rather than declared name or type, and stored under server-generated names outside executable or directly served paths.                            | 🔴  | ASVS·Files                     |
| R2.7 | Business invariants must be enforced in the domain layer independently of boundary validation.                                                                                                 | 🟡  | CONV-PHOENIX; DDD              |
| R2.8 | Data received from internal services and third-party APIs must be validated with the same rigor as client input.                                                                               | 🟡  | API10                          |

## 3. Authentication & Session

| ID   | Rule                                                                                                                                                       | Sev | Source                                   |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ---------------------------------------- |
| R3.1 | Every endpoint must require authentication unless listed as public in one enumerable declaration; the default is deny.                                     | 🔴  | ASVS·Authentication; API2; CONV-SPRING   |
| R3.2 | Passwords must be stored only with a salted, adaptive, password-specific hashing function; reversible encryption and general-purpose hashes are forbidden. | 🔴  | ASVS·Authentication; OWASP-PWD; NIST-63B |
| R3.3 | Credential-verifying endpoints (login, reset, one-time code, token exchange) must limit attempts per account and per source.                               | 🔴  | API2; NIST-63B                           |
| R3.4 | Self-contained tokens must be verified on every request for signature, a fixed allowed algorithm, issuer, audience, and expiry.                            | 🔴  | RFC8725; ASVS·Tokens                     |
| R3.5 | Session identifiers must be regenerated on authentication and privilege change, and invalidated server-side on logout and credential reset.                | 🟡  | ASVS·Session                             |
| R3.6 | Access credentials must have bounded lifetimes; long-lived credentials must be revocable server-side.                                                      | 🟡  | ASVS·Session; ASVS·Tokens                |
| R3.7 | Authentication and recovery responses must not reveal whether an account exists.                                                                           | 🟡  | ASVS·Authentication                      |
| R3.8 | Service-to-service calls must authenticate the calling workload; network location is not identity.                                                         | 🔴  | NIST-207                                 |
| R3.9 | Tokens, reset links, and one-time codes must come from a cryptographically secure random source, expire, and be single-use where the flow is one-time.     | 🔴  | ASVS·Authentication                      |

## 4. Authorization & Object Access

| ID   | Rule                                                                                                                                                                | Sev | Source                                        |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | --------------------------------------------- |
| R4.1 | Every operation that accesses an object by a client-supplied identifier must verify the caller's right to that specific object in the same code path as the access. | 🔴  | API1                                          |
| R4.2 | Every privileged function must enforce its permission check server-side; route obscurity and client-side hiding are not controls.                                   | 🔴  | API5; CONV-NEST                               |
| R4.3 | Authorization decisions must be made through a centralized policy mechanism, not ad-hoc conditionals repeated across handlers.                                      | 🟡  | CONV-LARAVEL; CONV-ASPNET; ASVS·Authorization |
| R4.4 | A missing policy, a policy error, or an indeterminate result must deny.                                                                                             | 🔴  | SALTZER; ASVS·Authorization                   |
| R4.5 | Where fields of one object differ in sensitivity, read and write permission must be enforced per field.                                                             | 🟡  | API3                                          |
| R4.6 | Authorization must use identity and roles from the verified credential only, never from request body, query, or client-controlled headers.                          | 🔴  | ASVS·Authorization                            |
| R4.7 | Asynchronous work performed on behalf of a principal must evaluate authorization for that principal at execution time.                                              | 🟡  | Proposed — house standard                     |

## 5. Application Security Hardening

| ID    | Rule                                                                                                                                                                                 | Sev | Source                         |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --- | ------------------------------ |
| R5.1  | Data must never be concatenated into query, command, template, path, header, or expression text consumed by an interpreter; only parameterized interfaces or context-aware encoding. | 🔴  | ASVS·Encoding; OWASP-TOP10     |
| R5.2  | Secrets must not appear in source, committed configuration, fixtures, or default values; they are read at runtime from a secret store or environment.                                | 🔴  | 12F-III; ASVS·Configuration    |
| R5.3  | Outbound requests to input-derived destinations must be restricted to an allow-list and must block private, loopback, link-local, and metadata ranges, including after redirects.    | 🔴  | API7; OWASP-SSRF               |
| R5.4  | Untrusted data must be deserialized only into declared data-only schemas; type-instantiating deserialization of untrusted input is forbidden.                                        | 🔴  | OWASP-DESER; ASVS·Encoding     |
| R5.5  | Cryptography must use vetted primitives in authenticated modes; custom algorithms, deprecated primitives, reused or static nonces, and embedded keys are forbidden.                  | 🔴  | ASVS·Cryptography              |
| R5.6  | Transport certificate verification must never be disabled.                                                                                                                           | 🔴  | ASVS·Communication             |
| R5.7  | Error responses must not expose stack traces, query text, internal hostnames, file paths, or dependency error text.                                                                  | 🟡  | API8; ASVS·Logging & Errors    |
| R5.8  | Comparisons of secrets, signatures, and message authentication codes must be constant-time.                                                                                          | 🟡  | ASVS·Cryptography              |
| R5.9  | Cross-origin policy must enumerate allowed origins; reflecting the request origin while allowing credentials is forbidden.                                                           | 🔴  | API8; ASVS·Web Frontend        |
| R5.10 | State-changing endpoints authenticated by ambient credentials (cookies) must enforce cross-site request forgery protection.                                                          | 🔴  | ASVS·Web Frontend; CONV-DJANGO |
| R5.11 | Paths derived from input must be canonicalized and confined to a declared base location.                                                                                             | 🔴  | ASVS·Files                     |
| R5.12 | Patterns matched against input must be free of catastrophic backtracking, or input length must be bounded before matching.                                                           | 🟡  | OWASP-REDOS                    |

## 6. Data Modeling & Integrity

| ID   | Rule                                                                                                                                                                                           | Sev | Source                         |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------------ |
| R6.1 | Invariants that must hold across all writers (uniqueness, referential integrity, required values, allowed values) must be enforced by store-level constraints, not only by application checks. | 🔴  | DDIA; CONV-RAILS; CONV-PHOENIX |
| R6.2 | Every entity must have a stable, immutable primary key; mutable natural attributes must not serve as primary keys.                                                                             | 🟡  | DBGUIDE                        |
| R6.3 | Keys generated by multiple nodes or regions must not depend on a single centralized sequence.                                                                                                  | 🟡  | Proposed — house standard      |
| R6.4 | Lifecycle states must be stored as constrained values, and permitted transitions must be defined in one place and enforced on every write.                                                     | 🟡  | Proposed — house standard      |
| R6.5 | A fact stored in more than one place must have a declared owner and a defined synchronization path.                                                                                            | 🟡  | DDIA                           |
| R6.6 | Every reference between entities must declare its deletion behavior (restrict, cascade, or nullify).                                                                                           | 🟡  | DBGUIDE                        |
| R6.7 | Mutable business entities must record creation and last-modification instants.                                                                                                                 | 🟢  | CONV-RAILS; CONV-DJANGO        |

### Primary and foreign keys

| ID    | Rule                                                                                                                                                | Sev | Source                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R6.8  | Primary keys of growable stores must use a width that cannot be exhausted at projected scale; 32-bit integer keys on growable stores are forbidden. | 🟡  | Proposed — house standard |
| R6.9  | Keys not drawn from a single sequence must be time-ordered; fully random values must not define the primary index order of a growable store.        | 🟡  | RFC9562                   |
| R6.10 | Identifiers exposed outside the service must not be sequential or otherwise enumerable.                                                             | 🟡  | OWASP-IDOR                |
| R6.11 | Every business entity keyed by a surrogate key must enforce uniqueness of its natural identifying attributes with a separate constraint.            | 🟡  | DBGUIDE                   |
| R6.12 | Foreign keys must match the type and width of the key they reference.                                                                               | 🟡  | DBGUIDE                   |
| R6.13 | Association entities must enforce uniqueness of the referenced pair.                                                                                | 🟡  | DBGUIDE                   |

### Growth and storage placement

| ID    | Rule                                                                                                                              | Sev | Source                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R6.14 | Stores that grow without bound must declare a partitioning, retention, or archival strategy.                                      | 🟡  | DDIA                      |
| R6.15 | Partition and shard keys must distribute load; monotonic or low-cardinality partition keys are forbidden.                         | 🟡  | DDIA                      |
| R6.16 | Large binary content must be held in object storage and referenced from records, not stored in rows.                              | 🟡  | Proposed — house standard |
| R6.17 | Every derived store (search index, read model, projection) must be rebuildable from its system of record by a declared procedure. | 🟡  | DDIA; CQRS                |

### Schema naming

| ID    | Rule                                                                                                             | Sev | Source                    |
| ----- | ---------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R6.18 | Stores, attributes, constraints, and indexes must follow one declared naming convention across the system.       | 🟢  | DBGUIDE                   |
| R6.19 | Constraints and indexes must have explicit, deterministic names.                                                 | 🟢  | Proposed — house standard |
| R6.20 | Reference attributes must be named consistently from the referenced entity and its key.                          | 🟢  | CONV-RAILS; CONV-DJANGO   |
| R6.21 | Attributes holding quantities must state their unit or representation in the name unless the type carries it.    | 🟡  | Proposed — house standard |
| R6.22 | Schema names must not be reserved words of the storage engine's query language and must not differ only by case. | 🟢  | DBGUIDE                   |

## 7. Query Efficiency & Data Access

| ID   | Rule                                                                                                                                    | Sev | Source                  |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------- | --- | ----------------------- |
| R7.1 | Storage or remote calls must not be issued per element inside iteration over a result set; related data is fetched in batch or by join. | 🟡  | CONV-DJANGO; CONV-RAILS |
| R7.2 | Every query on a store that grows with users or time must be bounded by limit, pagination, or a provably small key range.               | 🟡  | API4; SRE               |
| R7.3 | Every filter, join, and sort used by a runtime query on a growable store must be supported by a declared index.                         | 🟡  | WINAND                  |
| R7.4 | Predicates must not wrap indexed attributes in functions or conversions that prevent index use.                                         | 🟡  | WINAND                  |
| R7.5 | Hot-path reads must not load attributes or large payloads the caller does not use.                                                      | 🟢  | WINAND                  |
| R7.6 | Aggregations over unbounded data must not execute on the synchronous request path; they are precomputed or offloaded.                   | 🟡  | CQRS; SRE               |
| R7.7 | A read that follows a write by the same actor must not be served from a replica that can lag the write.                                 | 🟡  | DDIA                    |
| R7.8 | Ordered and paginated queries must sort by a unique key or include a unique tie-breaker.                                                | 🟡  | WINAND                  |
| R7.9 | Multi-record writes must be issued as batch operations, not one write per element inside iteration.                                     | 🟡  | CONV-DJANGO; CONV-RAILS |

## 8. Transactions & Concurrency

| ID    | Rule                                                                                                                                                         | Sev | Source                    |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | --- | ------------------------- |
| R8.1  | Writes that must succeed or fail together must run in one transaction or under a defined compensation sequence.                                              | 🔴  | DDIA; MS-SAGA             |
| R8.2  | Transactions must not span network calls to other services, user interaction, or unbounded work.                                                             | 🟡  | DDIA; CONV-SPRING         |
| R8.3  | Read-modify-write of shared state must use optimistic versioning, an atomic conditional update, or an explicit lock.                                         | 🔴  | DDIA                      |
| R8.4  | Check-then-act on limited resources (uniqueness, availability, quantity, balance) must be enforced atomically by the store.                                  | 🔴  | DDIA                      |
| R8.5  | Locks must be acquired in a consistent order and with a timeout.                                                                                             | 🟡  | DBGUIDE                   |
| R8.6  | In-process mutable state shared by concurrent requests must be synchronized or eliminated; request- and user-scoped data must never be held in shared state. | 🔴  | 12F-VI                    |
| R8.7  | Transaction boundaries must be declared at the use-case layer, not in handlers or data-access code.                                                          | 🟡  | CONV-SPRING               |
| R8.8  | Transactions whose correctness depends on concurrent reads must declare their isolation level explicitly.                                                    | 🟡  | DDIA                      |
| R8.9  | Distributed locks must use leases with expiry and fencing tokens checked by the protected resource.                                                          | 🟡  | KLEPPMANN-LOCK            |
| R8.10 | Bulk updates and deletes must be restricted by an explicit predicate; unscoped mass mutation of a store is forbidden.                                        | 🔴  | Proposed — house standard |
| R8.11 | Bulk mutations on growable stores must run in bounded batches, each in its own bounded transaction.                                                          | 🟡  | DDIA                      |

## 9. Idempotency & Retry Safety

| ID   | Rule                                                                                                                                               | Sev | Source                          |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------------- |
| R9.1 | Non-idempotent state-changing operations that clients can retry must accept an idempotency key and return the original outcome for a repeated key. | 🟡  | IETF-IDEM; STRIPE-IDEM; AIP-155 |
| R9.2 | Message and event consumers must be idempotent: a repeated delivery must not produce a second effect.                                              | 🔴  | MSIO-IDEMCONSUMER               |
| R9.3 | Idempotency keys must be scoped to the caller and recorded atomically with the effect they guard.                                                  | 🟡  | STRIPE-IDEM; AWS-IDEM           |
| R9.4 | Scheduled and batch jobs must be safe to re-run after partial completion.                                                                          | 🟡  | AWS-IDEM                        |
| R9.5 | Only operations that are idempotent or guarded by an idempotency key may be retried automatically.                                                 | 🔴  | AWS-RETRY                       |

## 10. Resilience & Dependency Failure

| ID    | Rule                                                                                                                    | Sev | Source                  |
| ----- | ----------------------------------------------------------------------------------------------------------------------- | --- | ----------------------- |
| R10.1 | Every outbound network call (store, cache, queue, service) must have an explicit timeout.                               | 🔴  | AWS-RETRY; SRE; CONV-GO |
| R10.2 | Deadlines and cancellation must propagate; a downstream timeout must not exceed the inbound request's remaining budget. | 🟡  | CONV-GO; SRE            |
| R10.3 | Retries must be bounded, use exponential backoff with jitter, and occur at one layer only.                              | 🟡  | AWS-RETRY; SRE          |
| R10.4 | Each independently failing dependency must be isolated so its failure cannot exhaust resources shared with other work.  | 🟡  | MS-BREAKER; MS-BULKHEAD |
| R10.5 | Failure of a non-critical dependency must degrade the response, not fail it.                                            | 🟡  | SRE                     |
| R10.6 | A failure must not be converted into a success response or a default value without a declared fallback.                 | 🟡  | CONV-GO; GEP            |

## 11. Background Processing & Messaging

| ID    | Rule                                                                                                                             | Sev | Source                    |
| ----- | -------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R11.1 | A state change and the message announcing it must be committed atomically; dual writes to a store and a broker are forbidden.    | 🔴  | MSIO-OUTBOX; DDIA         |
| R11.2 | Every consumer must have bounded redelivery and a dead-letter destination; a poison message must not block its queue.            | 🟡  | MS-QLOAD; CONV-LARAVEL    |
| R11.3 | Messages must be acknowledged only after their effects are durably committed.                                                    | 🔴  | DDIA                      |
| R11.4 | Where order matters it must be guaranteed by partition key or detected by sequence or version; global order must not be assumed. | 🟡  | DDIA                      |
| R11.5 | Every message must carry a unique identifier, a schema version, and a correlation identifier.                                    | 🟡  | CLOUDEVENTS               |
| R11.6 | Jobs must have a maximum execution time and defined behavior on interruption.                                                    | 🟡  | 12F-IX                    |
| R11.7 | Scheduled work running on multiple instances must guarantee a single execution per schedule.                                     | 🟡  | Proposed — house standard |
| R11.8 | Event types must follow one declared naming convention that names a completed domain fact and its owning context.                | 🟢  | CLOUDEVENTS               |

## 12. Caching

| ID    | Rule                                                                                                                    | Sev | Source                             |
| ----- | ----------------------------------------------------------------------------------------------------------------------- | --- | ---------------------------------- |
| R12.1 | Every cache entry must have a time-to-live or an invalidation trigger bound to the source write path.                   | 🟡  | MS-CACHE                           |
| R12.2 | Cache keys must include every dimension the value depends on, including user, tenant, locale, and permission scope.     | 🔴  | RFC9111 §4.1                       |
| R12.3 | Responses containing personal or authorization-dependent data must forbid storage by shared caches.                     | 🔴  | RFC9111 §5.2; ASVS·Data Protection |
| R12.4 | Expiry of hot keys must not release concurrent recomputation by all callers.                                            | 🟡  | MS-CACHE                           |
| R12.5 | Cache unavailability must not fail requests unless the cache is the declared system of record.                          | 🟡  | MS-CACHE                           |
| R12.6 | Authorization and financial decisions must not rely on cached values without revalidation against the system of record. | 🔴  | Proposed — house standard          |

## 13. Resource Governance & Performance

| ID    | Rule                                                                                                                                           | Sev | Source                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R13.1 | Externally reachable endpoints must be rate-limited per client identity and signal rejection with a too-many-requests status and a retry hint. | 🟡  | API4; RFC6585             |
| R13.2 | Business flows open to automated abuse must have per-actor velocity limits independent of generic rate limits.                                 | 🟡  | API6                      |
| R13.3 | Pools of connections, threads, and workers must be bounded, and every acquired resource must be released on all paths.                         | 🔴  | SRE                       |
| R13.4 | Inputs and outputs of unbounded size must be streamed or chunked, never held entirely in memory.                                               | 🟡  | API4                      |
| R13.5 | In-memory collections, buffers, and local caches must have a maximum size.                                                                     | 🟡  | SRE                       |
| R13.6 | Under overload the service must shed or defer work through explicit admission control.                                                         | 🟡  | SRE; MS-THROTTLE          |
| R13.7 | Blocking operations must not run on execution contexts reserved for non-blocking work.                                                         | 🟡  | Proposed — house standard |
| R13.8 | Work whose result is invariant across requests must not be recomputed per request on hot paths.                                                | 🟢  | Proposed — house standard |

## 14. Observability

| ID    | Rule                                                                                                        | Sev | Source                    |
| ----- | ----------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R14.1 | Logs must be structured events carrying timestamp, severity, service, and trace or correlation identifier.  | 🟡  | 12F-XI; OTEL              |
| R14.2 | Trace context must propagate across every inbound request, outbound call, and message boundary.             | 🟡  | W3C-TRACE                 |
| R14.3 | Logs, metric labels, and traces must not contain secrets, credentials, tokens, or unredacted personal data. | 🔴  | OWASP-LOG; GDPR-5         |
| R14.4 | Every inbound operation and dependency call must emit latency, traffic, error, and saturation signals.      | 🟡  | SRE                       |
| R14.5 | Metric label values must come from bounded sets.                                                            | 🟡  | PROM-NAMING               |
| R14.6 | An error must be logged once, at the layer that handles it, with cause and context.                         | 🟢  | Proposed — house standard |
| R14.7 | The application must write its event stream to standard output and must not manage log files.               | 🟢  | 12F-XI                    |

## 15. Configuration & Runtime Lifecycle

| ID    | Rule                                                                                                                         | Sev | Source                  |
| ----- | ---------------------------------------------------------------------------------------------------------------------------- | --- | ----------------------- |
| R15.1 | Environment-specific behavior must come from configuration, not code branches on environment name.                           | 🟡  | 12F-III                 |
| R15.2 | Configuration must be validated at startup, and the process must fail to start on missing or invalid values.                 | 🟡  | CONV-ASPNET             |
| R15.3 | Processes must be stateless; state that must survive restart or be shared across instances must live in a backing service.   | 🟡  | 12F-VI                  |
| R15.4 | On termination signal a process must stop accepting work, drain in-flight work within a bound, and release resources.        | 🟡  | 12F-IX; CONV-GO         |
| R15.5 | Liveness and readiness must be separate checks, and liveness must not depend on downstream dependencies.                     | 🟡  | CONV-ASPNET; K8S-PROBES |
| R15.6 | Every feature flag must declare a default, an owner, and a removal condition, and evaluation failure must yield the default. | 🟢  | FOWLER-FLAGS            |
| R15.7 | Diagnostic, debug, and verbose-error modes must be off in the shipped default configuration.                                 | 🔴  | API8                    |

## 16. Schema & Contract Evolution

| ID    | Rule                                                                                                                                                                         | Sev | Source                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R16.1 | Schema changes must be compatible with the previously deployed code version; destructive changes ship in a later, separate migration after no code depends on the old shape. | 🔴  | FOWLER-PARALLEL; DDIA     |
| R16.2 | Migrations on growable stores must not hold locks that block reads or writes for durations proportional to data size.                                                        | 🔴  | Proposed — house standard |
| R16.3 | Migrations must be versioned, ordered, forward-only, and deterministic in every environment.                                                                                 | 🟡  | CONV-RAILS; CONV-DJANGO   |
| R16.4 | Data backfills must be separate from schema migrations and resumable.                                                                                                        | 🟡  | FOWLER-PARALLEL           |
| R16.5 | Within one API version changes must be additive only: no removed or renamed fields, no changed types or semantics, no tightened validation, no new required inputs.          | 🔴  | AIP-180; ZAL; MSREST      |
| R16.6 | Event and message schemas must evolve compatibly: producers never remove or repurpose fields, consumers ignore unknown fields and values.                                    | 🟡  | DDIA; ZAL                 |
| R16.7 | Deprecated endpoints and fields must be marked in the contract and signalled in responses with deprecation and sunset metadata before removal.                               | 🟢  | RFC9745; RFC8594          |

## 17. Service Architecture & Modularity

| ID    | Rule                                                                                                                                          | Sev | Source                      |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------- | --- | --------------------------- |
| R17.1 | Modules containing business rules must not depend on transport, persistence, or delivery-mechanism types; dependencies point inward.          | 🟡  | HEX; CLEAN; SOLID-DIP       |
| R17.2 | Transport handlers must only decode input, invoke a use case, and encode output; they contain no business rules and no direct storage access. | 🟡  | CLEAN; CONV-RAILS           |
| R17.3 | Each module or service owns its data; no other module reads or writes that data except through the owner's interface.                         | 🟡  | MSIO-DBPERSVC; CONV-PHOENIX |
| R17.4 | There must be no dependency cycles between modules and no synchronous call cycles between services.                                           | 🟡  | MARTIN-ADP                  |
| R17.5 | Modules must be accessed only through their public interface; internal types must not be referenced from outside.                             | 🟡  | CONV-PHOENIX; LOD           |
| R17.6 | Separate read and write models or event-sourced state must be justified by a declared divergence in read and write requirements.              | 🟢  | CQRS                        |
| R17.7 | Libraries shared across services must not contain business rules or domain models owned by a single service.                                  | 🟡  | Proposed — house standard   |

## 18. Code Economy & Maintainability

| ID    | Rule                                                                                                                                                                                                       | Sev | Source                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R18.1 | Each business rule must have one authoritative implementation; a third copy of the same knowledge is a violation.                                                                                          | 🟡  | DRY; AHA                  |
| R18.2 | Abstractions with a single implementation and no active extension or test seam are forbidden.                                                                                                              | 🟢  | YAGNI; AHA                |
| R18.3 | Unreachable endpoints, handlers, jobs, and permanently resolved feature-flag branches must not remain in the codebase.                                                                                     | 🟢  | GEP                       |
| R18.4 | Cross-cutting concerns other than authorization and validation (logging, tracing, error mapping, transaction demarcation) must be applied through one shared pipeline mechanism, not repeated per handler. | 🟡  | CONV-ASPNET; CONV-NEST    |
| R18.5 | Variation by country, region, tenant, product, or plan must be expressed as data or configuration, not as duplicated code paths per variant.                                                               | 🟡  | Proposed — house standard |
| R18.6 | Code must not reimplement capabilities already provided by the platform standard library or an existing shared component of the system.                                                                    | 🟡  | GEP                       |
| R18.7 | Business logic must not navigate beyond the interfaces of its immediate collaborators.                                                                                                                     | 🟢  | LOD                       |
| R18.8 | One domain concept must carry one name across contract, code, and storage.                                                                                                                                 | 🟢  | DDD                       |

## 19. Time, Locale & Internationalization

| ID    | Rule                                                                                                                                                                         | Sev | Source                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R19.1 | Instants must be stored and exchanged in UTC with explicit offset in a standard format; local time exists only at presentation.                                              | 🟡  | RFC3339; ISO8601          |
| R19.2 | Calendar-dependent rules (deadlines, business days, billing cycles, day boundaries) must use the governing party's named time zone, never a fixed offset or the server zone. | 🟡  | IANA-TZ                   |
| R19.3 | Business logic must obtain current time, time zone, and locale from an injected source, never read them from the host.                                                       | 🟡  | Proposed — house standard |
| R19.4 | Scheduling and duration logic must define behavior for non-existent and repeated local times.                                                                                | 🟡  | IANA-TZ                   |
| R19.5 | Text must be handled as Unicode end-to-end, and identifiers compared for equality or uniqueness must be normalized and case-folded.                                          | 🟡  | UAX15; ASVS·Validation    |
| R19.6 | User-facing text emitted by the backend must be referenced by message key, not composed from language-specific literals.                                                     | 🟢  | W3C-I18N                  |
| R19.7 | Validation of names, addresses, phone numbers, and postal codes must not assume a single country's format.                                                                   | 🟡  | W3C-NAMES                 |
| R19.8 | Temporal attributes and fields must distinguish instants, local dates, local times, and durations by type or name.                                                           | 🟢  | RFC3339; ISO8601          |

## 20. Testing

| ID    | Rule                                                                                                                                          | Sev | Source                     |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------- | --- | -------------------------- |
| R20.1 | Every changed behavior-bearing unit must have tests covering its success path and each distinct failure path.                                 | 🟡  | GEP                        |
| R20.2 | Every authorization rule must have negative tests proving denial for unauthorized principals and non-owners.                                  | 🟡  | ASVS·Authorization         |
| R20.3 | Tests must not depend on wall-clock time, unseeded randomness, execution order, shared mutable state, or external networks.                   | 🟡  | GOOGLE-FLAKY               |
| R20.4 | Persistence logic relying on constraints, transactions, or non-trivial queries must be tested against the same store type used in production. | 🟡  | Proposed — house standard  |
| R20.5 | Every contract between separately deployed services must be verified by contract tests.                                                       | 🟡  | FOWLER-CONTRACT            |
| R20.6 | Tests must assert observable outcomes, not internal call sequences, unless the interaction is itself the contract.                            | 🟢  | GEP                        |
| R20.7 | Operations guarded for concurrency or idempotency must have tests for duplicate and concurrent execution.                                     | 🟡  | Proposed — house standard  |
| R20.8 | Test data must not contain real personal data or real secrets.                                                                                | 🔴  | GDPR-5; ASVS·Configuration |

## 21. Tenancy & Data Residency _(supplementary)_

| ID    | Rule                                                                                                                                                                      | Sev | Source           |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ---------------- |
| R21.1 | Every access path to tenant-owned data must be scoped by a tenant identifier taken from the verified credential, through a mechanism that individual queries cannot omit. | 🔴  | AWS-SAAS         |
| R21.2 | Queues, object storage, search indexes, and background jobs must carry and enforce tenant scope.                                                                          | 🔴  | AWS-SAAS         |
| R21.3 | Regulated data must be stored, replicated, backed up, and processed only in regions permitted for the data subject's jurisdiction.                                        | 🔴  | GDPR-44; DPDP-16 |
| R21.4 | Consumption of shared resources must be bounded per tenant.                                                                                                               | 🟡  | AWS-SAAS         |
| R21.5 | Cross-tenant operational access must be explicit, time-bounded, and audited.                                                                                              | 🟡  | AWS-SAAS         |

## 22. Privacy & Personal Data Lifecycle _(supplementary)_

| ID    | Rule                                                                                                                                                                                          | Sev | Source                                |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------------------- |
| R22.1 | Every personal data field must be classified with purpose and retention period in the schema or a declared data catalog.                                                                      | 🟡  | GDPR-30; GDPR-5                       |
| R22.2 | Stored personal data must be limited to fields a declared purpose requires.                                                                                                                   | 🟡  | GDPR-5; GDPR-25                       |
| R22.3 | Retention periods must be enforced by an automated deletion or anonymization mechanism.                                                                                                       | 🟡  | GDPR-5; DPDP-8                        |
| R22.4 | Erasure must reach every copy (primary stores, replicas, caches, search indexes, derived stores, outbound processors) within the statutory deadline; soft deletion alone does not satisfy it. | 🔴  | GDPR-17; DPDP-12                      |
| R22.5 | Special-category and high-risk identifiers must be encrypted at field level or tokenized at rest, with access restricted to named roles.                                                      | 🔴  | GDPR-9; GDPR-32; ASVS·Data Protection |
| R22.6 | Consent-based processing must check current, purpose-specific consent before processing and must stop on withdrawal.                                                                          | 🔴  | GDPR-7; DPDP-6                        |
| R22.7 | Personal data sent to third parties must be limited to the declared need and sent only to declared processors.                                                                                | 🟡  | GDPR-28                               |
| R22.8 | A data subject's personal data must be exportable across all stores that hold it.                                                                                                             | 🟡  | GDPR-15; GDPR-20                      |

## 23. Financial Correctness _(supplementary)_

| ID    | Rule                                                                                                                        | Sev | Source                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------- | --- | ------------------------- |
| R23.1 | Monetary amounts must use exact decimal or integer minor-unit representation, never binary floating point.                  | 🔴  | FOWLER-MONEY              |
| R23.2 | Every amount must carry its currency, and arithmetic across currencies without explicit conversion is forbidden.            | 🔴  | FOWLER-MONEY; ISO4217     |
| R23.3 | Minor-unit precision must come from the currency definition, not a fixed assumption.                                        | 🟡  | ISO4217                   |
| R23.4 | Rounding mode and rounding point must be explicit, and splitting or allocation must conserve the total.                     | 🔴  | FOWLER-MONEY              |
| R23.5 | Balances must derive from an append-only, double-entry ledger; no balance changes without a corresponding entry.            | 🔴  | FOWLER-ACCOUNT            |
| R23.6 | Posted financial records must be immutable; corrections are reversing entries.                                              | 🔴  | FOWLER-ACCOUNT            |
| R23.7 | Every conversion must record the rate, its source, and its effective instant with the converted amount.                     | 🟡  | Proposed — house standard |
| R23.8 | Every external payment interaction must store the provider reference and full status lifecycle required for reconciliation. | 🟡  | Proposed — house standard |

## 24. Audit Trail & Regulatory Obligations _(supplementary)_

| ID    | Rule                                                                                                                                                                                                                                                               | Sev | Source                           |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --- | -------------------------------- |
| R24.1 | Security- and business-significant actions (authentication events, permission changes, sensitive-data access, financial postings, administrative actions, exports, deletions) must produce audit records with actor, action, target, instant, origin, and outcome. | 🔴  | ASVS·Logging & Errors; OWASP-LOG |
| R24.2 | Audit records must be append-only and tamper-evident; application identities must hold no update or delete rights on the audit store.                                                                                                                              | 🔴  | NIST-92                          |
| R24.3 | For actions under legal obligation, the audit record must be committed atomically with the action or the action must fail.                                                                                                                                         | 🟡  | Proposed — house standard        |
| R24.4 | Audit records must be stored apart from operational logs with retention matching the legal requirement.                                                                                                                                                            | 🟡  | NIST-92                          |
| R24.5 | Statutory deadlines must be persisted as zoned due instants with automated tracking and escalation, not computed on read.                                                                                                                                          | 🟡  | GDPR-12; GDPR-33                 |
| R24.6 | Records under legal hold must be excluded from automated deletion.                                                                                                                                                                                                 | 🟡  | Proposed — house standard        |
| R24.7 | Audit records must reference sensitive values rather than contain them.                                                                                                                                                                                            | 🟡  | OWASP-LOG                        |

## Positions on contested trade-offs

| Topic                     | Disagreement                                                                                                                     | Position                                                                                | Reason                                                                         |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Versioning                | Path version, header or media-type version, or no versioning.                                                                    | Mechanism is free; R16.5 forbids breaking change within a version.                      | Compatibility is testable; mechanism choice is taste.                          |
| Pagination                | Offset is simple; cursor is stable.                                                                                              | Cursor for growable or mutating collections (R1.5).                                     | Offset degrades linearly and skips or repeats rows under concurrent writes.    |
| Persistence style         | Active-record style couples entities to storage; repository style separates them.                                                | Only modules with business rules must be persistence-independent (R17.1).               | CRUD-only modules gain nothing from extra layers (R18.2).                      |
| Validation location       | Boundary schemas versus domain-model validation.                                                                                 | Both: shape at the boundary (R2.1), invariants in the domain (R2.7) and store (R6.1).   | Each layer sees writers the others miss.                                       |
| DRY versus AHA            | Early abstraction versus tolerated duplication.                                                                                  | Flag the third copy of the same knowledge (R18.1) and single-use abstractions (R18.2).  | Premature abstraction and scattered rules both multiply change cost.           |
| Locking                   | Optimistic versus pessimistic.                                                                                                   | Either is compliant (R8.3); unguarded read-modify-write is not.                         | Contention profile, not doctrine, decides.                                     |
| Service granularity       | Modular monolith versus microservices.                                                                                           | No preference; ownership and acyclicity are mandatory (R17.3, R17.4).                   | Boundary discipline, not deployment topology, determines scalability of teams. |
| Error signalling          | Exceptions versus returned error values.                                                                                         | Out of scope as idiom; only silent conversion to success is ruled (R10.6).              | Idiom is a language concern; swallowed failure is a system concern.            |
| Key strategy              | Sequences are compact but centralize and enumerate; random identifiers scatter index writes; time-ordered identifiers are wider. | 64-bit keys minimum (R6.8); time-ordered for distributed or exposed keys (R6.9, R6.10). | Preserves index locality at scale without a coordination point or enumeration. |
| Natural vs surrogate keys | Natural keys carry meaning; surrogate keys stay stable.                                                                          | Surrogate primary key plus a uniqueness constraint on the natural key (R6.2, R6.11).    | Business identifiers change and differ by country; uniqueness must still hold. |
| Schema naming             | Singular vs plural stores, casing, prefixes.                                                                                     | Any convention, declared once and applied everywhere (R6.18).                           | Consistency is testable; the choice itself is taste.                           |
| Deletion                  | Soft delete preserves history; erasure law demands removal.                                                                      | Soft delete permitted except for personal data under erasure (R22.4).                   | Legal obligation overrides convenience.                                        |
