# Sources

Citation keys used in `rules.md`. One line per source on what was taken. Rules marked
"Proposed — house standard" have no external source.

## Security standards

| Key            | Source                                                                          | Taken                                                                                                                                                            |
| -------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ASVS·{chapter} | OWASP Application Security Verification Standard 5.0 (chapters cited by name)   | Verifiable requirements for validation, authentication, session, tokens, authorization, cryptography, files, configuration, data protection, logging and errors. |
| API1–API10     | OWASP API Security Top 10 (2023)                                                | Object-, property-, and function-level authorization; resource consumption; business-flow abuse; SSRF; misconfiguration; inventory; unsafe consumption.          |
| OWASP-TOP10    | OWASP Top 10 (2021), A03 Injection                                              | Injection as a class across interpreters.                                                                                                                        |
| OWASP-PWD      | OWASP Password Storage Cheat Sheet                                              | Adaptive, salted password hashing only.                                                                                                                          |
| OWASP-SSRF     | OWASP SSRF Prevention Cheat Sheet                                               | Destination allow-lists, internal and metadata range blocking, redirect handling.                                                                                |
| OWASP-DESER    | OWASP Deserialization Cheat Sheet                                               | No type-instantiating deserialization of untrusted input.                                                                                                        |
| OWASP-REDOS    | OWASP Regular Expression Denial of Service                                      | Catastrophic backtracking as a resource attack.                                                                                                                  |
| OWASP-IDOR     | OWASP Insecure Direct Object Reference Prevention Cheat Sheet                   | Non-enumerable external identifiers as defense in depth behind authorization.                                                                                    |
| OWASP-LOG      | OWASP Logging Cheat Sheet                                                       | Audit event fields; exclusion of secrets and personal data from logs.                                                                                            |
| NIST-63B       | NIST SP 800-63B Digital Identity Guidelines                                     | Password storage and attempt-limiting requirements.                                                                                                              |
| NIST-207       | NIST SP 800-207 Zero Trust Architecture                                         | Network location is not identity; authenticate every workload.                                                                                                   |
| NIST-92        | NIST SP 800-92 Guide to Computer Security Log Management                        | Integrity protection, separation, and retention of security logs.                                                                                                |
| RFC8725        | RFC 8725 JSON Web Token Best Current Practices                                  | Fixed algorithms; issuer, audience, and expiry validation.                                                                                                       |
| SALTZER        | Saltzer & Schroeder, "The Protection of Information in Computer Systems" (1975) | Fail-safe defaults: deny unless explicitly permitted.                                                                                                            |

## HTTP and API design

| Key                             | Source                                                     | Taken                                                                                                                             |
| ------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| RFC9110                         | RFC 9110 HTTP Semantics                                    | Method safety and idempotency; status code classes; replacement semantics.                                                        |
| RFC9111                         | RFC 9111 HTTP Caching                                      | Secondary cache keys by response variance; private and no-store directives.                                                       |
| RFC9457                         | RFC 9457 Problem Details for HTTP APIs                     | Single machine-readable error format with a stable type.                                                                          |
| RFC6585                         | RFC 6585 Additional HTTP Status Codes                      | Too-many-requests signalling with retry hints.                                                                                    |
| RFC7396                         | RFC 7396 JSON Merge Patch                                  | Explicit partial-update semantics for absent versus null.                                                                         |
| RFC8594                         | RFC 8594 The Sunset HTTP Header Field                      | Signalling planned removal.                                                                                                       |
| RFC9745                         | RFC 9745 The Deprecation HTTP Response Header Field        | Signalling deprecation in responses.                                                                                              |
| IETF-IDEM                       | IETF HTTPAPI draft "The Idempotency-Key HTTP Header Field" | Client-supplied idempotency keys for non-idempotent methods.                                                                      |
| GAPI                            | Google API Design Guide                                    | Consistent resource and field naming.                                                                                             |
| AIP-151, -155, -158, -180, -193 | Google API Improvement Proposals                           | Long-running operations; request identification; pagination with max size and opaque tokens; backward compatibility; error model. |
| MSREST                          | Microsoft REST API Guidelines                              | Long-running operations; versioning and breaking-change definition.                                                               |
| ZAL                             | Zalando RESTful API Guidelines                             | Contract-first APIs, problem format, cursor pagination, compatibility rules, tolerant reader.                                     |
| STRIPE-IDEM                     | Stripe API reference, Idempotent Requests                  | Keyed replay of original outcome; key scoping.                                                                                    |

## Operations and reliability

