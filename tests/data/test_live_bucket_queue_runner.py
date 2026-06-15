from __future__ import annotations

from datetime import datetime, timezone

from cms.data.live_bucket_queue_runner import make_live_bucket_queue_worker_command


def test_live_bucket_queue_worker_can_be_capped_to_replay_clock_closed_buckets() -> None:
    max_bucket_ts = datetime(2023, 1, 1, 1, 38, tzinfo=timezone.utc)
    command = make_live_bucket_queue_worker_command(max_bucket_ts=max_bucket_ts)

    assert "q.bucket_ts + interval '15 minutes' <= %(max_bucket_ts)s::timestamptz" in command.sql
    assert "q.bucket_ts + interval '1 hour' <= %(max_bucket_ts)s::timestamptz" in command.sql
    assert command.params["max_bucket_ts"] == max_bucket_ts
