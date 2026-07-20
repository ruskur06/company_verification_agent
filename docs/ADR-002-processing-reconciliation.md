# ADR-002: Operator-Driven Reconciliation for Stale Processing Requests

- Status: Accepted for implementation
- Date: 2026-07-20
- Scope: Diagnosis and safe reconciliation of CheckRequests left in `status = processing` after an approved-request run attempt
- Depends on: ADR-001 approved-request pipeline (merged happy path)
- Review status: Documentation-only decision record before diagnosis schemas and audit model

## 1. Context

The approved-request happy path is complete and merged into main. The current operator workflow is:

```text
public request
→ pending
→ manual approval
→ approved
→ manual Run check
→ atomic claim
→ processing
→ pipeline execution
→ report artifacts
→ strict fenced persistence
→ processed
→ result UI
```

The atomic claim reserves `processing_check_id` as both:

1. the future `CompanyCheck.check_id`;
2. the fencing token for the current attempt.

A crash or exception after claim can intentionally leave the request in `processing`. This is not a schema defect. ADR-001 already requires that uncertain post-claim outcomes are reconciled rather than blindly reset.

JSON and Markdown report artifacts are non-transactional and can survive database failures. Forensic artifacts must never be silently deleted. Automatic retry, automatic reset, automatic cleanup, and startup recovery are out of scope for this branch.

## 2. Existing System Facts

### Durable claim fields

While `status = processing`:

```text
processing_check_id IS NOT NULL
company_check_id IS NULL
processing_started_at IS NOT NULL
```

After successful strict finalization:

```text
status = processed
company_check_id = former processing_check_id
processing_check_id IS NULL
processing_started_at IS NULL
```

### Strict persistence contract

The successful path inserts:

- one `CompanyCheckRecord` with matching `check_id` and `source_check_request_id`;
- related `SourceRecord` rows (zero or more);
- related `ToolCallRecord` rows;
- one `ReportRecord`;
- then performs a fenced `processing → processed` update in the same transaction.

A legitimate completed check may find zero sources. `SourceRecord` count is therefore not a finalize precondition.

### Current tool-call evidence

For the current persistence version, when `CompanyCheckResult` has no explicit `tool_calls` field, strict persistence inserts four default tool calls. Safe finalize requires that exact multiset:

- `web_search` exactly once;
- `domain_dns_check` exactly once;
- `registry_search` exactly once;
- `risk_score` exactly once.

Missing, duplicate, additional, or unknown tool-call rows block finalize. This rule will require versioning when explicit tool-call manifests are introduced.

### Expected report paths

Expected filesystem paths are derived only from:

```text
json_path_for_check(processing_check_id)
markdown_path_for_check(processing_check_id)
```

Stored DB path strings are comparison facts only. They must not be trusted as arbitrary filesystem read paths.

### Unauthenticated internal MVP

Internal operator routes are currently unauthenticated. This is an accepted local/internal MVP limitation, not a security design. Authentication for `/internal/*` is a high-priority follow-up and is outside this branch.

## 3. Decision Summary

The system will add an explicit, operator-driven reconciliation workflow.

It must:

- diagnose `processing` requests conservatively before offering any action;
- keep diagnosis read-only;
- never mutate state from a GET page;
- offer mutation only through an explicit POST finalize action;
- fence every finalize mutation with an explicit expected `processing_check_id`;
- require a minimal audit table before shipping finalize;
- recheck all authoritative DB and filesystem facts immediately before finalize;
- leave unsafe or incomplete states for operator inspection without automatic repair.

No automatic retry, reset, cleanup, cron, background worker, or startup reconciliation is included.

## 4. Diagnosis Taxonomy

Diagnosis produces either a successful classification or a diagnosis error/result. Diagnosis failure is not a request classification and must be represented separately.

### Classification precedence

Apply classifications in this order:

1. diagnosis error if mandatory facts cannot be obtained;
2. `processing_inconsistent` if facts are readable but structurally conflict;
3. `within_processing_window` only when processing invariants are coherent and age is below `stale_after`;
4. stale classifications only for coherent requests whose age exceeds `stale_after`.

