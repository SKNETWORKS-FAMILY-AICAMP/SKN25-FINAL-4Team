from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from config.meter_metadata import get_all_meters, get_metadata
from scripts.anomaly_ensemble_h1z16 import run_ensemble
from scripts.anomaly_if_h1z16 import run_if
from scripts.anomaly_lstm_h1z16 import run_lstm
from scripts.anomaly_stl_h1z16 import run_stl
from scripts.preprocess_h1z16 import preprocess_meter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_CSV = PROJECT_ROOT / "outputs" / "anomaly_results_all.csv"
DETAIL_OUTPUT_CSV = PROJECT_ROOT / "outputs" / "anomaly_results_detail.csv"
SUMMARY_OUTPUT_CSV = PROJECT_ROOT / "outputs" / "anomaly_results_summary.csv"
STL_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "stl"
IF_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "if"
LSTM_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "lstm"

RUN_STL = True
RUN_IF = True
RUN_LSTM = True

ELECTRIC_FEATURES = [
    "P",
    "PF",
    "PF1",
    "PF2",
    "PF3",
    "P1",
    "P2",
    "P3",
    "I1",
    "I2",
    "I3",
    "Ta",
    "Igm",
    "hour_sin",
    "hour_cos",
    "month_sin",
    "month_cos",
    "is_weekend",
]

THERMAL_FEATURES = [
    "Tdiff",
    "Tvl",
    "Trl",
    "qv",
    "Ta",
    "Igm",
    "hour_sin",
    "hour_cos",
    "month_sin",
    "month_cos",
    "is_weekend",
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
logger = logging.getLogger(__name__)


def get_cause_hint(row: pd.Series) -> str:
    stl = bool(row["anomaly_stl"])
    if_ = bool(row["anomaly_if"])
    lstm = bool(row["anomaly_lstm"])

    if row["ensemble_level"] == "NORMAL":
        return "정상"
    if stl and if_ and lstm:
        return "즉각 점검 필요 (3개 모델 모두 이상 탐지)"
    if stl and if_ and not lstm:
        return "복합 이상, 설비 이상 또는 센서 오류 의심"
    if stl and lstm and not if_:
        return "패턴 지속 이탈, 장기 운영 이상 의심"
    if if_ and lstm and not stl:
        return "다변량 + 패턴 복합 이상 의심"
    if stl and not if_ and not lstm:
        return "일시적 수요 급증 또는 스파이크 의심"
    if if_ and not stl and not lstm:
        return "다변량 feature 이상, 역률/전류 불균형 의심"
    if lstm and not stl and not if_:
        return "운영 패턴 변화 의심"
    return "정상"


def select_feature_columns(df: pd.DataFrame, meter_type: str) -> list[str]:
    candidates = THERMAL_FEATURES if meter_type == "thermal" else ELECTRIC_FEATURES
    selected = []
    for column in candidates:
        if column not in df.columns:
            continue
        if df[column].isna().mean() <= 0.5:
            selected.append(column)
    return selected


def summarize_results(results_df: pd.DataFrame) -> None:
    success_df = results_df.loc[results_df["error"].isna()].copy()
    failed_df = results_df.loc[results_df["error"].notna()].copy()

    print(f"성공: {len(success_df)}개")
    print(f"실패: {len(failed_df)}개")

    if not success_df.empty:
        print(f"전체 DANGER 평균 비율: {success_df['danger_pct'].mean():.2f}%")
        print(f"전체 WARNING 평균 비율: {success_df['warning_pct'].mean():.2f}%")

        print("DANGER 가장 많은 계량기 top 5:")
        print(
            success_df.sort_values(["danger", "danger_pct"], ascending=False)[
                ["meter_urn", "danger", "danger_pct"]
            ]
            .head(5)
            .to_string(index=False)
        )

        print("WARNING 가장 많은 계량기 top 5:")
        print(
            success_df.sort_values(["warning", "warning_pct"], ascending=False)[
                ["meter_urn", "warning", "warning_pct"]
            ]
            .head(5)
            .to_string(index=False)
        )


def ensure_output_dirs() -> None:
    os.makedirs(STL_OUTPUT_DIR, exist_ok=True)
    os.makedirs(IF_OUTPUT_DIR, exist_ok=True)
    os.makedirs(LSTM_OUTPUT_DIR, exist_ok=True)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)


