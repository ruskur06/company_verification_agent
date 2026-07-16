# ADR-001: Strict Pipeline Execution for Approved Check Requests

- Status: Accepted for implementation
- Date: 2026-07-16
- Scope: CheckRequest execution lifecycle, strict CompanyCheck persistence, concurrency control, idempotency, and recovery
- Review status: Phase 0 source inspection completed and independently reviewed twice before implementation

## 1. Context

The project has two separate concepts:

1. The `CheckRequest` lifecycle, controlled by an operator.
2. `CompanyCheck` pipeline execution and result persistence.

The current operator decisions are `pending → approved` and `pending → rejected`. Approval only authorizes execution; it must not automatically run the pipeline. The next feature will add a separate manual Run action for approved requests.

This operation crosses CheckRequest lifecycle persistence, CompanyCheck result persistence, JSON and Markdown report persistence, and external agent and tool execution. It must prevent duplicate starts, stale attempts changing newer attempts, duplicate CompanyChecks, processed requests without durable results, accidental updates of unrelated legacy CompanyChecks, and unsafe retries after uncertain commit outcomes.

## 2. Existing System Facts

### Legacy entry point

The existing service is conceptually:

```text
run_company_check(company_name, country, domain=None)
```

It is used by `/internal/check` and `POST /company-check`.

The existing `CompanyCheckAgent`:

1. validates the request;
2. generates a millisecond timestamp check ID internally;
3. runs agents and external tools;
4. creates `CompanyCheckResult`;
5. writes JSON;
6. writes Markdown;
7. returns `CompanyCheckResponse` with completed status.

The legacy service then calls `save_company_check()`. The legacy service catches and suppresses PostgreSQL persistence exceptions. Therefore, legacy `completed` currently means that pipeline execution completed and report files were written, but it does not guarantee that PostgreSQL persistence completed. The legacy result UI can still work because it reads the JSON report file.

### Existing repository persistence

`save_company_check()`:

- upserts `CompanyCheckRecord` by `check_id`;
- replaces `SourceRecord` rows;
- replaces `ToolCallRecord` rows;
- replaces `ReportRecord` rows;
- commits in one SQLAlchemy transaction;
- rolls back and re-raises repository errors.

This function is an upsert and must not become the public strict persistence contract.

### Existing file persistence

JSON and Markdown are written as two separate non-transactional file operations. Files are not the strict workflow's source of truth.

### Existing schema evolution

The project does not use Alembic. Fresh tables are created through `Base.metadata.create_all()`. Existing PostgreSQL and SQLite schemas are evolved through idempotent startup `_ensure_*` helpers in `app/db/database.py`. There are currently no explicit database `CheckConstraint`s or `ForeignKey`s.

## 3. Decision Summary

The accepted architecture is to:

- preserve the existing legacy `run_company_check()` behavior during this feature;
- introduce a separate strict approved-request execution service;
- add `processing` to `CheckRequestStatus`;
- atomically claim an approved request before invoking the pipeline;
- reserve a numeric check ID before the pipeline starts;
- store the active attempt ID in `processing_check_id`;
- keep `company_check_id` NULL until successful strict persistence;
- use `processing_check_id` as the fencing identifier;
- add `source_check_request_id` to `CompanyCheckRecord`;
- execute agents and file operations outside database transactions;
- prepare and validate all persistence payload and report content before opening the final database transaction;
- use an insert-only strict CompanyCheck repository operation;
- persist the CompanyCheck and finalize the CheckRequest in one short transaction;
- reconcile uncertain outcomes instead of performing blind resets;
- not introduce Alembic as part of this feature.

## 4. CheckRequest State Model

The status values will be `pending`, `approved`, `rejected`, `processing`, and `processed`.

### pending

Waiting for operator review.

### approved

Execution is authorized but has not started.

### rejected

Declined by the operator. This is terminal for the current MVP.

### processing

One execution attempt atomically owns the request. The attempt is identified by `processing_check_id`. The pipeline may be active, interrupted, or awaiting reconciliation.

### processed

The strict final database transaction committed. A real CompanyCheck exists, and the CheckRequest is linked through `company_check_id`. `processed` must never mean only that execution started or that report files exist.

## 5. Allowed State Transitions

Allowed transitions:

- `pending → approved`
- `pending → rejected`
- `approved → processing`
- `processing → processed`
- `processing → approved`