`within_processing_window` must never hide contradictory request, ownership, evidence, or artifact facts.

### Successful classifications

#### `within_processing_window`

- request status is `processing`;
- `processing_started_at` is present;
- processing invariants are coherent;
- age is below `stale_after`;
- no claim is made that execution is alive;
- no reconciliation action is available.

Age alone never authorizes mutation. The application has no heartbeat or execution lease. `within_processing_window` means only “not older than threshold.”

#### `stale_no_result_evidence`

- request age exceeded `stale_after`;
- processing token exists;
- no matching `CompanyCheckRecord`;
- no report/evidence DB rows for the token;
- no expected JSON or Markdown artifacts;
- no mutation is offered in this branch.

#### `stale_artifacts_unpersisted`

- request age exceeded `stale_after`;
- expected JSON and/or Markdown artifact exists;
- no matching persisted `CompanyCheck` result exists;
- no automatic persistence recovery or retry is included.

The following may remain structured artifact facts under this classification:

- JSON-only;
- Markdown-only;
- both artifacts with invalid JSON;
- readable artifacts with no matching DB result.

A valid JSON artifact whose `check_id` differs from `processing_check_id` must classify as `processing_inconsistent`, not as `stale_artifacts_unpersisted`.

#### `stale_persisted_incomplete`

- matching `CompanyCheckRecord` exists for the same processing token and request;
- required persistence evidence is incomplete;
- not safe to finalize.

#### `stale_persisted_complete`

- matching `CompanyCheckRecord` exists;
- `source_check_request_id` matches the request;
- required `ReportRecord` and tool-call evidence exist;
- filesystem artifacts are present, valid, and consistent;
- eligible for explicit safe finalize after all facts are rechecked.

#### `processing_inconsistent`

Applicable regardless of processing age when readable facts show structural conflict. Examples:

- valid JSON artifact whose `check_id` differs from `processing_check_id`;
- symlinked expected artifact;
- path escape outside the approved outputs root;
- `CompanyCheck` linked to another request;
- another request owns the token;
- orphan `SourceRecord`, `ToolCallRecord`, or `ReportRecord` rows without the matching `CompanyCheck`;
- DB report paths/content conflict with expected artifacts;
- contradictory `CheckRequest` processing fields;
- IDs conflict;
- duplicate or foreign evidence exists.

No automatic mutation is allowed for `processing_inconsistent`.

### Diagnosis error

Use diagnosis error only when required facts cannot be obtained, not when facts are readable but inconsistent. Do not invent a request classification from a failed inspection.

## 5. Staleness Decision

- `stale_after` is configurable;
- classification receives it as an explicit `timedelta`;
- timestamps are evaluated in UTC;
- age alone never authorizes mutation;
- the application has no heartbeat or execution lease;
- `within_processing_window` means only that the request is not older than the threshold.

Staleness is a gate for offering diagnosis categories that may later support finalize. It is never itself authorization to mutate.

## 6. Repository / Service / UI Boundaries

### Repository

Owns:

- reads of DB facts;
- atomic guarded mutations;
- writes of audit records.

Does not:

- access the filesystem;
- classify reconciliation states;
- trust a prior GET diagnosis object as authority for mutation.

### Service

Owns:

- combining DB facts and filesystem facts;
- calculating age;
- classifying the request;
- performing fresh pre-finalize inspection;
- translating typed outcomes.

Must never perform hidden mutation during diagnosis.

### UI / routes

Planned routes:

```text
GET  /internal/reconciliation
GET  /internal/reconciliation/{request_id}
POST /internal/reconciliation/{request_id}/finalize
```

Rules:

- GET never mutates state;
- no public page links to reconciliation;
- mutations require POST;
- routes call only the reconciliation service.

## 7. Safe Finalize Fencing

The POST form must include:

```text
expected_processing_check_id
```

The repository must receive that expected token explicitly. Finalize must not derive the token solely from a previously rendered diagnosis object.

The finalize transaction must require all of the following:

