from __future__ import annotations

import sys
from types import ModuleType

import pytest


def test_runtime_postgres_module_does_not_import_psycopg_at_module_import_time() -> None:
    sys.modules.pop("psycopg", None)

    import cms.data.runtime_postgres as runtime_postgres

    assert runtime_postgres.LIVE_MEASUREMENT_EVENT_TABLE == "live.measurement_event"
    assert "psycopg" not in sys.modules


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


def test_runtime_postgres_writer_redacts_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            raise RuntimeError("password=super-secret connection failed")

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    module = ModuleType("psycopg")
    module.connect = lambda **kwargs: FakeConnection()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", module)

    from cms.data.runtime_postgres import create_psycopg_event_writer

    writer = create_psycopg_event_writer({"POSTGRES_PASSWORD": "super-secret"})
    result = writer.insert_measurement_event({"target_table": "live.measurement_event", "event_id": "event-1"})

    assert result.succeeded is False
    assert result.rows_affected == 0
    assert result.error is not None
    assert "super-secret" not in result.error
    assert "[REDACTED]" in result.error
