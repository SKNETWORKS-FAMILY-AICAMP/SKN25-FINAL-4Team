from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cms.workflow.replay_clock import replay_virtual_now


def test_replay_virtual_now_maps_wall_elapsed_to_historical_clock() -> None:
    env = {
        "CMS_REPLAY_VIRTUAL_START_TS": "2023-01-01T00:00:00+09:00",
        "CMS_REPLAY_WALL_START_TS": "2026-06-15T00:00:00+09:00",
        "CMS_REPLAY_TIME_SCALE": "1.0",
    }

    kst = timezone(timedelta(hours=9))
    value = replay_virtual_now(env=env, wall_now=datetime(2026, 6, 15, 1, 38, tzinfo=kst))

    assert value == datetime.fromisoformat("2023-01-01T01:38:00+09:00")


def test_replay_virtual_now_is_disabled_without_env_pair() -> None:
    assert replay_virtual_now(env={}) is None
