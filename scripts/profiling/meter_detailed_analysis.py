"""계량기별 상세 데이터 분석 및 시각화 스크립트.

기초 통계량 추출, 시계열 시각화, 그룹별 트렌드 비교 및 상관관계 히트맵을 생성합니다.

Usage:
    uv run --with pandas --with "psycopg[binary]" --with python-dotenv --with matplotlib --with seaborn \
           python scripts/profiling/meter_detailed_analysis.py
"""

import os
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import psycopg
from dotenv import load_dotenv
from pathlib import Path

# 출력 경로 설정
OUTPUT_DIR_PROFILING = Path("outputs/profiling")
OUTPUT_DIR_FIGURES = Path("outputs/figures/meters")
OUTPUT_DIR_INDIVIDUAL = OUTPUT_DIR_FIGURES / "individual"

OUTPUT_DIR_PROFILING.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR_INDIVIDUAL.mkdir(parents=True, exist_ok=True)

load_dotenv()

CONNECT_KWARGS = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

# 공통 설정
plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams["font.size"] = 10
plt.rcParams["figure.dpi"] = 150


def export_statistics():
    print("▶ DB에서 기초 통계량 집계 중 (SQL)...")
    sql = """
        SELECT
            meter_urn,
            measurement,
            COUNT(*) as count,
            AVG(value) as mean,
            STDDEV(value) as std,
            MIN(value) as min,
            MAX(value) as max,
            AVG(CASE WHEN value = 0 THEN 1.0 ELSE 0.0 END) as zero_ratio
        FROM ems.cr_measurement_1h
        GROUP BY meter_urn, measurement
    """
    with psycopg.connect(**CONNECT_KWARGS) as conn:
        stats = pd.read_sql(sql, conn)
        
    out_path = OUTPUT_DIR_PROFILING / "07_meter_statistics.csv"
    stats.to_csv(out_path, index=False)
    print(f"  - 통계량 저장 완료: {out_path}")


def load_power_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    print("▶ DB에서 전력(P) 데이터만 로드 중...")
    with psycopg.connect(**CONNECT_KWARGS) as conn:
        df_p = pd.read_sql("SELECT ts, meter_urn, value FROM ems.cr_measurement_1h WHERE measurement = 'P'", conn)
        
    df_registry = pd.read_csv(OUTPUT_DIR_PROFILING / "01_meter_registry.csv")
    
    df_p["ts"] = pd.to_datetime(df_p["ts"], utc=True)
    df_p["measurement"] = "P"
    return df_p, df_registry


def analyze_power(df: pd.DataFrame, df_registry: pd.DataFrame):
    # 전력(P) 데이터만 추출하여 시계열 및 상관관계 분석
    print("▶ 전력(P) 데이터 기반 시각화 진행 중...")
    df_p = df[df["measurement"] == "P"].copy()
    if df_p.empty:
        print("  - 'P' (Power) 측정 항목이 없어 시각화를 건너뜁니다.")
        return

    # Pivot table for time series: index=ts, columns=meter_urn, values=value
    df_pivot = df_p.pivot_table(index="ts", columns="meter_urn", values="value", aggfunc="mean")
    
    # 1. 개별 시각화 (Weekly Resample to reduce noise)
    print("  - 개별 미터 시계열 차트 생성 중 (Weekly Avg)...")
    df_weekly = df_pivot.resample("W-MON").mean()
    for col in df_weekly.columns:
        plt.figure(figsize=(10, 4))
        plt.plot(df_weekly.index, df_weekly[col], color="royalblue", linewidth=1.5)
        plt.title(f"Meter: {col} - Power (P) Weekly Avg")
        plt.ylabel("Power (W)")
        plt.xlabel("Time")
        plt.tight_layout()
        plt.savefig(OUTPUT_DIR_INDIVIDUAL / f"{col}_weekly.png")
        plt.close()
        
    # 2. 그룹별 트렌드 (Monthly Resample)
    print("  - 그룹별 사용량 트렌드 비교...")
    df_monthly = df_pivot.resample("ME").sum() / 1000  # kWh roughly
    
    groups = df_registry.groupby("equipment_group")["meter_urn"].apply(list).to_dict()
    
    for group_name, meters in groups.items():
        valid_meters = [m for m in meters if m in df_monthly.columns]
        if not valid_meters:
            continue
            
        plt.figure(figsize=(12, 6))
        for m in valid_meters:
            plt.plot(df_monthly.index, df_monthly[m], label=m, linewidth=2)
            
        plt.title(f"Group Comparison: {group_name} (Monthly Energy)")
        plt.ylabel("Monthly Energy (kWh)")
        plt.xlabel("Time")
        plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
        plt.tight_layout()
        plt.savefig(OUTPUT_DIR_FIGURES / f"group_comparison_{group_name}.png")
        plt.close()
        
    # 3. 상관관계 히트맵 (상위 30개 미터 위주)
    print("  - 상관관계 히트맵 생성 중...")
    top_30_meters = df_pivot.mean().nlargest(30).index
    corr_matrix = df_pivot[top_30_meters].corr()
    
    plt.figure(figsize=(14, 12))
    sns.heatmap(corr_matrix, cmap="coolwarm", center=0, annot=False, fmt=".2f")
    plt.title("Correlation Heatmap (Top 30 Power Meters)")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR_FIGURES / "meter_correlation_heatmap.png")
    plt.close()
    
    # 4. Boxplot (Seasonality) - 상위 20개 미터의 시간대별 패턴
    print("  - 상위 20개 미터 시간대별 패턴(Boxplot) 생성 중...")
    top_20_meters = df_pivot.mean().nlargest(20).index
    df_top20 = df_p[df_p["meter_urn"].isin(top_20_meters)].copy()
    df_top20["hour"] = df_top20["ts"].dt.hour
    
    g = sns.catplot(
        data=df_top20,
        x="hour",
        y="value",
        col="meter_urn",
        col_wrap=5,
        kind="box",
        sharey=False,
        height=3,
        aspect=1.2,
        palette="viridis",
        fliersize=1
    )
    g.fig.suptitle("Hourly Power Consumption Pattern (Top 20 Meters)", y=1.02)
    g.set_axis_labels("Hour of Day", "Power (W)")
    g.set_titles("{col_name}")
    plt.savefig(OUTPUT_DIR_FIGURES / "seasonality_top20.png", bbox_inches="tight")
    plt.close()
    
    print("▶ 개별 미터 분석 및 시각화 완료!")


def main():
    export_statistics()
    df_p, df_registry = load_power_data()
    analyze_power(df_p, df_registry)


if __name__ == "__main__":
    main()
