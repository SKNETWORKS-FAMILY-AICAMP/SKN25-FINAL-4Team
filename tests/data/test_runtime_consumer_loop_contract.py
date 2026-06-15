from __future__ import annotations

from tests.data.test_stream_consumer_runner_contract import _message

from cms.data.kafka_adapter import InMemoryKafkaProducer
from cms.data.postgres_event_writer import InMemoryPostgresEventWriter
from cms.data.runtime_consumer_loop import ConsumerLoopStats, run_consumer_loop


class FakeConsumer:
    def __init__(self, messages: list[dict[str, object] | None]) -> None:
        self.messages = messages
        self.committed: list[object] = []
        self.closed = False

    def poll_message(self, *, timeout: float) -> tuple[object, dict[str, object]] | None:
        if not self.messages:
            return None
        message = self.messages.pop(0)
        if message is None:
            return None
        return message, message

    def commit(self, message: object) -> None:
        self.committed.append(message)

    def close(self) -> None:
        self.closed = True


def test_consumer_loop_commits_after_successful_insert() -> None:
    message = _message()
    consumer = FakeConsumer([message])
    writer = InMemoryPostgresEventWriter()

    stats = run_consumer_loop(consumer=consumer, writer=writer, dlq_producer=InMemoryKafkaProducer(), max_messages=1)

    assert stats == ConsumerLoopStats(polled=1, processed=1, committed=1, inserted=1, duplicate=0, dlq=0, retry=0)
    assert consumer.committed == [message]
    assert consumer.closed is True


def test_consumer_loop_passes_runtime_topic_identity_to_postgres_payload() -> None:
    message = _message()
    consumer = FakeConsumer([message])
    writer = InMemoryPostgresEventWriter()

    stats = run_consumer_loop(
        consumer=consumer,
        writer=writer,
        dlq_producer=InMemoryKafkaProducer(),
        max_messages=1,
        kafka_topic_identity="local_pc123.measurement_raw_v1",
    )

    assert stats.inserted == 1
    assert writer.rows is not None
    row = next(iter(writer.rows.values()))
    assert row["kafka_topic"] == "local_pc123.measurement_raw_v1"
    assert row["source_ref"] == "local_pc123.measurement_raw_v1:0:5"


def test_consumer_loop_does_not_commit_db_failure() -> None:
    message = _message()
    consumer = FakeConsumer([message])

    stats = run_consumer_loop(consumer=consumer, writer=InMemoryPostgresEventWriter(fail=True), dlq_producer=InMemoryKafkaProducer(), max_messages=1)

    assert stats.processed == 1
    assert stats.retry == 1
    assert stats.committed == 0
    assert consumer.committed == []


def test_consumer_loop_commits_after_dlq_success() -> None:
    message = _message(value_numeric="bad", value_text=None)
    consumer = FakeConsumer([message])
    dlq = InMemoryKafkaProducer()

    stats = run_consumer_loop(consumer=consumer, writer=InMemoryPostgresEventWriter(), dlq_producer=dlq, max_messages=1)

    assert stats.dlq == 1
    assert stats.committed == 1
    assert consumer.committed == [message]
    assert dlq.to_kafka_message(0)["topic"] == "measurement_dead_letter_v1"


def test_consumer_loop_tolerates_initial_idle_polls_before_assignment() -> None:
    message = _message()
    consumer = FakeConsumer([None, message])

    stats = run_consumer_loop(
        consumer=consumer,
        writer=InMemoryPostgresEventWriter(),
        dlq_producer=InMemoryKafkaProducer(),
        max_messages=1,
        max_idle_polls=2,
    )

    assert stats.polled == 1
    assert stats.processed == 1
    assert stats.inserted == 1
    assert stats.committed == 1
    assert consumer.committed == [message]


def test_consumer_loop_stops_after_idle_poll_without_committing() -> None:
    consumer = FakeConsumer([])

    stats = run_consumer_loop(consumer=consumer, writer=InMemoryPostgresEventWriter(), dlq_producer=InMemoryKafkaProducer(), max_messages=1)

    assert stats.polled == 0
    assert stats.processed == 0
    assert stats.committed == 0
    assert consumer.closed is True


def test_consumer_loop_closes_runtime_writer_when_supported() -> None:
    class ClosableWriter(InMemoryPostgresEventWriter):
        closed = False

        def close(self) -> None:
            self.closed = True

    consumer = FakeConsumer([])
    writer = ClosableWriter()

    run_consumer_loop(consumer=consumer, writer=writer, dlq_producer=InMemoryKafkaProducer(), max_messages=1)

    assert writer.closed is True
    assert consumer.closed is True