def build_model_df(valid_df: pd.DataFrame, target_col: str) -> pd.DataFrame:
    model_df = valid_df.copy()
    if target_col != "P":
        model_df["P"] = model_df[target_col]
    return model_df


def stl_output_path(meter_urn: str) -> Path:
    return STL_OUTPUT_DIR / f"{meter_urn}_stl.csv"


def if_output_path(meter_urn: str) -> Path:
    return IF_OUTPUT_DIR / f"{meter_urn}_if.csv"


def lstm_output_path(meter_urn: str) -> Path:
    return LSTM_OUTPUT_DIR / f"{meter_urn}_lstm.csv"


def get_stl_result(meter_urn: str, model_df: pd.DataFrame) -> pd.DataFrame | None:
    path = stl_output_path(meter_urn)
    if RUN_STL:
        stl_df = run_stl(df=model_df.copy(), target_col="P", save_plot_file=False)
        output_df = stl_df[["ts", "anomaly_stl"]].copy()
        output_df.insert(1, "meter_urn", meter_urn)
        output_df.to_csv(path, index=False, encoding="utf-8-sig")
        return output_df

    if not path.exists():
        logger.warning("%s STL 중간 결과 없음, skip", meter_urn)
        return None
    return pd.read_csv(path, parse_dates=["ts"])


def get_if_result(
    meter_urn: str,
    model_df: pd.DataFrame,
    feature_cols: list[str],
) -> pd.DataFrame | None:
    path = if_output_path(meter_urn)
    if RUN_IF:
        if_df, _, _ = run_if(
            df=model_df.copy(),
            feature_cols=feature_cols,
            save_plot_file=False,
        )
        output_df = if_df[["ts", "anomaly_if"]].copy()
        output_df.insert(1, "meter_urn", meter_urn)
        output_df.to_csv(path, index=False, encoding="utf-8-sig")
        return output_df

    if not path.exists():
        logger.warning("%s IF 중간 결과 없음, skip", meter_urn)
        return None
    return pd.read_csv(path, parse_dates=["ts"])


def get_lstm_result(
    meter_urn: str,
    model_df: pd.DataFrame,
    feature_cols: list[str],
) -> pd.DataFrame | None:
    path = lstm_output_path(meter_urn)
    if RUN_LSTM:
        lstm_df, _, _, _ = run_lstm(
            df=model_df.copy(),
            feature_cols=feature_cols,
            save_plot_files=False,
            print_epoch_logs=False,
        )
        output_df = lstm_df[["ts", "anomaly_lstm"]].copy()
        output_df.insert(1, "meter_urn", meter_urn)
        output_df.to_csv(path, index=False, encoding="utf-8-sig")
        return output_df

    if not path.exists():
        logger.warning("%s LSTM 중간 결과 없음, skip", meter_urn)
        return None
    return pd.read_csv(path, parse_dates=["ts"])


def attach_target_for_ensemble(stl_df: pd.DataFrame, model_df: pd.DataFrame) -> pd.DataFrame:
    if "P" in stl_df.columns:
        return stl_df
    return stl_df.merge(model_df[["ts", "P"]], on="ts", how="left")


