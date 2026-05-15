"""
3단계 앙상블 이상탐지.

통계(1단계) + Isolation Forest(2단계) + LSTM AE(3단계) 결과를
다수결로 합산해 신뢰도 등급을 부여하고 anomaly_results 테이블에 저장.

신뢰도 등급:
  HIGH  : 3개 모델 모두 이상 탐지
  MEDIUM: 2개 모델 이상 탐지
  LOW   : 1개 모델만 이상 탐지 (FN 최소화 — 기록은 남김)
"""

import os
from datetime import datetime, timezone

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

SEVERITY_MAP = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}


def _ensure_table(conn):
    """anomaly_results 테이블이 없으면 생성."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_results (
            id           SERIAL PRIMARY KEY,
            timestamp    TIMESTAMPTZ NOT NULL,
            meter_id     TEXT NOT NULL DEFAULT 'ensemble',
            anomaly_type TEXT,
            severity     TEXT,
            description  TEXT,
            score_stat   FLOAT,
            score_iso    FLOAT,
            score_lstm   FLOAT,
            vote_count   INT,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_anomaly_ts ON anomaly_results (timestamp DESC);
    """)
    conn.commit()


def _classify_type(row: pd.Series) -> str:
    """피처 값 기반 이상 유형 추정."""
    if "cop" in row and pd.notna(row.get("cop")) and row.get("cop", 99) < 1.0:
        return "COPDrop"
    if "chp_P" in row and pd.notna(row.get("chp_P")) and row.get("chp_P", 1) < 1.0:
        return "CHPOutage"
    ts = row.get("ts")
    if ts is not None:
        hour = pd.to_datetime(ts).hour
        if hour < 6 or hour >= 22:
            pv = row.get("pv_P", 0)
            if pd.notna(pv) and pv > 0:
                return "PVNightNonZero"
            return "NightConsumption"
    grid = row.get("grid_P", 0)
    if pd.notna(grid) and grid > 0:
        return "PowerSpike"
    return "Unknown"


def run(
    df: pd.DataFrame,
    save_to_db: bool = True,
    min_votes: int = 2,
) -> pd.DataFrame:
    """
    3단계 탐지 실행 후 앙상블.

    Parameters
    ----------
    df         : loader.load_reduced() 출력
    save_to_db : True면 anomaly_results 테이블에 저장
    min_votes  : 이 값 이상의 모델이 탐지해야 결과에 포함 (기본 1 = FN 최소화)

    Returns
    -------
    이상 탐지된 행만 담은 DataFrame + vote_count, severity, anomaly_type 컬럼
    """
    try:
        from . import statistical, isolation, lstm_ae
    except ImportError:
        import statistical, isolation, lstm_ae  # type: ignore  # __main__ 실행 시

    print("[Ensemble] 1단계: 통계 기반 탐지...")
    df1 = statistical.detect(df)

    print("[Ensemble] 2단계: Isolation Forest...")
    df2 = isolation.detect(df1)

    print("[Ensemble] 3단계: LSTM AE...")
    df3 = lstm_ae.detect(df2)

    df3["vote_count"] = (
        df3["anomaly_stat"].astype(int) +
        df3["anomaly_iso"].astype(int) +
        df3["anomaly_lstm"].astype(int)
    )
    df3["severity"] = df3["vote_count"].map(SEVERITY_MAP).fillna("")
    anomalies = df3[df3["vote_count"] >= min_votes].copy()
    anomalies["anomaly_type"] = anomalies.apply(_classify_type, axis=1)

    print(f"[Ensemble] 탐지 결과: {len(anomalies)}건 "
          f"(HIGH={( anomalies['severity']=='HIGH').sum()}, "
          f"MEDIUM={(anomalies['severity']=='MEDIUM').sum()}, "
          f"LOW={(anomalies['severity']=='LOW').sum()})")

    if save_to_db and not anomalies.empty:
        _save_results(anomalies)

    return anomalies


def _save_results(anomalies: pd.DataFrame) -> None:
    """anomaly_results 테이블에 결과 저장 (중복 방지: timestamp 기준 upsert)."""
    try:
        conn = psycopg2.connect(DB_URL)
        _ensure_table(conn)
        cur = conn.cursor()

        inserted = 0
        for _, row in anomalies.iterrows():
            ts = pd.to_datetime(row["ts"])
            if ts.tzinfo is None:
                ts = ts.tz_localize("Europe/Berlin")

            description = (
                f"vote={row['vote_count']}/3 | "
                f"stat={'Y' if row.get('anomaly_stat') else 'N'} "
                f"iso={'Y' if row.get('anomaly_iso') else 'N'} "
                f"lstm={'Y' if row.get('anomaly_lstm') else 'N'}"
            )

            cur.execute("""
                INSERT INTO anomaly_results
                    (timestamp, meter_id, anomaly_type, severity, description,
                     score_stat, score_iso, score_lstm, vote_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING;
            """, (
                ts.isoformat(),
                "ensemble",
                str(row.get("anomaly_type", "Unknown")),
                str(row.get("severity", "LOW")),
                description,
                float(row.get("score_stat", 0)),
                float(row.get("score_iso", 0)),
                float(row.get("score_lstm", 0)),
                int(row.get("vote_count", 0)),
            ))
            inserted += cur.rowcount

        conn.commit()
        conn.close()
        print(f"[Ensemble] DB 저장: {inserted}건 → anomaly_results")
    except Exception as e:
        print(f"[Ensemble] DB 저장 실패: {e}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range
    from models.anomaly import statistical, isolation, lstm_ae  # noqa: F401

    df = load_range("2022-07-01", "2022-08-01")
    anomalies = run(df, save_to_db=True)
    print("\n상위 10건:")
    cols = ["ts", "vote_count", "severity", "anomaly_type",
            "score_stat", "score_iso", "score_lstm"]
    print(anomalies[cols].head(10).to_string(index=False))
