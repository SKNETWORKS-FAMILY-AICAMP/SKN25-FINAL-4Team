"""Import-safe fake champion model adapter for tests and dry local flows."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from cms.contracts.model_input_1h import HISTORY_HOURS, INPUT_GRAIN_1H, ModelInput1HRow, assert_valid_model_input_1h


@dataclass(frozen=True)
class FakeChampionPrediction:
    """Three-hour ahead fake prediction envelope for one meter."""

    meter_urn: str
    input_grain: str
    pred_t_plus_1: float
    pred_t_plus_2: float
    pred_t_plus_3: float
    post_hoc: Mapping[str, int | str]


@dataclass(frozen=True)
class FakeChampionAdapter:
    """Deterministic adapter that validates input and echoes ``P`` at ``base_ts``."""

    adapter_name: str = "fake_champion_adapter"
    model_version: str = "fake-champion-v0"

    def predict(self, rows: Iterable[ModelInput1HRow], *, base_ts: datetime) -> tuple[FakeChampionPrediction, ...]:
        materialized_rows = tuple(rows)
        assert_valid_model_input_1h(materialized_rows, base_ts=base_ts)

        base_row_by_meter: OrderedDict[str, ModelInput1HRow] = OrderedDict()
        for row in materialized_rows:
            if row.ts == base_ts and row.meter_urn not in base_row_by_meter:
                base_row_by_meter[row.meter_urn] = row

        return tuple(self._prediction_for(row, base_ts=base_ts) for row in base_row_by_meter.values())

    def _prediction_for(self, row: ModelInput1HRow, *, base_ts: datetime) -> FakeChampionPrediction:
        latest_p = float(cast(float | int, row.features["P"]))
        return FakeChampionPrediction(
            meter_urn=row.meter_urn,
            input_grain=INPUT_GRAIN_1H,
            pred_t_plus_1=latest_p,
            pred_t_plus_2=latest_p,
            pred_t_plus_3=latest_p,
            post_hoc={
                "adapter_name": self.adapter_name,
                "model_version": self.model_version,
                "history_hours": HISTORY_HOURS,
                "base_ts": base_ts.isoformat(),
                "pred_t_plus_1_ts": (base_ts + timedelta(hours=1)).isoformat(),
                "pred_t_plus_2_ts": (base_ts + timedelta(hours=2)).isoformat(),
                "pred_t_plus_3_ts": (base_ts + timedelta(hours=3)).isoformat(),
            },
        )


__all__ = ["FakeChampionAdapter", "FakeChampionPrediction"]
