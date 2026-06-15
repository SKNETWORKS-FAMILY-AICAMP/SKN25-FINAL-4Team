"""Replay-time helpers for historical live simulation.

Wall-clock time is allowed for audit fields such as created_at. Report/model
business timestamps must use the replay clock when a historical live run is
configured.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta


REPLAY_VIRTUAL_START_ENV = "CMS_REPLAY_VIRTUAL_START_TS"
REPLAY_WALL_START_ENV = "CMS_REPLAY_WALL_START_TS"
REPLAY_TIME_SCALE_ENV = "CMS_REPLAY_TIME_SCALE"


def parse_aware_datetime(value: str, *, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def replay_virtual_now(
    *,
    env: Mapping[str, str] | None = None,
    wall_now: datetime | None = None,
) -> datetime | None:
    """Return the simulated business clock, or None when replay mode is unset."""

    runtime_env = os.environ if env is None else env
    virtual_start_text = runtime_env.get(REPLAY_VIRTUAL_START_ENV)
    wall_start_text = runtime_env.get(REPLAY_WALL_START_ENV)
    if not virtual_start_text or not wall_start_text:
        return None

    virtual_start = parse_aware_datetime(virtual_start_text, field_name=REPLAY_VIRTUAL_START_ENV)
    wall_start = parse_aware_datetime(wall_start_text, field_name=REPLAY_WALL_START_ENV)
    current_wall = wall_now or datetime.now(tz=UTC)
    if current_wall.tzinfo is None or current_wall.utcoffset() is None:
        raise ValueError("wall_now must be timezone-aware")

    scale = float(runtime_env.get(REPLAY_TIME_SCALE_ENV, "1.0"))
    if scale <= 0:
        raise ValueError(f"{REPLAY_TIME_SCALE_ENV} must be positive")

    elapsed = current_wall.astimezone(UTC) - wall_start.astimezone(UTC)
    if elapsed < timedelta(0):
        elapsed = timedelta(0)
    return virtual_start + timedelta(seconds=elapsed.total_seconds() * scale)


def cap_to_replay_now(value: datetime | None, *, env: Mapping[str, str] | None = None, wall_now: datetime | None = None) -> datetime | None:
    """Cap a timestamp to replay virtual now when replay mode is configured."""

    virtual_now = replay_virtual_now(env=env, wall_now=wall_now)
    if virtual_now is None:
        return value
    if value is None:
        return virtual_now
    return min(value, virtual_now.astimezone(value.tzinfo))


__all__ = [
    "REPLAY_TIME_SCALE_ENV",
    "REPLAY_VIRTUAL_START_ENV",
    "REPLAY_WALL_START_ENV",
    "cap_to_replay_now",
    "parse_aware_datetime",
    "replay_virtual_now",
]