The `processing → approved` transition is allowed only when the system can prove that no strict CompanyCheck was durably committed for the current execution attempt. This proof may come from a failure before the final persistence transaction, a confirmed rollback before commit with no correlation conflict, or reconciliation proving that no strict CompanyCheck exists.

An exception occurring after the final persistence transaction has started is not automatically eligible for release. Constraint conflicts, stale-attempt conflicts, and commit-time uncertainty require classification or reconciliation as defined in Section 16.

All other transitions are rejected. Approval and execution remain separate actions.

## 6. Data Model Changes

### CheckRequestRecord

Add:

- `processing_started_at`: nullable datetime
- `processing_check_id`: nullable `String(64)`

`processing_check_id` must have a unique nullable index. Keep `company_check_id` as nullable `String(64)`.

Invariant while `status = processing`:

```text
processing_check_id IS NOT NULL
company_check_id IS NULL
```

Invariant after successful processing:

```text
processing_check_id IS NULL
company_check_id IS NOT NULL
status = processed
```

Do not use `company_check_id` as a temporary reservation.

### CompanyCheckRecord

Add `source_check_request_id` as a nullable Integer with a unique nullable index.

- Legacy checks: `source_check_request_id = NULL`
- Strict approved-request checks: `source_check_request_id = originating CheckRequest.id`

This field provides durable correlation, database-level idempotency, reconciliation lookup, and an independent defense against duplicate strict results. Conditional finalization with rollback is the primary stale-attempt protection. The unique `source_check_request_id` constraint is an independent database-level idempotency and correlation barrier; it is not the only stale-attempt protection.

### No separate processing_token

Do not add `processing_token` initially. `processing_check_id` identifies the execution attempt and also acts as the fencing value.

Mandatory code invariant: every operation that changes a CheckRequest from `processing` must include:

```text
processing_check_id = expected_processing_check_id
```

in its conditional `WHERE` clause. This includes release, finalization, reconciliation repair, and any future administrative processing-state mutation.

## 7. ForeignKey Decision

Do not add a `ForeignKey` for `source_check_request_id` in this feature because:

- the current project does not use database ForeignKeys;
- service and repository validation are sufficient for the MVP;
- SQLite cannot add an FK constraint to an existing table through a simple `ALTER TABLE ADD COLUMN`;
- adding it would require table reconstruction or a migration framework;
- introducing that infrastructure is outside this feature.

This is a deferred design decision, not a claim that ForeignKeys have no value.

## 8. Check ID Strategy

The strict execution service must generate the check ID before claim and pipeline execution. `CompanyCheckAgent.run()` will later accept an optional supplied `check_id`. When no ID is supplied, legacy behavior remains unchanged.

The preferred strict generator is:

```text
time.time_ns() // 1_000
```

This produces an epoch-microsecond integer. It:

- remains compatible with existing integer schemas and routes;
- fits in `String(64)` after conversion;
- works in existing report filenames;
- has substantially lower collision risk than millisecond timestamps;
- avoids the oversized 128-bit decimal value produced by `uuid4().int`;
- avoids PostgreSQL/SQLite sequence differences.

Database uniqueness remains the final guard. The claim service must implement a small bounded collision retry. A generated ID collision must not be reported as a generic operator error on the first occurrence.

Before the pipeline writes files, the system must ensure that the reserved ID does not already belong to an existing CompanyCheck. This is necessary because file paths are derived from `check_id`; an existing ID could otherwise cause another result's JSON or Markdown files to be overwritten before the database detects the collision.

This pre-check is not a database lock and cannot mathematically prevent every concurrent insertion after the lookup. Under the currently inspected implementation, legacy IDs are epoch-millisecond integers while strict IDs are epoch-microsecond integers. At the current epoch, these generators occupy operationally distinct numeric ranges, substantially reducing collision risk between the current legacy and strict paths. The existing CompanyCheck lookup, unique database constraints, and bounded collision retry remain required. This range-separation assumption must be reviewed if the legacy ID generator changes or another writer can create arbitrary check IDs. The remaining concurrency gap is an accepted residual MVP risk and does not justify holding a long database transaction while report files, external tools, and pipeline operations run.

The claim/repository design must distinguish a request that is not eligible for claim from a generated processing ID collision.

## 9. Atomic Claim

The strict service generates a candidate `processing_check_id`. Before pipeline execution, one short database transaction atomically changes `approved → processing`:

```sql
UPDATE check_request_records
SET status = 'processing',
    processing_started_at = :started_at,
    processing_check_id = :processing_check_id
WHERE id = :request_id
  AND status = 'approved'
  AND company_check_id IS NULL
  AND processing_check_id IS NULL
```

