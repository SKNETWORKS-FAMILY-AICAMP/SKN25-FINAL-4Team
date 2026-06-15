from __future__ import annotations

import sys
from types import ModuleType

import pytest


def test_runtime_postgres_module_does_not_import_psycopg_at_module_import_time() -> None:
    sys.modules.pop("psycopg", None)

    import cms.data.runtime_postgres as runtime_postgres

    assert runtime_postgres.LIVE_MEASUREMENT_EVENT_TABLE == "live.measurement_event"
    assert "psycopg" not in sys.modules


def test_runtime_postgres_loader_accepts_db_env_fallback() -> None:
    from cms.data.runtime_postgres import load_postgres_config_from_env

    config = load_postgres_config_from_env(
        {
            "DB_HOST": "db.example.internal",
            "DB_PORT": "5433",
            "DB_NAME": "cms_live",
            "DB_USER": "worker",
            "DB_PASSWORD": "secret",
            "DB_SSLMODE": "require",
        }
    )

    assert config.host == "db.example.internal"
    assert config.port == 5433
    assert config.dbname == "cms_live"
    assert config.user == "worker"
    assert config.password == "secret"
    assert config.sslmode == "require"


def test_runtime_postgres_writer_executes_idempotent_insert_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, dict[str, object]]] = []
    committed: list[bool] = []

    class FakeCursor:
        rowcount = 1

        def execute(self, sql: str, params: dict[str, object]) -> None:
            executed.append((sql, params))

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def commit(self) -> None:
            committed.append(True)

        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    module = ModuleType("psycopg")
    module.connect = lambda **kwargs: FakeConnection()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)

    from cms.data.runtime_postgres import create_psycopg_event_writer

    writer = create_psycopg_event_writer({"POSTGRES_HOST": "172.31.47.236", "POSTGRES_PASSWORD": "secret"})
    result = writer.insert_measurement_event(
        {
            "target_table": "live.measurement_event",
            "event_id": "source_event|sensor|evt-1",
            "source_event_id": "evt-1",
            "meter_urn": "meter:001",
            "measurement": "P",
        }
    )

    assert result.succeeded is True
    assert result.duplicate_event is False
    assert result.rows_affected == 1
    assert committed == [True]
    assert "ON CONFLICT (event_id) DO NOTHING" in executed[0][0]
    assert executed[0][1]["event_id"] == "source_event|sensor|evt-1"


def test_runtime_postgres_writer_maps_zero_rowcount_to_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCursor:
        rowcount = 0

        def execute(self, sql: str, params: dict[str, object]) -> None:
            pass

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def commit(self) -> None:
            pass

        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    module = ModuleType("psycopg")
    module.connect = lambda **kwargs: FakeConnection()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)

    from cms.data.runtime_postgres import create_psycopg_event_writer

    writer = create_psycopg_event_writer({"POSTGRES_HOST": "172.31.47.236"})
    result = writer.insert_measurement_event({"target_table": "live.measurement_event", "event_id": "event-1"})

    assert result.succeeded is True
    assert result.duplicate_event is True
    assert result.rows_affected == 0


def test_runtime_postgres_writer_maps_business_unique_violation_to_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDiag:
        constraint_name = "measurement_event_business_idempotency_uq"

    class FakeUniqueViolation(Exception):
        diag = FakeDiag()

    class FakeCursor:
        def execute(self, sql: str, params: dict[str, object]) -> None:
            raise FakeUniqueViolation("duplicate key value violates unique constraint")

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    module = ModuleType("psycopg")
    module.connect = lambda **kwargs: FakeConnection()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)

    from cms.data.runtime_postgres import create_psycopg_event_writer

    writer = create_psycopg_event_writer({"POSTGRES_HOST": "172.31.47.236"})
    result = writer.insert_measurement_event({"target_table": "live.measurement_event", "event_id": "event-1"})

    assert result.succeeded is True
    assert result.duplicate_event is True
    assert result.rows_affected == 0
    assert result.error is None


def test_runtime_postgres_writer_redacts_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("psycopg")

    def connect(**kwargs: object) -> object:
        raise RuntimeError("password=super-secret connection failed")

    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)

    from cms.data.runtime_postgres import create_psycopg_event_writer

    writer = create_psycopg_event_writer({"POSTGRES_HOST": "172.31.47.236", "POSTGRES_PASSWORD": "super-secret"})
    result = writer.insert_measurement_event({"target_table": "live.measurement_event", "event_id": "event-1"})

    assert result.succeeded is False
    assert result.rows_affected == 0
    assert result.error is not None
    assert "super-secret" not in result.error
    assert "[REDACTED]" in result.error


def test_runtime_postgres_writer_reuses_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connections: list[object] = []

    class FakeCursor:
        rowcount = 1

        def execute(self, sql: str, params: dict[str, object]) -> None:
            pass

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    class FakeConnection:
        closed = False

        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    def connect(**kwargs: object) -> FakeConnection:
        conn = FakeConnection()
        connections.append(conn)
        return conn

    module = ModuleType("psycopg")
    module.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)

    from cms.data.runtime_postgres import create_psycopg_event_writer

    writer = create_psycopg_event_writer({"POSTGRES_HOST": "172.31.47.236"})
    for idx in range(2):
        result = writer.insert_measurement_event({"target_table": "live.measurement_event", "event_id": f"event-{idx}"})
        assert result.succeeded is True

    assert len(connections) == 1
    writer.close()
    assert getattr(connections[0], "closed") is True
