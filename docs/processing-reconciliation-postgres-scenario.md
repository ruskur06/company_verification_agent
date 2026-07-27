# Processing Reconciliation — PostgreSQL Concurrency Scenario

## Purpose

SQLite sequential tests cannot prove PostgreSQL row-lock contention or
`READ COMMITTED` commit visibility for concurrent
`finalize_processing_reconciliation_request(...)` calls.

Commit 10 covers **repository-level** concurrency only:

- same `CheckRequest`;
- same expected processing token;
- same complete DB evidence;
- two independent Sessions / connections under `READ COMMITTED`.

Service-layer filesystem verification races, artifact drift, route/UI
behavior, and multi-process stress are deferred.

## Automated test setup

PostgreSQL-marked tests read **only** `TEST_POSTGRES_URL`.

They never reuse the application `DATABASE_URL` or default settings.

Safety rules enforced by the test module:

1. URL must use a PostgreSQL dialect.
2. Database name must be non-empty.
3. Database name must contain the substring `test` (case-insensitive).
4. The default development database `company_verification` must not be used.
5. Unsafe configured URLs raise a hard configuration error (they are not
   skipped).
6. The database name is never rewritten silently.

Use a dedicated database such as `company_verification_test`.

When `TEST_POSTGRES_URL` is absent, PostgreSQL-marked tests skip with a clear
reason and the normal suite remains runnable.

Do not put real passwords or secrets into repository files.

## Commands

Run only PostgreSQL concurrency coverage (placeholder credentials):

```bash
TEST_POSTGRES_URL='postgresql://USER:PASSWORD@HOST:PORT/company_verification_test' \
  .venv/bin/python -m pytest \
  -m postgres \
  tests/test_processing_reconciliation_postgres_concurrency.py -v
```

Run the suite while excluding PostgreSQL tests:

```bash
.venv/bin/python -m pytest -m "not postgres"
```

Default local runs without `TEST_POSTGRES_URL` collect the PostgreSQL tests and
skip them clearly; existing non-PostgreSQL tests continue to pass.

## Expected automated result

For the two concurrent finalizers:

- outcome multiset is exactly one `finalized` and one `already_processed`;
- neither worker returns a second `finalized`, `conflict`,
  `precondition_failed`, or an unexpected exception;
- request state becomes:
  - `status == processed`
  - `company_check_id == token`
  - `processing_check_id` NULL
  - `processing_started_at` NULL
- evidence remains exactly:
  - one `CompanyCheckRecord`
  - one `ReportRecord`
  - four `ToolCallRecord` rows with the exact multiset
    `web_search`, `domain_dns_check`, `registry_search`, `risk_score`
- audit history for the request/token contains exactly one `finalized` and
  one `already_processed` (no conflict / precondition_failed / second
  finalized)

After a third sequential repository call with the same request ID and token:

- outcome is `already_processed`;
- audit outcome multiset is exactly one `finalized` and two
  `already_processed`;
- request and evidence state remain unchanged.

## Controlled operator scenario

Safe manual operator walk-through on a non-production environment:

1. Create or identify an eligible `processing` request.
2. Confirm complete persisted DB evidence for its processing token.
3. Confirm valid report artifacts for that token under the trusted outputs
   root.
4. Reach the stale threshold using controlled test timestamps/data.
5. Open the reconciliation list (`GET /internal/reconciliation`).
6. Open the reconciliation detail page for the request.
7. Confirm classification `stale_persisted_complete`.
8. Submit finalize with the expected processing token.
9. Confirm redirect to the result page for that token.
10. Verify the request is `processed` with cleared processing fields.
11. Repeat finalize with the same submitted token.
12. Verify `already_processed` behavior and append-only audit history.

## Negative manual scenarios

Expected classifications:

- wrong token → `conflict`
- missing or invalid artifact → `precondition_failed`
- GET list/detail → no database mutation
- foreign ownership → `conflict`

Do not run destructive SQL (`DROP`, `TRUNCATE`, table-wide `DELETE`) against
shared databases. Do not casually modify production data.