The pipeline may run only when exactly one row was updated. If rowcount is zero, the pipeline must not run. The claim transaction commits before external work begins.

The claim flow must also protect against duplicate `processing_check_id` values and a `processing_check_id` already used as an existing `CompanyCheck.check_id`. Use bounded generation retry only for an ID collision. Do not retry when the CheckRequest itself is not eligible.

## 10. Pipeline Execution Boundary

The pipeline runs outside every database transaction. No session, row lock, transaction, or connection may remain open while running:

- DNS operations;
- registry lookups;
- web search;
- HTTP calls;
- agent execution;
- report generation;
- report file writes.

`CompanyCheckAgent` must not know about CheckRequest lifecycle rules. Its only related extension is accepting an optional supplied `check_id`.

Prefer a reusable service-level execution helper, conceptually:

```text
execute_company_check_pipeline(..., check_id=None)
```

The helper invokes `CompanyCheckAgent`, accepts an optional supplied ID, and performs no database persistence. The legacy path may use it and then perform tolerant legacy persistence. The strict path may use it with `processing_check_id` and then perform strict persistence. Do not duplicate the full agent invocation logic. Do not expose the private global agent directly through routes.

## 11. Input Mapping

For strict execution, map:

- `CheckRequest.company_name → pipeline company_name`
- `CheckRequest.country → pipeline country`
- `CheckRequest.website → pipeline domain`

The following remain request metadata and must not silently become evidence, ownership signals, or risk inputs:

- `email`
- `transaction_type`
- `additional_context`
- `preferred_language`

## 12. Persistence Payload Preparation

Before opening the final database transaction:

- validate `CompanyCheckResponse`;
- validate the result payload;
- prepare all CompanyCheck fields;
- prepare `SourceRecord` data;
- prepare `ToolCallRecord` data;
- read and validate JSON report content;
- read and validate Markdown report content;
- prepare `ReportRecord` data.

No disk I/O may occur during the final strict database transaction. A file-read or payload-preparation error is a pre-persistence failure. Report-file content is prepared before the strict database transaction.

## 13. Strict Insert-Only Persistence

The strict path must not use legacy `save_company_check()` as its final public repository contract because that function is an upsert. The strict operation must be insert-only.

Conceptually add a narrow repository operation:

```text
persist_company_check_and_finalize_request(
    request_id,
    processing_check_id,
    prepared_company_check_payload,
)
```

It must not update an existing `CompanyCheckRecord`. If `check_id` already exists, strict persistence must not replace related records or modify that CompanyCheck.

Shared low-level mapping helpers may be extracted for reuse by legacy and strict persistence, but the public repository contracts must remain separate.

## 14. Final Strict Transaction

Use one short transaction for strict CompanyCheck insertion and CheckRequest finalization. Do not use `SELECT ... FOR UPDATE` as the fencing mechanism. The design must work consistently with PostgreSQL and SQLite tests.

The operation order must be:

1. Add a new `CompanyCheckRecord` with:
   - `check_id = processing_check_id`
   - `source_check_request_id = request_id`
2. Add related `SourceRecord` rows.
3. Add related `ToolCallRecord` rows.
4. Add the `ReportRecord`.
5. Call `session.flush()` explicitly.

The explicit flush is mandatory because the project configures SQLAlchemy sessions with `autoflush=False`. The flush must send INSERT statements and expose deterministic insert or constraint failures before commit.

6. Perform exactly one conditional UPDATE of CheckRequest, and perform it last:

```sql
UPDATE check_request_records
SET status = 'processed',
    company_check_id = :processing_check_id,
    processing_check_id = NULL,
    processing_started_at = NULL
WHERE id = :request_id
  AND status = 'processing'
  AND processing_check_id = :processing_check_id
  AND company_check_id IS NULL
```

7. Check that rowcount equals exactly one.
8. If rowcount is zero:
   - raise a typed stale-attempt or finalization-conflict exception;
   - explicitly roll back the transaction;
   - ensure all CompanyCheck, SourceRecord, ToolCallRecord, and ReportRecord inserts are rolled back.
9. If rowcount is one, call `commit()` separately.

There must not be a preliminary CheckRequest state update inside this transaction. There must be only one conditional `processing → processed` update. It must be the last database mutation before commit. Rowcount zero rolls back the full strict transaction.

## 15. Why the Final Conditional Update Is Last

