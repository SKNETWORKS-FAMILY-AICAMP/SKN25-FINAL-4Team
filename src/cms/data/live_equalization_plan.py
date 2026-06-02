"""Read-only TC0 inventory and equalization count planning for CMS live tests.

This module scans local EMS/CMS file names only. It does not import database
clients, open network connections, write MongoDB documents, or write PostgreSQL
rows. Corrected-resampled files are reported as validation references only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

HARMONIZED_SUFFIX = "_harmonized.csv.gz"
CORRECTED_1MIN_SUFFIX = "_corrected_resampled_1min.csv.gz"
CORRECTED_5MIN_SUFFIX = "_corrected_resampled_5min.csv.gz"
CORRECTED_15MIN_SUFFIX = "_corrected_resampled_15min.csv.gz"
CORRECTED_1H_SUFFIX = "_corrected_resampled_1h.csv.gz"
BACKUP_SEGMENT = "/backup/"


@dataclass(frozen=True)
class SeriesInventoryEntry:
    """One direct harmonized measurement series and its validation references."""

    meter_urn: str
    measurement: str
    harmonized_file: str
    corrected_1min_ref: str | None
    corrected_15min_ref: str | None
    corrected_1h_ref: str | None
    corrected_5min_ref: str | None
    eq_1min_reference_kind: str
    eq_1min_reference_source: str | None
    eq_5min_reference_kind: str
    eq_5min_reference_source: str | None


@dataclass(frozen=True)
class TC0Inventory:
    """TC0 source mapping summary for dry-run planning."""

    data_root: str
    source_identifier_count: int
    measurement_series_count: int
    corrected_5min_ref_count: int
    missing_reference_counts: dict[str, int]
    entries: tuple[SeriesInventoryEntry, ...]
    side_effects_executed: bool = False
    writes_allowed: bool = False

    @property
    def by_meter_measurement(self) -> dict[tuple[str, str], SeriesInventoryEntry]:
        return {(entry.meter_urn, entry.measurement): entry for entry in self.entries}


@dataclass(frozen=True)
class EqualizationCountPlan:
    """Expected row counts for diagnostic/intermediate and final grains."""

    measurement_series_count: int
    window_minutes: int
    rows_out_1min: int
    rows_out_5min: int
    rows_out_15min: int
    rows_out_1h: int
    eq_1min_reference: str = "corrected_resampled_1min"
    eq_5min_reference: str = "derived_from_corrected_resampled_1min"
    corrected_resampled_5min_is_input: bool = False
    side_effects_executed: bool = False
    writes_allowed: bool = False


@dataclass(frozen=True)
class SeriesCadenceCountPolicy:
    """Count-planning policy for one meter/measurement native cadence."""

    native_interval_seconds: int
    target_grain_minutes: int | None = None


def build_tc0_inventory(data_root: Path | str) -> TC0Inventory:
    """Build the TC0 inventory from local files, failing on ambiguous mappings."""

    root = Path(data_root).expanduser().resolve()
    harmonized_by_key = _collect_by_key(root, HARMONIZED_SUFFIX)
    corrected_1min_by_key = _collect_by_key(root, CORRECTED_1MIN_SUFFIX)
    corrected_5min_by_key = _collect_by_key(root, CORRECTED_5MIN_SUFFIX)
    corrected_15min_by_key = _collect_by_key(root, CORRECTED_15MIN_SUFFIX)
    corrected_1h_by_key = _collect_by_key(root, CORRECTED_1H_SUFFIX)

    ambiguous = {key: paths for key, paths in harmonized_by_key.items() if len(paths) > 1}
    if ambiguous:
        details = "; ".join(f"{key}: {', '.join(path.as_posix() for path in paths)}" for key, paths in sorted(ambiguous.items()))
        raise ValueError(f"ambiguous harmonized mapping: {details}")

    entries: list[SeriesInventoryEntry] = []
    for key, paths in sorted(harmonized_by_key.items()):
        harmonized_file = paths[0]
        meter_urn, measurement = _split_meter_measurement(key, harmonized_file.parent.name)
        corrected_1min = _single_ref(corrected_1min_by_key, key)
        corrected_5min = _single_ref(corrected_5min_by_key, key)
        corrected_15min = _single_ref(corrected_15min_by_key, key)
        corrected_1h = _single_ref(corrected_1h_by_key, key)
        entries.append(
            SeriesInventoryEntry(
                meter_urn=meter_urn,
                measurement=measurement,
                harmonized_file=harmonized_file.as_posix(),
                corrected_1min_ref=_path_text(corrected_1min),
                corrected_15min_ref=_path_text(corrected_15min),
                corrected_1h_ref=_path_text(corrected_1h),
                corrected_5min_ref=_path_text(corrected_5min),
                eq_1min_reference_kind="corrected_resampled_1min" if corrected_1min else "missing_corrected_resampled_1min",
                eq_1min_reference_source=_path_text(corrected_1min),
                eq_5min_reference_kind="derived_from_corrected_resampled_1min" if corrected_1min else "missing_corrected_resampled_1min",
                eq_5min_reference_source=_path_text(corrected_1min),
            )
        )

    missing_reference_counts = {
        "corrected_1min_ref": sum(entry.corrected_1min_ref is None for entry in entries),
        "corrected_15min_ref": sum(entry.corrected_15min_ref is None for entry in entries),
        "corrected_1h_ref": sum(entry.corrected_1h_ref is None for entry in entries),
    }
    return TC0Inventory(
        data_root=root.as_posix(),
        source_identifier_count=len({entry.meter_urn for entry in entries}),
        measurement_series_count=len(entries),
        corrected_5min_ref_count=sum(entry.corrected_5min_ref is not None for entry in entries),
        missing_reference_counts=missing_reference_counts,
        entries=tuple(entries),
    )


def build_equalization_count_plan(*, measurement_series_count: int, window_minutes: int) -> EqualizationCountPlan:
    """Return legacy 1min-derived row-count expectations for a whole-hour window."""

    if measurement_series_count < 0:
        raise ValueError("measurement_series_count must be non-negative")
    return build_cadence_equalization_count_plan(
        cadence_policies=tuple(SeriesCadenceCountPolicy(native_interval_seconds=60) for _ in range(measurement_series_count)),
        window_minutes=window_minutes,
    )


def build_cadence_equalization_count_plan(
    *,
    cadence_policies: tuple[SeriesCadenceCountPolicy, ...],
    window_minutes: int,
) -> EqualizationCountPlan:
    """Return row-count expectations using per-series native cadence policies."""

    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")
    if window_minutes % 60 != 0:
        raise ValueError("window_minutes must be divisible by 60 for 1h final count planning")

    rows_out_1min = 0
    rows_out_5min = 0
    rows_out_15min = 0
    rows_out_1h = 0
    for policy in cadence_policies:
        target_grain = _target_grain_minutes(policy)
        if window_minutes % target_grain != 0:
            raise ValueError("window_minutes must be divisible by every target grain")
        if target_grain == 1:
            rows_out_1min += window_minutes
            rows_out_5min += window_minutes // 5
            rows_out_15min += window_minutes // 15
            rows_out_1h += window_minutes // 60
        elif target_grain == 15:
            rows_out_15min += window_minutes // 15
            rows_out_1h += window_minutes // 60
        elif target_grain == 60:
            rows_out_1h += window_minutes // 60
        else:
            raise ValueError(f"unsupported target grain: {target_grain}")

    return EqualizationCountPlan(
        measurement_series_count=len(cadence_policies),
        window_minutes=window_minutes,
        rows_out_1min=rows_out_1min,
        rows_out_5min=rows_out_5min,
        rows_out_15min=rows_out_15min,
        rows_out_1h=rows_out_1h,
    )


def _target_grain_minutes(policy: SeriesCadenceCountPolicy) -> int:
    if policy.native_interval_seconds <= 0:
        raise ValueError("native_interval_seconds must be positive")
    if policy.target_grain_minutes is not None:
        if policy.target_grain_minutes <= 0:
            raise ValueError("target_grain_minutes must be positive")
        return policy.target_grain_minutes
    if policy.native_interval_seconds <= 60:
        return 1
    if policy.native_interval_seconds <= 900:
        return 15
    return 60


def _collect_by_key(root: Path, suffix: str) -> dict[str, tuple[Path, ...]]:
    paths_by_key: dict[str, list[Path]] = defaultdict(list)
    if not root.exists():
        return {}
    for path in root.rglob(f"*{suffix}"):
        if BACKUP_SEGMENT in path.as_posix():
            continue
        key = path.name.removesuffix(suffix)
        paths_by_key[key].append(path)
    return {key: tuple(sorted(paths)) for key, paths in paths_by_key.items()}


def _single_ref(paths_by_key: dict[str, tuple[Path, ...]], key: str) -> Path | None:
    paths = paths_by_key.get(key, ())
    if len(paths) > 1:
        counts = Counter(path.parent.as_posix() for path in paths)
        raise ValueError(f"ambiguous reference mapping for {key}: {dict(counts)}")
    return paths[0] if paths else None


def _split_meter_measurement(key: str, parent_name: str) -> tuple[str, str]:
    prefix = f"{parent_name}."
    if key.startswith(prefix):
        return parent_name, key[len(prefix) :]
    if "." not in key:
        raise ValueError(f"ambiguous meter/measurement mapping for {key}")
    meter_urn, measurement = key.rsplit(".", 1)
    return meter_urn, measurement


def _path_text(path: Path | None) -> str | None:
    return path.as_posix() if path else None
