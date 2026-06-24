"""Import-safe verification for CMS live worker and canonical promotion boundaries.

Run from repository root:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_cms_live_worker_boundaries.py
"""

from __future__ import annotations

from cms.data.canonical_promotion_runner import (
    CANONICAL_PROMOTION_WRITE_ENV_FLAG,
    execute_canonical_promotion_command,
    make_canonical_promotion_command,
)
from cms.data.live_bucket_queue_runner import make_live_bucket_queue_worker_command


def _assert_not_contains(text: str, forbidden: tuple[str, ...], *, label: str) -> None:
    present = tuple(token for token in forbidden if token in text)
    if present:
        raise AssertionError(f"{label} contains forbidden token(s): {present}")


def verify_live_worker_sql_split() -> None:
    mean_command = make_live_bucket_queue_worker_command(job_kinds=("mean_rollup",))
    assert mean_command.output_tables == (
        "live.measurement_15min",
        "live.measurement_1h",
        "live.promotion_check",
    )
    assert "INSERT INTO live.measurement_15min" in mean_command.sql
    assert "INSERT INTO live.measurement_1h" in mean_command.sql
    assert "INSERT INTO live.promotion_check" in mean_command.sql
    _assert_not_contains(mean_command.sql, ("mart.peak_feature_15min", "canonical."), label="mean_rollup SQL")

    peak_command = make_live_bucket_queue_worker_command(job_kinds=("peak_feature",), resolutions=("15min",))
    assert peak_command.output_tables == ("mart.peak_feature_15min",)
    assert "INSERT INTO mart.peak_feature_15min" in peak_command.sql
    _assert_not_contains(
        peak_command.sql,
        ("live.measurement_15min", "live.measurement_1h", "live.promotion_check", "canonical."),
        label="peak_feature SQL",
    )


def verify_canonical_promotion_source_and_gate() -> None:
    command = make_canonical_promotion_command(promotion_id="verify_promo", approval_id="verify_approval")
    assert command.source_tables == ("live.measurement_1min", "live.measurement_15min", "live.measurement_1h")
    assert command.target_tables == ("canonical.measurement_1min", "canonical.measurement_15min", "canonical.measurement_1h")
    assert "INSERT INTO canonical.measurement_1min" in command.sql
    assert "FROM live.measurement_1min AS src" in command.sql

    for source_table in (
        "mart.peak_feature_15min",
        "mart.anomaly_feature_1h",
        "reference.corrected_resampled_15min",
        "reference.corrected_resampled_1h",
    ):
        try:
            make_canonical_promotion_command(
                promotion_id="verify_promo",
                approval_id="verify_approval",
                source_tables=(source_table,),
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"canonical promotion accepted forbidden source table: {source_table}")

    no_env = execute_canonical_promotion_command(command, allow_write=True, env={})
    assert no_env.ok is False
    assert no_env.attempted is False
    assert no_env.blocked is True
    assert no_env.errors == (f"{CANONICAL_PROMOTION_WRITE_ENV_FLAG}=1_required",)

    dry_run = execute_canonical_promotion_command(command, allow_write=False, env={CANONICAL_PROMOTION_WRITE_ENV_FLAG: "1"})
    assert dry_run.ok is False
    assert dry_run.attempted is False
    assert dry_run.blocked is True
    assert dry_run.errors == ("allow_write_required",)


def main() -> None:
    verify_live_worker_sql_split()
    verify_canonical_promotion_source_and_gate()
    print("cms live worker boundary verification passed")


if __name__ == "__main__":
    main()