- request ID matches;
- status is `processing`;
- `processing_check_id` equals the explicit expected token;
- `processing_started_at` is not null;
- `company_check_id` is null;
- exactly one `CompanyCheckRecord` exists with:
  - `check_id` equal to the expected token;
  - `source_check_request_id` equal to the request ID;
- exactly one `ReportRecord` exists for the token;
- current required tool-call evidence is complete.

Do not require `SourceRecord` count >= 1.

For the current persistence version, safe finalize requires the exact tool-call multiset:

- `web_search` exactly once;
- `domain_dns_check` exactly once;
- `registry_search` exactly once;
- `risk_score` exactly once.

Missing, duplicate, additional, or unknown tool-call rows block finalize. This rule will require versioning when explicit tool-call manifests are introduced.

Conceptual conditional finalization:

```sql
UPDATE check_request_records
SET status = 'processed',
    company_check_id = :expected_processing_check_id,
    processing_check_id = NULL,
    processing_started_at = NULL
WHERE id = :request_id
  AND status = 'processing'
  AND processing_check_id = :expected_processing_check_id
  AND processing_started_at IS NOT NULL
  AND company_check_id IS NULL
```

Rowcount must equal exactly one. Concurrent POSTs may create two audit rows, but only one may perform the `processing → processed` transition.

## 8. Filesystem Rules

Expected report paths are derived only from:

```text
json_path_for_check(processing_check_id)
markdown_path_for_check(processing_check_id)
```

Stored DB paths are comparison facts only and must not be trusted as arbitrary read paths.

Before finalize, both expected artifacts must:

- exist;
- be regular files;
- not be symlinks;
- resolve inside the approved outputs root;
- be readable as UTF-8;
- match `ReportRecord` content;
- have valid JSON where applicable;
- have JSON `check_id` equal to `processing_check_id`.

DB and filesystem cannot participate in one atomic transaction. Finalize performs fresh filesystem validation immediately before the fenced DB transaction and records SHA-256 hashes in the audit snapshot.

A residual filesystem race remains: files may change between the final filesystem read and DB commit acknowledgement. That race must be documented honestly and is accepted for this MVP. It does not justify silent deletion, automatic rewrite, or trusting stale diagnosis output.

## 9. Audit Requirement

Safe finalize must not ship before a minimal audit table exists.

Document a future:

```text
ReconciliationActionRecord
```

with at least:

- `check_request_id`
- `processing_check_id`
- `action`
- `outcome`
- serialized diagnosis snapshot
- JSON artifact SHA-256
- Markdown artifact SHA-256
- `actor_label`
- operator note
- `created_at`

Until authentication exists:

```text
actor_label = internal-unauthenticated
```

Successful state mutation and its audit record must commit in the same DB transaction.

These business outcomes must produce committed audit rows:

- `finalized`;
- `already_processed`;
- `conflict`;
- `precondition_failed`.

Unexpected infrastructure or transaction failure is different:

- rollback;
- HTTP 500;
- server-side logging;
- no claim that the action completed;
- an audit row cannot be guaranteed when the database transaction itself fails.

Do not invent a successful audit outcome after a failed commit.

Two concurrent POSTs may legitimately create two audit rows, but only one may perform the `processing → processed` transition.

## 10. Finalize Outcomes and Idempotency

Document outcomes:

- `finalized`
- `already_processed`
- `conflict`
- `precondition_failed`

### `finalized`

The fenced transaction committed the `processing → processed` transition and the matching audit row.

### `already_processed`

Idempotent success only when a fresh check confirms all of the following:

- request status is `processed`;
- `company_check_id` equals the submitted expected processing token;
- matching `CompanyCheckRecord` exists;
- `CompanyCheckRecord.source_check_request_id` equals the request ID;
- exactly one matching `ReportRecord` exists;
- expected result artifacts remain readable and consistent.

Otherwise return `conflict` or a diagnosis/infrastructure error. Never treat a partial or mismatched processed state as successful redirect.

### `conflict`

