"""데이터 로드 + 번들 준비. test2 의존 없음."""
from __future__ import annotations

from energy_v84.common.config import MeterSpec
from energy_v84.common.db import build_engine, fetch_meter_frame
from energy_v84.common.preprocessing import DatasetBundle, build_windows, prepare_model_frame


def load_bundle(engine, spec: MeterSpec, horizon: int, window_size: int, use_time_features: bool) -> DatasetBundle:
    raw = fetch_meter_frame(engine, spec)
    frame, rows_raw, feature_columns = prepare_model_frame(raw, spec, use_time_features)
    return build_windows(frame, spec, horizon, rows_raw, feature_columns, window_size)