An early eligibility check is insufficient without row locking. `SELECT FOR UPDATE` is not the cross-database fencing strategy. Another operation may change the request after an early check.

The final conditional UPDATE detects that the attempt no longer owns the request. Rowcount zero causes rollback of all inserts in the same transaction. This prevents a stale attempt from committing an unlinked CompanyCheck.

## 16. Failure Classification

### Safe pre-persistence failure

Failures before the final strict database transaction starts include:

- validation;
- agent execution;
- DNS;
- registry;
- web search;
- risk calculation;
- JSON write;
- Markdown write;
- report content reading;
- payload preparation.

No strict CompanyCheck database record has been attempted. The service may perform a guarded `processing → approved` release. The release must require the same `processing_check_id`, clear `processing_check_id` and `processing_started_at`, and leave `company_check_id` NULL.

### Deterministic pre-commit transaction failure

These failures are produced during INSERT execution, `session.flush()`, the final conditional UPDATE, or rowcount validation, before `commit()` is called. The transaction must be rolled back.

Do not classify all such errors identically:

- a normal deterministic payload or insert failure with confirmed rollback may allow guarded release;
- a `source_check_request_id` uniqueness conflict may indicate that a durable result already exists and must trigger reconciliation;
- a `check_id` uniqueness conflict must determine whether the ID belongs to this request or an unrelated result;
- rowcount zero indicates stale ownership or a conflicting lifecycle transition and must not allow the stale attempt to change the request.

Guarded release after a pre-commit failure is allowed only when all of the following are true:

- rollback is confirmed;
- `commit()` was not called;
- no durable correlated CompanyCheck exists;
- the failure is not a stale-ownership rowcount conflict;
- the failure is not a `source_check_request_id` conflict or an unclear `check_id` conflict requiring reconciliation.

Do not automatically release every `IntegrityError`.

### Ambiguous commit outcome

An exception directly on or around `session.commit()` may have an uncertain durable outcome, such as connection loss while waiting for commit acknowledgement, driver timeout during commit acknowledgement, or process/server interruption at commit.

The strict service must not automatically release the request. Leave it `processing` until reconciliation determines the durable state. Commit handling must remain distinguishable from execute/flush handling.

## 17. Guarded Release

A release operation conceptually performs:

```sql
UPDATE check_request_records
SET status = 'approved',
    processing_check_id = NULL,
    processing_started_at = NULL
WHERE id = :request_id
  AND status = 'processing'
  AND processing_check_id = :expected_processing_check_id
  AND company_check_id IS NULL
```

The release succeeds only when rowcount is exactly one. A stale attempt must not release a newer attempt.

## 18. Reconciliation

Blind reset is forbidden. Reconciliation examines durable state using:

- `CompanyCheckRecord.source_check_request_id`;
- `CompanyCheckRecord.check_id`;
- `CheckRequest.processing_check_id`;
- `CheckRequest.company_check_id`;
- `CheckRequest.status`.

### Correlated CompanyCheck exists

Treat the durable CompanyCheck as authoritative. If the CheckRequest link is inconsistent due to historical, manual, or abnormal state, repair it through a guarded conditional operation. The repair must include the expected `processing_check_id` when changing a processing request.

### No correlated CompanyCheck exists

After the request exceeds a configured staleness threshold, perform a guarded release to `approved`.

### Durable state cannot be determined

Leave the request `processing`. Do not perform automatic retry.

With the new atomic strict transaction, the normal successful state should expose both the CompanyCheck and the processed and linked CheckRequest. A state where the CompanyCheck exists while the request remains processing is an abnormal or historical recovery case, not the expected normal result of the new transaction.

No background worker is required for the MVP. A manual internal reconciliation action may be implemented after strict persistence exists. The staleness threshold is deliberately not fixed in this ADR.

## 19. Startup Schema Evolution

Do not introduce Alembic in this feature. Continue using SQLAlchemy model definitions for fresh databases and idempotent startup `_ensure_*` helpers for existing databases.

Required additions:

- `check_request_records.processing_started_at`
- `check_request_records.processing_check_id`
- `company_check_records.source_check_request_id`

Required indexes:

- unique nullable index on `check_request_records.processing_check_id`
- unique nullable index on `company_check_records.source_check_request_id`

The startup helpers must inspect and create both missing columns and missing indexes. Do not assume that adding a column creates a unique index on an existing table. For PostgreSQL, use idempotent index creation or inspect existing indexes first. For SQLite, create the unique index as a separate operation. The helpers must be safe when run repeatedly.

