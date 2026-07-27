"""PostgreSQL READ COMMITTED concurrency coverage for reconciliation finalize.

SQLite sequential tests cannot prove overlapping PostgreSQL transactions,
row locking, or commit visibility under READ COMMITTED. This module is the
Commit 10 repository-level concurrency proof for
finalize_processing_reconciliation_request(...).

This test intentionally does not use a single outer transaction rollback
fixture. It requires:

- committed setup on an independent test engine;
- two independent SQLAlchemy Sessions / PostgreSQL connections;
- real row locking on the fenced conditional UPDATE;
- real commit visibility between workers;
- cleanup via a separate committed cleanup transaction that deletes only
  rows created for the generated request ID and processing token.

Token-only cleanup fallback is safe because each run generates a unique
collision-resistant canonical processing token before setup; audit /
request / evidence rows for that token cannot collide with unrelated data.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, event, or_, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401
from app.db.database import Base
from app.db.models import (
    CheckRequestRecord,
    CompanyCheckRecord,
    ReconciliationActionRecord,
    ReportRecord,
    ToolCallRecord,
)
from app.db.repositories import (
    REQUIRED_RECONCILIATION_TOOL_CALLS,
    finalize_processing_reconciliation_request,
)


_ENV_VAR = "TEST_POSTGRES_URL"
_FORBIDDEN_DATABASE = "company_verification"
_WORKER_TIMEOUT_SECONDS = 15
# Strictly less than the overall worker wait, leaving grace for cancel/cleanup.
_DB_TIMEOUT_MS = 5_000
_CONNECT_TIMEOUT_SECONDS = 5
_BARRIER_TIMEOUT_SECONDS = 10
_GRACE_TIMEOUT_SECONDS = 7
_STARTED_AT = datetime(2026, 7, 20, 12, 0, 0)
_SNAPSHOT = '{"classification":"stale_persisted_complete"}'
_JSON_HASH = "a" * 64
_MD_HASH = "b" * 64


def _require_safe_postgres_url(raw_url: str):
    """Parse and hard-fail on unsafe TEST_POSTGRES_URL values."""
    url = make_url(raw_url)
    backend = url.get_backend_name()
    if backend not in {"postgresql", "postgres"}:
        raise RuntimeError(
            f"{_ENV_VAR} must use a PostgreSQL dialect; got backend "
            f"{backend!r}"
        )
    database_name = url.database
    if not database_name:
        raise RuntimeError(
            f"{_ENV_VAR} must include a non-empty database name"
        )
    if "test" not in database_name.lower():
        raise RuntimeError(
            f"{_ENV_VAR} database name {database_name!r} must contain "
            f"'test' (case-insensitive); refusing to run against an "
            f"unguarded database"
        )
    if database_name.lower() == _FORBIDDEN_DATABASE:
        raise RuntimeError(
            f"{_ENV_VAR} must not target the default development database "
            f"{_FORBIDDEN_DATABASE!r}"
        )
    return url


def _new_canonical_token() -> str:
    """Return a collision-resistant canonical processing check ID."""
    # Positive ASCII digits, no leading zero, length well under 64.
    token = f"{time.time_ns()}{os.getpid()}"
    if token[0] == "0":
        token = f"1{token}"
    assert token.isascii() and token.isdigit() and token[0] != "0"
    assert len(token) <= 64
    return token


def _is_finalize_conditional_update(statement: str) -> bool:
    """Identify the fenced finalize UPDATE on check_request_records only."""
    normalized = " ".join(statement.lower().split())
    if not normalized.startswith("update "):
        return False
    if "check_request_records" not in normalized:
        return False
    # Finalize sets all four of these columns in one statement.
    required_fragments = (
        " status=",
        " company_check_id=",
        " processing_check_id=",
        " processing_started_at=",
    )
    # SQLAlchemy may render "status=%(status)s" or "status = %(status)s".
    compact = normalized.replace(" =", "=").replace("= ", "=")
    return all(fragment in compact for fragment in required_fragments)


def _libpq_startup_options() -> str:
    """Return libpq -c options that survive rollback and pool reuse."""
    return (
        f"-c statement_timeout={_DB_TIMEOUT_MS} "
        f"-c lock_timeout={_DB_TIMEOUT_MS}"
    )


def _assert_nonzero_timeout(label: str, value: object) -> None:
    rendered = str(value).strip().lower()
    assert rendered not in {"", "0", "0ms", "0s"}, (
        f"{label} must be a non-zero bounded timeout, got {value!r}"
    )


def _assert_isolation_and_timeouts(connection) -> None:
    isolation = connection.execute(
        text("SHOW transaction_isolation")
    ).scalar_one()
    assert str(isolation).lower() == "read committed", (
        f"expected READ COMMITTED isolation, got {isolation!r}"
    )
    statement_timeout = connection.execute(
        text("SHOW statement_timeout")
    ).scalar_one()
    lock_timeout = connection.execute(
        text("SHOW lock_timeout")
    ).scalar_one()
    _assert_nonzero_timeout("statement_timeout", statement_timeout)
    _assert_nonzero_timeout("lock_timeout", lock_timeout)


def _cancel_tracked_dbapi_connections(
    active_connections: set[Any],
    active_lock: threading.Lock,
) -> None:
    """Cancel in-progress statements on test-engine connections only."""
    with active_lock:
        connections = list(active_connections)
    for dbapi_connection in connections:
        cancel = getattr(dbapi_connection, "cancel", None)
        if not callable(cancel):
            continue
        try:
            cancel()
        except Exception:
            # Best-effort cancellation; grace wait still bounds teardown.
            pass


def _force_worker_futures_done(
    *,
    barrier: threading.Barrier,
    futures: list[Future],
    active_connections: set[Any],
    active_lock: threading.Lock,
    grace_seconds: float,
) -> bool:
    """Abort barrier, cancel work, cancel SQL, wait a bounded grace period."""
    try:
        barrier.abort()
    except Exception:
        pass
    for future in futures:
        future.cancel()
    _cancel_tracked_dbapi_connections(active_connections, active_lock)
    wait(futures, timeout=grace_seconds)
    return all(future.done() for future in futures)


@pytest.fixture()
def postgres_engine() -> Engine:
    raw_url = os.environ.get(_ENV_VAR)
    if raw_url is None or raw_url.strip() == "":
        pytest.skip(
            f"{_ENV_VAR} is not configured; PostgreSQL concurrency tests "
            f"are skipped"
        )

    url = _require_safe_postgres_url(raw_url)
    # Startup options survive transaction rollback and pool reset; do not use
    # transactional SET statements for worker timeout bounds.
    engine = create_engine(
        url,
        isolation_level="READ COMMITTED",
        connect_args={
            "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
            "options": _libpq_startup_options(),
        },
    )
    try:
        with engine.connect() as connection:
            _assert_isolation_and_timeouts(connection)
        # Returning the connection to the pool triggers rollback/reset.
        with engine.connect() as connection:
            _assert_isolation_and_timeouts(connection)
        Base.metadata.create_all(bind=engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session_factory(postgres_engine: Engine, monkeypatch):
    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=postgres_engine,
    )
    # finalize_processing_reconciliation_request opens SessionLocal itself.
    monkeypatch.setattr(
        "app.db.repositories.SessionLocal",
        factory,
    )
    return factory


def _seed_eligible_request(session_factory, *, token: str) -> int:
    session = session_factory()
    try:
        request = CheckRequestRecord(
            company_name="Postgres Concurrency GmbH",
            country="Austria",
            email="pg-concurrency@example.com",
            preferred_language="en",
            status="processing",
            company_check_id=None,
            processing_check_id=token,
            processing_started_at=_STARTED_AT,
        )
        session.add(request)
        session.flush()
        request_id = request.id

        session.add(
            CompanyCheckRecord(
                check_id=token,
                source_check_request_id=request_id,
                company_name="Postgres Concurrency GmbH",
                country="Austria",
                json_report_path=(
                    f"outputs/json/company_check_{token}.json"
                ),
                markdown_report_path=(
                    f"outputs/reports/company_check_{token}.md"
                ),
            )
        )
        session.add(
            ReportRecord(
                check_id=token,
                json_path=f"outputs/json/company_check_{token}.json",
                markdown_path=f"outputs/reports/company_check_{token}.md",
                json_content='{"check_id":"%s"}' % token,
                markdown_content="# Report\n",
            )
        )
        for tool_name in REQUIRED_RECONCILIATION_TOOL_CALLS:
            session.add(
                ToolCallRecord(
                    check_id=token,
                    tool_name=tool_name,
                    status="completed",
                )
            )
        session.commit()
        return request_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _cleanup_test_rows(
    session_factory,
    *,
    request_id: int | None,
    token: str,
) -> None:
    """Delete only rows created for this run's request ID and/or token.

    When request_id is unavailable after a commit/client ambiguity, fall back
    to the exact generated token. That token is unique per run, so token-only
    deletes remain targeted and cannot wipe unrelated rows.
    """
    session = session_factory()
    try:
        if request_id is not None:
            (
                session.query(ReconciliationActionRecord)
                .filter(
                    ReconciliationActionRecord.check_request_id
                    == request_id,
                    ReconciliationActionRecord.processing_check_id == token,
                )
                .delete(synchronize_session=False)
            )
        else:
            (
                session.query(ReconciliationActionRecord)
                .filter(
                    ReconciliationActionRecord.processing_check_id == token
                )
                .delete(synchronize_session=False)
            )

        (
            session.query(ToolCallRecord)
            .filter(ToolCallRecord.check_id == token)
            .delete(synchronize_session=False)
        )
        (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == token)
            .delete(synchronize_session=False)
        )
        (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == token)
            .delete(synchronize_session=False)
        )

        if request_id is not None:
            (
                session.query(CheckRequestRecord)
                .filter(CheckRequestRecord.id == request_id)
                .delete(synchronize_session=False)
            )
        else:
            (
                session.query(CheckRequestRecord)
                .filter(
                    or_(
                        CheckRequestRecord.processing_check_id == token,
                        CheckRequestRecord.company_check_id == token,
                    )
                )
                .delete(synchronize_session=False)
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _load_request(session_factory, request_id: int) -> dict[str, Any]:
    session = session_factory()
    try:
        record = (
            session.query(CheckRequestRecord)
            .filter(CheckRequestRecord.id == request_id)
            .one()
        )
        return {
            "status": record.status,
            "company_check_id": record.company_check_id,
            "processing_check_id": record.processing_check_id,
            "processing_started_at": record.processing_started_at,
        }
    finally:
        session.close()


def _count_evidence(session_factory, token: str) -> dict[str, Any]:
    session = session_factory()
    try:
        company_count = (
            session.query(CompanyCheckRecord)
            .filter(CompanyCheckRecord.check_id == token)
            .count()
        )
        report_count = (
            session.query(ReportRecord)
            .filter(ReportRecord.check_id == token)
            .count()
        )
        tool_rows = (
            session.query(ToolCallRecord.tool_name)
            .filter(ToolCallRecord.check_id == token)
            .all()
        )
        tool_names = tuple(name for (name,) in tool_rows)
        return {
            "company_count": company_count,
            "report_count": report_count,
            "tool_count": len(tool_names),
            "tool_names": tool_names,
        }
    finally:
        session.close()


def _audit_outcomes(
    session_factory,
    *,
    request_id: int,
    token: str,
) -> list[str]:
    session = session_factory()
    try:
        rows = (
            session.query(ReconciliationActionRecord.outcome)
            .filter(
                ReconciliationActionRecord.check_request_id == request_id,
                ReconciliationActionRecord.processing_check_id == token,
            )
            .all()
        )
        return [outcome for (outcome,) in rows]
    finally:
        session.close()


def _run_finalize_worker(
    *,
    request_id: int,
    token: str,
    worker_label: str,
):
    """Call the repository finalizer; SessionLocal is already test-bound."""
    return finalize_processing_reconciliation_request(
        request_id,
        expected_processing_check_id=token,
        actor_label=f"pg-concurrency-{worker_label}",
        diagnosis_snapshot_json=_SNAPSHOT,
        operator_note=f"worker-{worker_label}",
        artifact_json_sha256=_JSON_HASH,
        artifact_markdown_sha256=_MD_HASH,
    )


def test_require_safe_postgres_url_rejects_non_postgres_dialect():
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        _require_safe_postgres_url("sqlite:////tmp/example.db")


def test_require_safe_postgres_url_rejects_missing_database_name():
    with pytest.raises(RuntimeError, match="non-empty database name"):
        _require_safe_postgres_url("postgresql://user:pass@localhost")


def test_require_safe_postgres_url_rejects_database_without_test():
    with pytest.raises(RuntimeError, match="must contain 'test'"):
        _require_safe_postgres_url(
            "postgresql://user:pass@localhost/company_verification"
        )


def test_require_safe_postgres_url_accepts_dedicated_test_database():
    url = _require_safe_postgres_url(
        "postgresql://user:pass@localhost:5432/company_verification_test"
    )
    assert url.database == "company_verification_test"


def test_is_finalize_conditional_update_matches_finalize_shape():
    statement = (
        "UPDATE check_request_records SET status=%(status)s, "
        "company_check_id=%(company_check_id)s, "
        "processing_check_id=%(processing_check_id)s, "
        "processing_started_at=%(processing_started_at)s "
        "WHERE check_request_records.id = %(id_1)s"
    )
    assert _is_finalize_conditional_update(statement) is True


def test_is_finalize_conditional_update_ignores_inserts_and_selects():
    assert _is_finalize_conditional_update(
        "INSERT INTO check_request_records (status) VALUES ('processing')"
    ) is False
    assert _is_finalize_conditional_update(
        "SELECT * FROM check_request_records WHERE id = 1"
    ) is False
    assert _is_finalize_conditional_update(
        "UPDATE reconciliation_action_records SET outcome='conflict' "
        "WHERE id = 1"
    ) is False


def test_db_timeout_is_positive_and_strictly_less_than_worker_timeout():
    assert _DB_TIMEOUT_MS > 0
    assert _DB_TIMEOUT_MS < _WORKER_TIMEOUT_SECONDS * 1000


def test_connect_timeout_is_positive_and_bounded():
    assert _CONNECT_TIMEOUT_SECONDS > 0
    assert _CONNECT_TIMEOUT_SECONDS < _WORKER_TIMEOUT_SECONDS


def test_barrier_and_grace_timeouts_leave_worker_budget():
    assert 0 < _BARRIER_TIMEOUT_SECONDS < _WORKER_TIMEOUT_SECONDS
    assert 0 < _GRACE_TIMEOUT_SECONDS < _WORKER_TIMEOUT_SECONDS


def test_new_canonical_token_is_canonical_shape():
    token = _new_canonical_token()
    assert token.isascii() and token.isdigit()
    assert token[0] != "0"
    assert len(token) <= 64


@pytest.mark.postgres
def test_two_workers_one_finalized_one_already_processed(
    postgres_engine: Engine,
    session_factory,
):
    """Prove one winner and one idempotent loser under READ COMMITTED."""
    token = _new_canonical_token()
    request_id: int | None = None
    update_listener_registered = False
    checkout_listener_registered = False
    checkin_listener_registered = False
    executor: ThreadPoolExecutor | None = None
    futures: list[Future] = []
    barrier = threading.Barrier(2, timeout=_BARRIER_TIMEOUT_SECONDS)
    match_lock = threading.Lock()
    matched_updates = 0
    active_lock = threading.Lock()
    active_connections: set[Any] = set()
    workers_confirmed_done = False
    deferred_failure: BaseException | None = None

    def before_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        nonlocal matched_updates
        if not _is_finalize_conditional_update(statement):
            return
        should_wait = False
        with match_lock:
            if matched_updates < 2:
                matched_updates += 1
                should_wait = True
        if should_wait:
            barrier.wait()

    def on_checkout(dbapi_connection, _connection_record, _connection_proxy):
        with active_lock:
            active_connections.add(dbapi_connection)

    def on_checkin(dbapi_connection, _connection_record):
        with active_lock:
            active_connections.discard(dbapi_connection)

    try:
        request_id = _seed_eligible_request(session_factory, token=token)

        event.listen(
            postgres_engine,
            "before_cursor_execute",
            before_cursor_execute,
        )
        update_listener_registered = True
        event.listen(postgres_engine, "checkout", on_checkout)
        checkout_listener_registered = True
        event.listen(postgres_engine, "checkin", on_checkin)
        checkin_listener_registered = True

        executor = ThreadPoolExecutor(max_workers=2)
        futures = [
            executor.submit(
                _run_finalize_worker,
                request_id=request_id,
                token=token,
                worker_label="a",
            ),
            executor.submit(
                _run_finalize_worker,
                request_id=request_id,
                token=token,
                worker_label="b",
            ),
        ]
        done, not_done = wait(
            futures,
            timeout=_WORKER_TIMEOUT_SECONDS,
        )
        if not_done:
            terminated = _force_worker_futures_done(
                barrier=barrier,
                futures=futures,
                active_connections=active_connections,
                active_lock=active_lock,
                grace_seconds=_GRACE_TIMEOUT_SECONDS,
            )
            workers_confirmed_done = terminated
            if not terminated:
                pytest.fail(
                    "infrastructure-timeout: worker futures still running "
                    f"after psycopg2 cancel() and {_GRACE_TIMEOUT_SECONDS}s "
                    f"grace (matched_updates={matched_updates}); cleanup "
                    f"was not started"
                )
            pytest.fail(
                "PostgreSQL concurrency workers timed out after "
                f"{_WORKER_TIMEOUT_SECONDS}s; possible deadlock or barrier "
                f"hang (matched_updates={matched_updates}). Workers were "
                f"terminated after cancel/grace before cleanup."
            )

        workers_confirmed_done = True
        outcomes = []
        for future in futures:
            try:
                result = future.result(timeout=0)
            except Exception as exc:  # noqa: BLE001 - surface worker error
                pytest.fail(
                    f"worker raised unexpected exception: "
                    f"{type(exc).__name__}: {exc}"
                )
            outcomes.append(result.outcome)

        assert matched_updates == 2, (
            "expected both worker finalize UPDATEs to enter the barrier; "
            f"matched_updates={matched_updates}"
        )
        assert Counter(outcomes) == Counter(
            ["finalized", "already_processed"]
        ), f"unexpected outcome multiset: {outcomes!r}"

        request_state = _load_request(session_factory, request_id)
        assert request_state["status"] == "processed"
        assert request_state["company_check_id"] == token
        assert request_state["processing_check_id"] is None
        assert request_state["processing_started_at"] is None

        evidence = _count_evidence(session_factory, token)
        assert evidence["company_count"] == 1
        assert evidence["report_count"] == 1
        assert evidence["tool_count"] == 4
        assert Counter(evidence["tool_names"]) == Counter(
            REQUIRED_RECONCILIATION_TOOL_CALLS
        )

        audit_outcomes = _audit_outcomes(
            session_factory,
            request_id=request_id,
            token=token,
        )
        assert Counter(audit_outcomes) == Counter(
            ["finalized", "already_processed"]
        )
        assert audit_outcomes.count("finalized") == 1
        assert "conflict" not in audit_outcomes
        assert "precondition_failed" not in audit_outcomes

        third = _run_finalize_worker(
            request_id=request_id,
            token=token,
            worker_label="third",
        )
        assert third.outcome == "already_processed"

        request_after_third = _load_request(session_factory, request_id)
        assert request_after_third == request_state
        evidence_after_third = _count_evidence(session_factory, token)
        assert evidence_after_third == evidence

        audit_after_third = _audit_outcomes(
            session_factory,
            request_id=request_id,
            token=token,
        )
        assert Counter(audit_after_third) == Counter(
            ["finalized", "already_processed", "already_processed"]
        )
        assert audit_after_third.count("finalized") == 1
        assert audit_after_third.count("already_processed") == 2
    except BaseException as exc:
        deferred_failure = exc
        raise
    finally:
        cleanup_error: Exception | None = None

        try:
            barrier.abort()
        except Exception as exc:  # noqa: BLE001
            cleanup_error = exc

        if update_listener_registered:
            try:
                event.remove(
                    postgres_engine,
                    "before_cursor_execute",
                    before_cursor_execute,
                )
            except Exception as exc:  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = exc
        if checkout_listener_registered:
            try:
                event.remove(postgres_engine, "checkout", on_checkout)
            except Exception as exc:  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = exc
        if checkin_listener_registered:
            try:
                event.remove(postgres_engine, "checkin", on_checkin)
            except Exception as exc:  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = exc

        if not futures:
            # Workers never started; row cleanup is safe.
            workers_confirmed_done = True
        elif not all(future.done() for future in futures):
            workers_confirmed_done = _force_worker_futures_done(
                barrier=barrier,
                futures=futures,
                active_connections=active_connections,
                active_lock=active_lock,
                grace_seconds=_GRACE_TIMEOUT_SECONDS,
            )
        else:
            workers_confirmed_done = True

        if not workers_confirmed_done:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            infra_error = RuntimeError(
                "cleanup skipped: worker futures were not confirmed done "
                "after cancel/grace; refusing to delete rows while a worker "
                "may still hold a transaction or lock"
            )
            if deferred_failure is not None:
                raise infra_error from deferred_failure
            if cleanup_error is not None:
                raise infra_error from cleanup_error
            raise infra_error

        try:
            _cleanup_test_rows(
                session_factory,
                request_id=request_id,
                token=token,
            )
        except Exception as exc:  # noqa: BLE001
            if cleanup_error is None:
                cleanup_error = exc
            else:
                raise RuntimeError(
                    f"prior cleanup failed ({cleanup_error!r}) and "
                    f"row cleanup also failed ({exc!r})"
                ) from exc
        finally:
            if executor is not None:
                # Futures are done; wait=True returns immediately and joins
                # worker threads cleanly.
                executor.shutdown(wait=True)

        if cleanup_error is not None:
            raise RuntimeError(
                f"PostgreSQL concurrency cleanup failed: {cleanup_error!r}"
            ) from cleanup_error