def main() -> None:
    ensure_output_dirs()

    results: list[dict[str, object]] = []
    detail_frames: list[pd.DataFrame] = []

    for meter_urn in get_all_meters():
        meta = get_metadata(meter_urn)
        if meta is None or meta.get("anomaly_target") is None:
            continue

        logger.info(
            "%s 처리 시작 STL=%s IF=%s LSTM=%s",
            meter_urn,
            "실행" if RUN_STL else "재사용",
            "실행" if RUN_IF else "재사용",
            "실행" if RUN_LSTM else "재사용",
        )

        try:
            df, _, _, _ = preprocess_meter(
                meter_urn,
                print_progress=False,
                print_issue_details=False,
            )
            target_col = str(meta["anomaly_target"])
            meter_type = str(meta["meter_type"])
            valid_df = df.loc[df["is_valid"]].copy()
            feature_cols = select_feature_columns(valid_df, meter_type)

            if valid_df.empty or not feature_cols:
                results.append(
                    {
                        "meter_urn": meter_urn,
                        "total": None,
                        "danger": None,
                        "warning": None,
                        "danger_pct": None,
                        "warning_pct": None,
                        "error": "유효 데이터 없음",
                    }
                )
                logger.warning("%s skip: 유효 데이터 없음", meter_urn)
                continue

            model_df = build_model_df(valid_df, target_col)
            stl_df = get_stl_result(meter_urn, model_df)
            if stl_df is None:
                results.append(
                    {
                        "meter_urn": meter_urn,
                        "total": None,
                        "danger": None,
                        "warning": None,
                        "danger_pct": None,
                        "warning_pct": None,
                        "error": "STL 중간 결과 없음",
                    }
                )
                continue

            if_df = get_if_result(meter_urn, model_df, feature_cols)
            if if_df is None:
                results.append(
                    {
                        "meter_urn": meter_urn,
                        "total": None,
                        "danger": None,
                        "warning": None,
                        "danger_pct": None,
                        "warning_pct": None,
                        "error": "IF 중간 결과 없음",
                    }
                )
                continue

            lstm_df = get_lstm_result(meter_urn, model_df, feature_cols)
            if lstm_df is None:
                results.append(
                    {
                        "meter_urn": meter_urn,
                        "total": None,
                        "danger": None,
                        "warning": None,
                        "danger_pct": None,
                        "warning_pct": None,
                        "error": "LSTM 중간 결과 없음",
                    }
                )
                continue

            ensemble_stl_df = attach_target_for_ensemble(stl_df, model_df)
            ensemble_df = run_ensemble(ensemble_stl_df, if_df, lstm_df)

            detail_df = ensemble_df[
                ["ts", "anomaly_stl", "anomaly_if", "anomaly_lstm", "ensemble_level"]
            ].copy()
            detail_df.insert(1, "meter_urn", meter_urn)
            detail_df["cause_hint"] = detail_df.apply(get_cause_hint, axis=1)
            detail_frames.append(detail_df)

            danger = int((ensemble_df["status"] == "DANGER").sum())
            warning = int((ensemble_df["status"] == "WARNING").sum())
            total = int(len(ensemble_df))

            results.append(
                {
                    "meter_urn": meter_urn,
                    "total": total,
                    "danger": danger,
                    "warning": warning,
                    "danger_pct": round(danger / total * 100, 2) if total else 0.0,
                    "warning_pct": round(warning / total * 100, 2) if total else 0.0,
                    "error": None,
                }
            )
            logger.info("%s 완료: DANGER=%s, WARNING=%s", meter_urn, danger, warning)
        except Exception as exc:
            logger.exception("%s 실패: %s", meter_urn, exc)
            results.append(
                {
                    "meter_urn": meter_urn,
                    "total": None,
                    "danger": None,
                    "warning": None,
                    "danger_pct": None,
                    "warning_pct": None,
                    "error": str(exc),
                }
            )

    df_summary = pd.DataFrame(results)
    df_detail = (
        pd.concat(detail_frames, ignore_index=True)
        if detail_frames
        else pd.DataFrame(
            columns=[
                "ts",
                "meter_urn",
                "anomaly_stl",
                "anomaly_if",
                "anomaly_lstm",
                "ensemble_level",
                "cause_hint",
            ]
        )
    )

    df_summary.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    df_summary.to_csv(SUMMARY_OUTPUT_CSV, index=False, encoding="utf-8-sig")
    df_detail.to_csv(DETAIL_OUTPUT_CSV, index=False, encoding="utf-8-sig")

    logger.info("STL: %s", "실행" if RUN_STL else "CSV 재사용")
    logger.info("IF:  %s", "실행" if RUN_IF else "CSV 재사용")
    logger.info("LSTM:%s", "실행" if RUN_LSTM else "CSV 재사용")
    print(f"결과 저장: {OUTPUT_CSV}")
    print(f"상세 저장: {DETAIL_OUTPUT_CSV}")
    print(f"요약 저장: {SUMMARY_OUTPUT_CSV}")
    print("detail shape:", df_detail.shape)
    print("summary shape:", df_summary.shape)
    print("detail columns:", df_detail.columns.tolist())
    print("detail sample:")
    print(df_detail.head(3).to_string())
    print("cause_hint 분포:")
    print(df_detail["cause_hint"].value_counts())
    print()
    print("WARNING 샘플 3개:")
    print(
        df_detail.loc[df_detail["ensemble_level"] == "WARNING", [
            "ts",
            "meter_urn",
            "anomaly_stl",
            "anomaly_if",
            "anomaly_lstm",
            "ensemble_level",
            "cause_hint",
        ]]
        .head(3)
        .to_string()
    )
    summarize_results(df_summary)


if __name__ == "__main__":
    main()