Tests must verify fresh database creation, evolution of an existing database, repeated `init_db` calls, rejection of duplicate non-NULL values, and allowance of multiple NULL values. A manual PostgreSQL migration and index-enforcement verification is required before merge.

## 20. Service and Repository Boundaries

The future route:

```text
POST /internal/requests/{request_id}/run
```

must call only the strict approved-request service. The route must not access SQLAlchemy directly, claim requests, invoke `CompanyCheckAgent` directly, persist CompanyChecks, or implement lifecycle transitions.

The strict service owns eligibility orchestration, numeric ID generation, bounded collision retry, atomic claim, CheckRequest-to-pipeline input mapping, pipeline invocation, payload preparation, safe guarded release, strict persistence orchestration, failure classification, and typed exception translation.

Repositories own short atomic persistence operations. `CompanyCheckAgent` owns verification execution only.

## 21. Planned Repository Contracts

Conceptual narrow operations:

```text
claim_check_request_for_processing(
    request_id,
    processing_check_id,
    started_at,
)
```

It returns claimed request data or a typed failure that distinguishes not found, not eligible, and generated ID collision.

```text
release_check_request_processing(
    request_id,
    processing_check_id,
)
```

It performs a guarded release.

```text
persist_company_check_and_finalize_request(
    request_id,
    processing_check_id,
    prepared_payload,
)
```

It performs insert-only strict persistence, explicit flush, final conditional update, rowcount validation, and commit.

```text
find_company_check_by_source_request_id(request_id)
```

It supports idempotency and reconciliation.

```text
find_company_check_by_check_id(check_id)
```

It supports collision protection and reconciliation.

Do not add a generic arbitrary-field update method.

## 22. Required Invariants

1. Approval does not execute the pipeline.
2. Pipeline execution requires a successful atomic claim.
3. `company_check_id` remains NULL during processing.
4. `processing_check_id` is generated anew for each attempt.
5. Every mutation from processing checks the expected `processing_check_id` in `WHERE`.
6. External work occurs outside database transactions.
7. Strict persistence is insert-only.
8. File and payload preparation occurs before the final transaction.
9. `session.flush()` occurs before the final CheckRequest update.
10. The `processing → processed` UPDATE is the final database mutation before commit.
11. Rowcount must equal one.
12. Rowcount zero rolls back every strict insert.
13. `processed` is committed atomically with the strict CompanyCheck.
14. One CheckRequest may create at most one strict CompanyCheck.
15. Uncertain commit outcomes are reconciled, not blindly retried.
16. Legacy persistence semantics remain unchanged during this feature.

## 23. Testing Requirements

Required tests cover:

- `processing` enum support;
- model schema on a fresh database;
- startup migration of existing tables;
- idempotent index creation;
- unique `processing_check_id` enforcement;
- unique `source_check_request_id` enforcement;
- multiple NULL values allowed by both indexes;
- supplied check ID used by `CompanyCheckAgent`;
- unchanged legacy ID generation when no ID is supplied;
- collision retry for processing ID generation;
- protection against an existing CompanyCheck `check_id` before file writes;
- successful claim from `approved`;
- failed claim from `pending`;
- failed claim from `rejected`;
- failed claim from `processed`;
- failed repeated claim from `processing`;
- claim failure when `company_check_id` is already set;
- guarded release requiring matching `processing_check_id`;
- stale attempt unable to release a newer attempt;
- strict persistence inserts rather than upserts;
- explicit flush detects deterministic constraint failures;
- final conditional UPDATE is last;
- rowcount zero rolls back CompanyCheck and all related inserts;
- `processed` is never committed without a CompanyCheck;
- a strict CompanyCheck is never committed without processed linkage;
- `source_check_request_id` blocks two strict results for one request;
- `check_id` collision does not update an unrelated CompanyCheck;
- pre-persistence failure performs guarded release;
- source correlation conflict triggers reconciliation rather than automatic release;
- commit-time ambiguous failure leaves the request processing;
- stale or zombie attempt cannot finalize after a newer attempt;
- reconciliation racing with a live attempt remains protected by conditional updates;
- repeated Run POST does not invoke the pipeline twice;
- legacy run behavior remains unchanged;
- refresh behavior remains unchanged;
- one manual PostgreSQL end-to-end verification before merge.

Do not require flaky real-time multithreaded tests when deterministic sequential claim and stale-attempt simulations can test the invariant.

## 24. Implementation Commit Plan

