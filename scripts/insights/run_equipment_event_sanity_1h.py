"""Run first-pass event sanity checks for equipment-anomaly validation.

The output compares short windows around known public events. It is a screening
artifact, not causal attribution.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from run_equipment_relation_strength_1h import OUT, TZ, fetch_reduced_1h

EVENTS = [
    {
        "event_id": "chp_control_mode_update_2019_02_19",
        "event_ts_local": "2019-02-19 00:00:00",
        "module": "CHP operation",
        "signals": ["chp_elec_P", "chp_heat_P", "heating_total_P", "Ta"],
        "window_days": 14,
        "expected_check": "CHP 전기·열 생산 관계와 운전 패턴 변화",
    },
    {
        "event_id": "pv_group_1_2_commissioning_2019_06",
        "event_ts_local": "2019-06-28 22:00:00",
        "module": "PV performance",
        "signals": ["pv_P", "Igm", "Ta"],
        "window_days": 14,
        "expected_check": "PV 발전 availability와 일사량 관계 시작",
    },
    {
        "event_id": "pv_group_4_6_commissioning_2020_06",
        "event_ts_local": "2020-06-01 00:00:00",
        "module": "PV performance",
        "signals": ["pv_P", "Igm", "Ta"],
        "window_days": 30,
        "expected_check": "PV 발전량 분포와 regime 변화",
    },
    {
        "event_id": "covid_lockdown_context_2020_03",
        "event_ts_local": "2020-03-16 00:00:00",
        "module": "Ventilation / baseload",
        "signals": ["cooling_elec_P", "heating_total_P", "chp_elec_P", "Ta"],
        "window_days": 30,
        "expected_check": "site operation proxy의 분포 변화 후보",
    },
    {
        "event_id": "transformer_replacement_context_2020_09",
        "event_ts_local": "2020-09-09 12:00:00",
        "module": "Transformer / grid boundary",
        "signals": ["pv_P", "chp_elec_P", "Ta"],
        "window_days": 14,
        "expected_check": "grid boundary 해석 시 replacement gap context로 보존",
    },
    {
        "event_id": "heating_chp_modernization_2023_06",
        "event_ts_local": "2023-06-01 00:00:00",
        "module": "CHP operation / Heating",
        "signals": ["chp_elec_P", "chp_heat_P", "heating_total_P", "Ta"],
        "window_days": 30,
        "expected_check": "heating/CHP 관계 regime 변화 후보",
    },
    {
        "event_id": "cooling_load_scaling_issue_2023_09_20",
        "event_ts_local": "2023-09-20 18:00:00",
        "module": "Cooling efficiency",
        "signals": ["cooling_elec_P", "cooling_thermal_P", "Ta"],
        "window_days": 7,
        "expected_check": "cooling thermal relation 이탈 또는 data-quality 후보",
    },
]


def summarize_window(frame: pd.DataFrame, signal: str, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    subset = frame[(frame["local_ts"] >= start) & (frame["local_ts"] < end)][signal].dropna()
    if len(subset) == 0:
        return {"count": 0, "mean": np.nan, "std": np.nan, "p50": np.nan}
    return {
        "count": int(len(subset)),
        "mean": float(subset.mean()),
        "std": float(subset.std(ddof=0)),
        "p50": float(subset.quantile(0.5)),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = fetch_reduced_1h()
    rows = []
    for event in EVENTS:
        event_ts = pd.Timestamp(event["event_ts_local"], tz=TZ)
        days = int(event["window_days"])
        before_start = event_ts - pd.Timedelta(days=days)
        before_end = event_ts
        after_start = event_ts
        after_end = event_ts + pd.Timedelta(days=days)
        for signal in event["signals"]:
            before = summarize_window(frame, signal, before_start, before_end)
            after = summarize_window(frame, signal, after_start, after_end)
            shift = after["mean"] - before["mean"] if before["count"] and after["count"] else np.nan
            std_shift = shift / before["std"] if before["count"] and after["count"] and before["std"] else np.nan
            rows.append(
                {
                    "event_id": event["event_id"],
                    "event_ts_local": event_ts.isoformat(),
                    "module": event["module"],
                    "signal": signal,
                    "window_days": days,
                    "before_count": before["count"],
                    "after_count": after["count"],
                    "before_mean": before["mean"],
                    "after_mean": after["mean"],
                    "mean_shift": shift,
                    "standardized_shift_vs_before": std_shift,
                    "before_p50": before["p50"],
                    "after_p50": after["p50"],
                    "expected_check": event["expected_check"],
                    "interpretation_boundary": "event-window screening only; causal attribution requires source/BMS/field evidence",
                }
            )
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "04_event_sanity_check_1h.csv", index=False)

    brief = [
        "# Step 4 공개 이벤트 sanity check 1차 결과\n",
        f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat()}",
        "- 기준 relation: `ems.reduced_measurement_1h`",
        "- 방식: 이벤트 전후 동일 길이 window의 평균·중앙값·표준화 평균 변화 비교",
        "- 해석 경계: change signal screening이며 원인 확정 결과가 아님",
        "\n## 표준화 평균 변화가 큰 항목 예시\n",
    ]
    ranked = out.dropna(subset=["standardized_shift_vs_before"]).copy()
    ranked["abs_shift"] = ranked["standardized_shift_vs_before"].abs()
    for row in ranked.sort_values("abs_shift", ascending=False).head(12).itertuples():
        brief.append(
            f"- {row.event_id} / {row.signal}: before_n={row.before_count}, after_n={row.after_count}, std_shift={row.standardized_shift_vs_before:.2f}"
        )
    brief.append("\n## 생성 파일\n")
    brief.append(f"- `{OUT / '04_event_sanity_check_1h.csv'}`")
    brief.append(f"- `{OUT / 'STEP4_EVENT_SANITY_BRIEF.md'}`")
    (OUT / "STEP4_EVENT_SANITY_BRIEF.md").write_text("\n".join(brief) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(out), "out": str(OUT / "04_event_sanity_check_1h.csv")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