| Key                                                               | Source                                                                                        | Taken                                                                                                                                 |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 12F-III, -VI, -IX, -XI                                            | The Twelve-Factor App (Config, Processes, Disposability, Logs)                                | Config in environment; stateless processes; graceful shutdown; logs as event streams.                                                 |
| SRE                                                               | Google, _Site Reliability Engineering_ (chapters on monitoring, overload, cascading failures) | Golden signals; bounded resources; load shedding; deadline propagation; graceful degradation.                                         |
| AWS-RETRY                                                         | AWS Builders' Library, "Timeouts, retries, and backoff with jitter"                           | Mandatory timeouts; bounded jittered retries; single-layer retry.                                                                     |
| AWS-IDEM                                                          | AWS Builders' Library, "Making retries safe with idempotent APIs"                             | Atomic recording of idempotency tokens with effects; safe re-runs.                                                                    |
| AWS-SAAS                                                          | AWS Well-Architected SaaS Lens                                                                | Tenant isolation models; tenant context from identity; noisy-neighbor controls; audited cross-tenant access.                          |
| MS-BREAKER, MS-BULKHEAD, MS-CACHE, MS-QLOAD, MS-SAGA, MS-THROTTLE | Microsoft Azure Architecture Center, Cloud Design Patterns                                    | Circuit breaker; bulkhead; cache-aside and stampede control; queue-based load leveling with dead-lettering; compensation; throttling. |
| OTEL                                                              | OpenTelemetry specification and semantic conventions                                          | Structured, correlated telemetry.                                                                                                     |
| W3C-TRACE                                                         | W3C Trace Context                                                                             | Trace propagation across process boundaries.                                                                                          |
| PROM-NAMING                                                       | Prometheus documentation, Metric and label naming                                             | Bounded label cardinality.                                                                                                            |
| K8S-PROBES                                                        | Kubernetes documentation, Liveness, Readiness and Startup Probes                              | Separate liveness and readiness; liveness independent of dependencies.                                                                |
| CLOUDEVENTS                                                       | CNCF CloudEvents specification                                                                | Message identifier, type/version, and correlation metadata.                                                                           |

## Data and distributed systems

| Key                                           | Source                                                                                | Taken                                                                                                                                                                                             |
| --------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DDIA                                          | Kleppmann, _Designing Data-Intensive Applications_                                    | Isolation anomalies; constraints across writers; replication lag; ordering; schema compatibility; dual-write hazards; partitioning and hot spots; derived data rebuilt from the system of record. |
| KLEPPMANN-LOCK                                | Kleppmann, "How to do distributed locking" (2016)                                     | Leases require fencing tokens.                                                                                                                                                                    |
| DBGUIDE                                       | Established relational design guidance (Codd normal forms; Date, _Database in Depth_) | Stable surrogate keys with natural-key uniqueness; matching reference types; association uniqueness; declared referential actions; consistent naming; lock ordering.                              |
| WINAND                                        | Winand, _SQL Performance Explained_ / "Use The Index, Luke"                           | Index support for filters, joins, sorts; sargable predicates; column projection.                                                                                                                  |
| MSIO-OUTBOX, MSIO-IDEMCONSUMER, MSIO-DBPERSVC | Richardson, microservices.io patterns                                                 | Transactional outbox; idempotent consumer; database per service.                                                                                                                                  |
| FOWLER-PARALLEL                               | Sato/Fowler, "Parallel Change" (bliki)                                                | Expand and contract for schema and contract changes.                                                                                                                                              |
| CQRS                                          | Fowler, "CQRS" (bliki)                                                                | Separate models only where read and write needs diverge.                                                                                                                                          |
| FOWLER-MONEY                                  | Fowler, _Patterns of Enterprise Application Architecture_, Money                      | Exact amounts with currency; conserving allocation.                                                                                                                                               |
| FOWLER-ACCOUNT                                | Fowler, _Analysis Patterns_, accounting patterns                                      | Append-only double-entry; reversing entries.                                                                                                                                                      |

## Design principles