### Commit 1 — ADR only

- create this ADR;
- no production code;
- no tests.

### Commit 2 — Lifecycle schema

- add `processing` enum;
- add `processing_started_at`;
- add `processing_check_id`;
- add `source_check_request_id`;
- add model indexes;
- add startup column helpers;
- add startup unique-index helpers;
- add SQLite schema and index tests.

### Commit 3 — Shared execution and supplied ID

- optional `check_id` accepted by `CompanyCheckAgent`;
- reusable execution helper without persistence;
- legacy behavior unchanged;
- regression tests.

### Commit 4 — Atomic claim and guarded release

- epoch-microsecond ID generation;
- bounded collision retry;
- protection against existing CompanyCheck IDs;
- claim repository operation;
- release repository operation;
- typed claim failures;
- fencing and rowcount tests.

No real approved-request pipeline execution yet.

### Commit 5 — Strict insert-only persistence

- prepare file and result payload outside the transaction;
- insert-only CompanyCheck persistence;
- source correlation;
- related-record inserts;
- explicit `session.flush()`;
- final conditional UPDATE last;
- mandatory rowcount validation;
- separate commit handling;
- rollback, idempotency, and stale-attempt tests.

### Commit 6 — Strict execution service

- claim;
- execute pipeline with `processing_check_id`;
- prepare persistence payload;
- guarded release for safe failures;
- reconciliation path for correlation conflicts;
- ambiguous commit classification;
- mocked pipeline tests.

### Commit 7 — Internal Run route and UI

- `POST /internal/requests/{request_id}/run`;
- HTTP 303 redirect;
- Run button only for eligible approved requests;
- service-side enforcement remains authoritative;
- processing and error display.

### Commit 8 — Reconciliation

- correlation lookup;
- stale diagnostics;
- guarded repair to processed;
- guarded release when no durable result exists;
- no blind reset.

### Commit 9 — End-to-end validation

- complete regression suite;
- PostgreSQL startup migration verification;
- PostgreSQL unique-index enforcement;
- browser workflow:
  `public submit → approve → run → processing → processed → linked CompanyCheck result`.

## 25. Consequences

Positive consequences:

- duplicate Run actions cannot both start execution;
- stale attempts cannot mutate newer attempts;
- `processed` has a durable meaning;
- strict CompanyCheck persistence and request finalization are atomic;
- strict execution cannot overwrite an unrelated CompanyCheck through upsert;
- one request cannot create two strict results;
- recovery uses durable correlation;
- legacy compatibility is preserved;
- external work does not hold database transactions.

Negative consequences:

- additional processing and correlation fields;
- more complex startup schema helpers;
- strict and legacy persistence remain temporarily different;
- collision checking adds repository logic;
- failed attempts may leave orphaned files;
- reconciliation adds an operator workflow;
- the project still lacks a general migration framework.

## 26. Rejected Alternatives

The following alternatives are rejected or deferred:

- starting the pipeline directly after approval;
- only checking eligibility after the pipeline;
- storing the reservation in `company_check_id`;
- adding a separate `processing_token` now;
- keeping a transaction open across external work;
- using `SELECT FOR UPDATE` as the cross-database fencing mechanism;
- using legacy `save_company_check()` for strict persistence;
- automatically releasing every exception;
- blind stale reset;
- adding a `FAILED` CheckRequest status now;
- changing legacy `run_company_check()` behavior now;
- using `uuid4().int` for strict IDs;
- using a database sequence for strict IDs;
- adding ForeignKeys now;
- introducing Alembic during this feature.

## 27. Non-Goals

This ADR does not design or add:

- background queues;
- automatic retry workers;
- customer email delivery;
- user accounts;
- payments;
- a full authentication system;
- investment scoring;
- orphan-file cleanup;
- global ForeignKey refactoring;
- replacement of the legacy workflow;
- general migration-framework adoption.

## 28. Implementation Gate

Cursor must not implement the real Run route or invoke the real approved-request pipeline until all of the following exist and are tested:

- processing lifecycle schema;
- startup column and unique-index evolution;
- supplied check ID support;
- existing CompanyCheck ID collision protection;
- atomic claim;
- guarded release;
- `processing_check_id` fencing;
- `source_check_request_id` uniqueness;
- strict insert-only persistence;
- explicit flush;
- final conditional update with mandatory rowcount check;
- atomic CompanyCheck persistence and CheckRequest finalization.

The first code commit after this ADR must be lifecycle-schema-only.