The submitted expected token no longer identifies the authoritative attempt, or ownership/ID facts conflict. Typical cases:

- request state changed between diagnosis and POST;
- request is `processed` with another `company_check_id`;
- `CompanyCheck` or token ownership points to another request;
- valid JSON contains another `check_id`;
- IDs or ownership facts conflict;
- `already_processed` validation finds a different or foreign result.

Ownership, token, and ID conflicts from `processing_inconsistent` map here.

### `precondition_failed`

The submitted expected token still identifies the same request attempt, and ownership/IDs do not conflict, but the attempt is not eligible for finalize because it is too recent, artifacts are missing/invalid, persistence is incomplete, or required evidence is missing/duplicated.

Classifications such as `within_processing_window`, `stale_no_result_evidence`, `stale_artifacts_unpersisted`, and `stale_persisted_incomplete` normally map here. Non-conflicting but incomplete or unsafe evidence from `processing_inconsistent` (for example symlink, path escape outside the approved outputs root, or orphan evidence without foreign ownership) also maps here.

Ordinary incomplete evidence belongs in `precondition_failed`, not in `conflict`.

## 11. Reset Decision

State explicitly:

```text
processing → approved reset is not part of this branch.
```

Reset requires a future `ProcessingAttemptRecord` or equivalent durable attempt history. The failed attempt and old `processing_check_id` must never be erased without an audit trail.

Reset will be evaluated in a separate future branch. This branch must not implement bulk reset, token replacement, token reuse, or silent clearing of processing fields.

## 12. Read-Only Diagnosis Limitation

A diagnosis is advisory.

A single `SessionLocal`/transaction does not automatically guarantee an immutable PostgreSQL snapshot under `READ COMMITTED`. Concurrent writers may change request ownership, evidence rows, or related records after a diagnosis page is rendered.

Therefore:

- never authorize finalization from a previously rendered diagnosis object;
- the finalize action must repeat all authoritative DB and filesystem checks;
- UI may display the last diagnosis classification as operator context only.

## 13. Authorization Limitation

Internal routes are currently unauthenticated.

Document this as an accepted local/internal MVP limitation, not as security.

Additional constraints for this branch:

- no public page links to reconciliation;
- mutations require POST;
- authentication for `/internal/*` is a high-priority follow-up and is not implemented here.

## 14. Explicit Non-Goals

This ADR does not design or add:

- automatic pipeline retry;
- automatic reset to `approved`;
- bulk reset;
- processing token replacement;
- processing token reuse;
- background worker;
- cron;
- automatic startup reconciliation;
- report deletion;
- partial DB cleanup;
- hidden state change from GET;
- Redis;
- Celery;
- any queue system;
- full event-sourcing redesign;
- authentication implementation in this branch;
- attempt-history implementation in this branch;
- orphan-file cleanup;
- `processing → approved` release UI.

## 15. Planned Implementation Commits

### Commit 1 — ADR only

- create this ADR;
- no production code;
- no tests.

### Commit 2 — Diagnosis schemas and classification

- taxonomy enums/models;
- stale threshold input as explicit `timedelta`;
- classification result and diagnosis-error result;
- no DB mutations.

### Commit 3 — Reconciliation audit model and DB migration

- `ReconciliationActionRecord`;
- startup/model evolution helpers as required by project conventions;
- tests for fresh and existing databases.

### Commit 4 — Repository DB inspection

- read-only inspection of request, CompanyCheck, Report, and tool-call evidence;
- no filesystem access;
- no classification.

### Commit 5 — Read-only diagnosis service and filesystem inspection

- combine DB and filesystem facts;
- path derivation from expected helpers only;
- SHA-ready artifact inspection helpers;
- classification without mutation.

### Commit 6 — Read-only reconciliation UI

- `GET /internal/reconciliation`
- `GET /internal/reconciliation/{request_id}`
- no POST yet;
- no mutation from GET.

### Commit 7 — Atomic finalize repository with audit

- explicit expected token fencing;
- evidence completeness checks;
- audit row in the same transaction;
- rowcount-one finalization.