| Key             | Source                                                            | Taken                                                                                  |
| --------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| HEX             | Cockburn, "Hexagonal Architecture"                                | Core independent of delivery and persistence adapters.                                 |
| CLEAN           | Martin, _Clean Architecture_                                      | Inward dependency rule; thin delivery layer.                                           |
| SOLID-DIP       | Martin, SOLID principles                                          | Dependency inversion at module boundaries.                                             |
| MARTIN-ADP      | Martin, Acyclic Dependencies Principle                            | No dependency cycles between modules.                                                  |
| LOD             | Lieberherr & Holland, Law of Demeter (1989)                       | Talk only to immediate collaborators; encapsulated module internals.                   |
| DDD             | Evans, _Domain-Driven Design_                                     | Bounded contexts; ubiquitous language; domain-layer invariants.                        |
| DRY             | Hunt & Thomas, _The Pragmatic Programmer_                         | One authoritative representation of each piece of knowledge.                           |
| AHA             | Dodds, "AHA Programming"; Metz, "The Wrong Abstraction"           | Tolerate duplication until the third instance; avoid premature abstraction.            |
| YAGNI           | Fowler, "Yagni" (bliki)                                           | No speculative generality.                                                             |
| FOWLER-FLAGS    | Hodgson, "Feature Toggles" (martinfowler.com)                     | Flag ownership, defaults, and removal.                                                 |
| FOWLER-CONTRACT | Fowler, "ContractTest" (bliki)                                    | Contract tests between separately deployed services.                                   |
| GEP             | Google Engineering Practices, "What to look for in a code review" | Tests accompany changes; no over-engineering; no reinvention; explicit error handling. |
| GOOGLE-FLAKY    | Google Testing Blog, flaky-test series                            | Sources of non-determinism in tests.                                                   |

## Time, text, and money standards

| Key               | Source                                          | Taken                                                                                 |
| ----------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| RFC3339 / ISO8601 | RFC 3339; ISO 8601                              | Unambiguous timestamps with offset; distinct instant, date, time, and duration forms. |
| RFC9562           | RFC 9562 Universally Unique IDentifiers         | Time-ordered identifiers for index locality without central coordination.             |
| IANA-TZ           | IANA Time Zone Database                         | Named zones; transition gaps and overlaps.                                            |
| UAX15             | Unicode Standard Annex #15, Normalization Forms | Normalization before identifier comparison.                                           |
| W3C-I18N          | W3C Internationalization best practices         | Message externalization for localization.                                             |
| W3C-NAMES         | W3C, "Personal names around the world"          | No single-country assumptions for names and addresses.                                |
| ISO4217           | ISO 4217 Currency codes                         | Currency identity and minor-unit precision.                                           |

## Privacy and regulation

| Key                                                              | Source                                                          | Taken                                                                                                                                                                                                   |
| ---------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GDPR-5, -7, -9, -12, -15, -17, -20, -25, -28, -30, -32, -33, -44 | EU General Data Protection Regulation, Articles cited           | Principles; consent; special categories; response deadlines; access; erasure; portability; privacy by design; processors; records of processing; security; breach notification; cross-border transfers. |
| DPDP-6, -8, -12, -16                                             | India Digital Personal Data Protection Act 2023, Sections cited | Consent; fiduciary obligations and retention; correction and erasure; cross-border transfer.                                                                                                            |

## Framework conventions (transferable ideas only)

| Key          | Source                                                                                                                 | Taken                                                                                                                                                         |
| ------------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CONV-DJANGO  | Django documentation (query optimization, CSRF, migrations, model timestamps)                                          | Batched related loading and bulk writes; default forgery protection; versioned migrations; audit timestamps; reference-attribute naming convention.           |
| CONV-RAILS   | Ruby on Rails Guides (Strong Parameters, Active Record validations, migrations, eager loading)                         | Writable-field allow-lists; uniqueness requires a store constraint; thin controllers; ordered migrations; bulk writes; reference-attribute naming convention. |
| CONV-SPRING  | Spring Framework and Spring Security reference                                                                         | Transactions at the service layer; no remote calls inside transactions; deny-by-default security.                                                             |
| CONV-ASPNET  | ASP.NET Core documentation (ProblemDetails, options validation, health checks, policy-based authorization, middleware) | Uniform errors; fail-fast config; separate health probes; centralized policies; pipeline cross-cutting concerns.                                              |
| CONV-NEST    | NestJS documentation (validation pipes, guards, interceptors, DTOs)                                                    | Reject unknown fields; guard-based authorization; interceptor cross-cutting; explicit output models.                                                          |
| CONV-LARAVEL | Laravel documentation (form requests, policies, mass assignment, queues)                                               | Request-level validation; policy objects; fillable allow-lists; retry limits and failed-job handling.                                                         |
| CONV-PHOENIX | Phoenix contexts and Ecto changesets documentation                                                                     | Context-owned data behind public interfaces; domain validation mapped to store constraints.                                                                   |
| CONV-GO      | Go standard library (`context`, `net/http` server timeouts and Shutdown), Go error-handling guidance                   | Deadline and cancellation propagation; explicit timeouts; graceful shutdown; errors not silently discarded.                                                   |
