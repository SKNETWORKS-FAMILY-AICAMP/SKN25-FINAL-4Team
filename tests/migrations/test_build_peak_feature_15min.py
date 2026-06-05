import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "build_peak_feature_15min.py"
SPEC = importlib.util.spec_from_file_location("build_peak_feature_15min", SCRIPT_PATH)
assert SPEC is not None
build_peak_feature_15min = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_peak_feature_15min)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("datetime_utc,value\n", encoding="utf-8")


def test_discover_files_filters_measurements_prefixes_and_exact_meters(tmp_path):
    root = tmp_path / "corrected_resampled"
    _touch(root / "H1.Z10" / "H1.Z10.P_corrected_resampled_1min.csv.gz")
    _touch(root / "H1.Z10" / "H1.Z10.U1_corrected_resampled_1min.csv.gz")
    _touch(root / "H1.K11" / "H1.K11.P_corrected_resampled_1min.csv.gz")
    _touch(root / "WeatherStation.Weather" / "WeatherStation.Weather.Ta_corrected_resampled_1min.csv.gz")

    files = build_peak_feature_15min.discover_files(
        root,
        {"P", "U1", "Ta"},
        limit_files=None,
        meter_prefixes=("H1.Z",),
        exact_meters=("WeatherStation.Weather",),
    )

    assert [path.relative_to(root).as_posix() for path in files] == [
        "H1.Z10/H1.Z10.P_corrected_resampled_1min.csv.gz",
        "H1.Z10/H1.Z10.U1_corrected_resampled_1min.csv.gz",
        "WeatherStation.Weather/WeatherStation.Weather.Ta_corrected_resampled_1min.csv.gz",
    ]


def test_non_positive_limit_means_no_limit(tmp_path):
    root = tmp_path / "corrected_resampled"
    _touch(root / "H1.Z10" / "H1.Z10.P_corrected_resampled_1min.csv.gz")
    _touch(root / "H1.Z11" / "H1.Z11.P_corrected_resampled_1min.csv.gz")

    files = build_peak_feature_15min.discover_files(
        root,
        {"P"},
        limit_files=0,
        meter_prefixes=("H1.Z",),
        exact_meters=(),
    )

    assert len(files) == 2