### Commit 8 — Finalize service and POST route

- fresh pre-finalize inspection;
- `POST /internal/reconciliation/{request_id}/finalize`
- outcome mapping;
- no trust of prior diagnosis objects.

### Commit 9 — SQLite integration tests

- taxonomy coverage;
- finalize happy path;
- conflict and already-processed outcomes;
- GET non-mutation proofs.

### Commit 10 — PostgreSQL concurrency and manual scenario

- concurrent finalize race;
- only one successful transition;
- audit-row expectations;
- manual operator scenario verification.

## 16. Required Invariants

1. Opening a GET reconciliation page never mutates state.
2. Diagnosis is advisory and must be recomputed before finalize.
3. Age alone never authorizes mutation.
4. Finalize requires an explicit `expected_processing_check_id`.
5. Every `processing` mutation checks that token in its conditional `WHERE`.
6. Finalize ships only after the audit table exists.
7. Successful mutation and audit commit in one DB transaction.
8. Filesystem reads use expected paths derived from the processing token.
9. Stored DB paths are comparison facts, not trusted arbitrary read paths.
10. Symlinks and path escapes outside the approved outputs root block finalize.
11. Zero sources do not block finalize.
12. Missing, duplicate, additional, or unknown tool-call rows block finalize.
13. Forensic artifacts are never silently deleted.
14. `processing → approved` reset is out of scope for this branch.
15. No public page links to reconciliation.

## 17. Testing Requirements

Required coverage will include:

- each successful diagnosis classification;
- diagnosis-error representation separate from taxonomy;
- UTC age calculation with explicit `stale_after`;
- GET list and detail never mutate;
- finalize requires matching expected token;
- finalize blocked by incomplete evidence;
- finalize blocked by filesystem inconsistency;
- finalize blocked by JSON check-ID mismatch;
- finalize blocked by symlink or path escape;
- finalize succeeds only for `stale_persisted_complete` after fresh inspection;
- `already_processed` only after fresh confirmation of processed status, matching token, matching CompanyCheck, matching ReportRecord, and consistent readable artifacts;
- conflicting or partial already-processed case returns `conflict` or infrastructure/diagnosis error, never successful redirect;
- concurrent finalize allows only one transition;
- audit rows for `finalized`, `already_processed`, `conflict`, and `precondition_failed`;
- no guaranteed audit row after a failed DB transaction;
- no SourceRecord minimum count requirement;
- exact current four-tool multiset for safe finalize;
- no automatic retry or reset side effects.

## 18. Consequences

Positive consequences:

- operators can inspect stuck `processing` requests without mutating them;
- safe finalize is available only when durable evidence is complete and consistent;
- fencing continues to use `processing_check_id`;
- auditability exists before mutation ships;
- forensic report files remain available for investigation;
- ADR-001 happy path remains unchanged.

Negative consequences:

- incomplete or inconsistent states remain operator-visible until a future reset/attempt-history design;
- residual filesystem races cannot be eliminated without a shared transactional store;
- internal routes remain unauthenticated until a separate auth follow-up;
- reconciliation adds another operator workflow and audit table.

## 19. Rejected Alternatives

The following alternatives are rejected or deferred:

- automatic startup reconciliation;
- automatic pipeline retry from diagnosis;
- blind `processing → approved` reset;
- deleting or rewriting report files during diagnosis;
- trusting stored DB path strings as arbitrary read paths;
- authorizing finalize from a previously rendered diagnosis object;
- requiring at least one `SourceRecord`;
- shipping finalize before the audit table;
- background workers, cron, Redis, Celery, or queues;
- implementing authentication in this branch;
- implementing attempt history / reset in this branch.

## 20. Implementation Gate

Cursor must not implement finalize mutation until all of the following exist:

- this ADR;
- diagnosis schemas and classification;
- `ReconciliationActionRecord` and DB evolution;
- repository DB inspection;
- read-only diagnosis service with filesystem inspection;
- read-only reconciliation UI proving GET non-mutation.

The first code commit after this ADR must be diagnosis schemas and classification only.
