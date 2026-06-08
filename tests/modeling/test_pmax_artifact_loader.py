from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_CANDIDATE_VERSIONS,
    PMAX_FORECAST_FEATURE_COLUMNS,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
)
from cms.modeling.pmax_artifact_loader import (
    PmaxArtifactDescriptor,
    PmaxArtifactIntegrityError,
    PmaxArtifactLoader,
    PmaxArtifactUnavailableError,
    PmaxReleaseArtifactLoader,
    sha256_file,
)


class FakeLoadedModel:
    pass


def test_pmax_artifact_loader_is_lazy_and_uses_injected_loader(tmp_path: Path) -> None:
    artifact_path = tmp_path / "model.joblib"
    artifact_path.write_bytes(b"fake artifact")
    expected_sha = sha256_file(artifact_path)
    calls: list[Path] = []
    fake_model = FakeLoadedModel()

    loader = PmaxArtifactLoader(
        PmaxArtifactDescriptor(artifact_path=artifact_path, expected_sha256=expected_sha),
        loader=lambda path: calls.append(path) or fake_model,
    )

    assert loader.loaded is False
    assert calls == []
    assert loader.load() is fake_model
    assert loader.load() is fake_model
    assert calls == [artifact_path]
    assert loader.describe()["loaded"] is True


def test_pmax_artifact_loader_blocks_missing_path_and_checksum_mismatch(tmp_path: Path) -> None:
    with pytest.raises(PmaxArtifactUnavailableError, match="artifact_path is required"):
        PmaxArtifactLoader(PmaxArtifactDescriptor(artifact_uri="drive://fake", drive_artifact_verified=True)).load()

    artifact_path = tmp_path / "model.joblib"
    artifact_path.write_bytes(b"fake artifact")
    loader = PmaxArtifactLoader(PmaxArtifactDescriptor(artifact_path=artifact_path, expected_sha256="0" * 64), loader=lambda path: object())

    with pytest.raises(PmaxArtifactIntegrityError, match="sha256 mismatch"):
        loader.load()


class FakeCandidateModel:
    def __init__(self, version: str) -> None:
        self.version = version

    def predict(self, rows: object) -> list[list[float]]:
        assert rows
        base = float(self.version.removeprefix("v"))
        return [[base, base + 1.0, base + 2.0, base + 3.0]]


def test_pmax_release_artifact_loader_validates_manifests_and_predicts_per_meter_ensemble(tmp_path: Path) -> None:
    release_root = tmp_path / "import_pmax_production_release_20260608"
    _write_fake_release(release_root)
    loaded_paths: list[Path] = []

    def fake_loader(path: Path) -> FakeCandidateModel:
        loaded_paths.append(path)
        return FakeCandidateModel(path.stem)

    loader = PmaxReleaseArtifactLoader(release_root, model_loader=fake_loader)

    model = loader.load()
    prediction = model.predict_features([SimpleNamespace(logical_meter="V.Z81")], [[1.0] * (96 * len(PMAX_FORECAST_FEATURE_COLUMNS))])

    assert loader.loaded is True
    assert len(loaded_paths) == len(PMAX_FORECAST_LOGICAL_METER_SOURCES) * len(PMAX_FORECAST_CANDIDATE_VERSIONS)
    assert set(model.meters) == set(PMAX_FORECAST_LOGICAL_METER_SOURCES)
    assert prediction[0] == pytest.approx((24.9, 25.9, 26.9, 27.9))


def _write_fake_release(release_root: Path) -> None:
    base = release_root / "artifacts" / "import_pmax_v29_60min" / "input_24h" / "predict_60min"
    for logical_meter in PMAX_FORECAST_LOGICAL_METER_SOURCES:
        meter_root = base / logical_meter
        model_root = meter_root / "_candidate_models"
        version_root = meter_root / "v29"
        model_root.mkdir(parents=True)
        version_root.mkdir(parents=True)
        (version_root / "manifest.json").write_text(
            json.dumps(
                {
                    "table": "mart.peak_feature_15min",
                    "logical_meter": logical_meter,
                    "target": "import P_max = max(measurement='P'.max_value, 0)",
                    "input_hours": 24,
                    "output_range": "60min",
                    "horizon_steps": 4,
                    "step_minutes": 15,
                    "feature_columns": list(PMAX_FORECAST_FEATURE_COLUMNS),
                    "candidate_versions": list(PMAX_FORECAST_CANDIDATE_VERSIONS),
                    "method": "v29",
                }
            ),
            encoding="utf-8",
        )
        (version_root / "ensemble_weights.csv").write_text(
            "candidate_version,weight\nv20,0.1\nv23,0.2\nv25,0.3\nv27,0.4\n",
            encoding="utf-8",
        )
        for version in PMAX_FORECAST_CANDIDATE_VERSIONS:
            (model_root / f"{version}.joblib").write_bytes(b"fake")
