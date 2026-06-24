"""Export DB-backed anomaly training data to parquet archives for RunPod jobs."""
from __future__ import annotations

import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cms.modeling.anomaly.config import MeterSpec, training_specs_for_group
from cms.modeling.anomaly.db import build_engine, fetch_meter_frame


def _select_specs(
    meters: list[str] | None = None,
    groups: list[str] | None = None,
) -> list[MeterSpec]:
    specs = training_specs_for_group()
    if groups:
        group_set = set(groups)
        specs = [spec for spec in specs if spec.group in group_set]
    if meters:
        meter_set = set(meters)
        specs = [spec for spec in specs if spec.meter_urn in meter_set]
    return specs


def export_training_data_archive(
    destination_root: str | Path,
    run_id: str,
    *,
    meters: list[str] | None = None,
    groups: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """Export selected training meters into ``run_id.tar.gz``.

    The tarball contains ``{run_id}/frames/{meter_urn}.parquet`` and a
    ``manifest.json``. It is independent from horizon because the same raw
    hourly frame can train both 1h and 3h anomaly models.
    """
    destination = Path(destination_root)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / f"{run_id}.tar.gz"
    if archive_path.exists() and not overwrite:
        return {
            "status": "exists",
            "run_id": run_id,
            "archive_path": str(archive_path),
        }

    specs = _select_specs(meters=meters, groups=groups)
    if not specs:
        raise ValueError("no training meters selected for export")

    engine = build_engine()
    started_at = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix=f"training_data_{run_id}_", dir=str(destination)) as tmp:
        tmp_root = Path(tmp)
        package_root = tmp_root / run_id
        frames_dir = package_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        manifest_rows: list[dict] = []
        for spec in specs:
            frame = fetch_meter_frame(engine, spec)
            out_path = frames_dir / f"{spec.meter_urn}.parquet"
            frame.to_parquet(out_path, index=False, compression="snappy")
            manifest_rows.append(
                {
                    "meter_urn": spec.meter_urn,
                    "group": spec.group,
                    "role": spec.role,
                    "features": list(spec.features),
                    "rows": int(len(frame)),
                    "file": f"frames/{spec.meter_urn}.parquet",
                }
            )

        manifest = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started_at.isoformat(),
            "meter_count": len(manifest_rows),
            "meters": manifest_rows,
        }
        (package_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        tmp_archive = destination / f".{run_id}.tar.gz.tmp"
        if tmp_archive.exists():
            tmp_archive.unlink()
        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(package_root, arcname=run_id)
        tmp_archive.replace(archive_path)

    return {
        "status": "exported",
        "run_id": run_id,
        "archive_path": str(archive_path),
        "size_bytes": archive_path.stat().st_size,
        "meter_count": len(specs),
    }
