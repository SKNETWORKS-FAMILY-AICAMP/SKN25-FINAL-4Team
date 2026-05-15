"""건물 전체 에너지 흐름 시각화.

Reduced Aggregated View (ems.reduced_measurement_1h)를 활용하여
6년간의 에너지 소비/생산 패턴, 계절적 변화, Regime 경계를 시각화한다.

Usage:
    UV_CACHE_DIR=.uv_cache UV_PYTHON_INSTALL_DIR=.uv_python \
    uv run --no-project --python /usr/bin/python3 \
    --with pandas --with "psycopg[binary]" --with python-dotenv --with matplotlib \
    -- python3 scripts/profiling/visualize_energy_flow.py
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

# ── 설정 ─────────────────────────────────────────────────
CONNECT_KWARGS = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

OUT_DIR = Path("outputs/figures/energy_flow")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Regime 경계선
REGIME_EVENTS = [
    ("2019-02-13", "CHP Logic"),
    ("2019-06-01", "PV Phase1"),
    ("2020-03-01", "COVID"),
    ("2020-06-01", "PV Phase2"),
    ("2020-09-09", "Meter Swap"),
    ("2023-06-01", "Heat Mod."),
]

# 스타일
plt.rcParams.update({
    "figure.facecolor": "#0d1117",
    "axes.facecolor": "#161b22",
    "axes.edgecolor": "#30363d",
    "axes.labelcolor": "#c9d1d9",
    "xtick.color": "#8b949e",
    "ytick.color": "#8b949e",
    "text.color": "#c9d1d9",
    "grid.color": "#21262d",
    "grid.alpha": 0.6,
    "legend.facecolor": "#161b22",
    "legend.edgecolor": "#30363d",
    "legend.labelcolor": "#c9d1d9",
    "font.size": 11,
})


def query_df(sql: str, params=None) -> pd.DataFrame:
    with psycopg.connect(**CONNECT_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc.name for desc in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)


def add_regime_lines(ax):
    """Regime 경계를 세로 점선으로 추가."""
    for date_str, label in REGIME_EVENTS:
        dt = pd.Timestamp(date_str)
        ax.axvline(dt, color="#f0883e", linewidth=0.8, linestyle="--", alpha=0.7, zorder=1)
        ax.text(
            dt, ax.get_ylim()[1] * 0.95, f" {label}",
            fontsize=7, color="#f0883e", alpha=0.8,
            rotation=90, va="top", ha="left",
        )


def load_reduced_data() -> pd.DataFrame:
    """reduced_measurement_1h 뷰에서 전체 데이터 로드."""
    print("DB에서 Reduced View 데이터 로딩 중...")
    sql = """
    SELECT ts, category, subcategory, measurement, value
    FROM ems.reduced_measurement_1h
    WHERE measurement IN ('P', 'W', 'Ta', 'Igm')
    ORDER BY ts
    """
    df = query_df(sql)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    print(f"  로드 완료: {len(df):,} rows, {df['ts'].min()} ~ {df['ts'].max()}")
    return df


# ── 1. 전기 소비/생산 6년 추이 ─────────────────────────────

def plot_electricity_overview(df: pd.DataFrame):
    """전기: 외부 전력, PV 발전, CHP 발전 (일간 평균 kW)."""
    elec = df[(df["category"] == "electricity") & (df["measurement"] == "P")].copy()
    if elec.empty:
        print("  전기 데이터 없음, 스킵")
        return

    pivot = elec.pivot_table(
        index="ts", columns="subcategory", values="value", aggfunc="sum"
    )
    # W → kW 변환
    pivot = pivot / 1000

    # 일간 평균으로 리샘플링 (노이즈 감소)
    daily = pivot.resample("1D").mean()

    fig, ax = plt.subplots(figsize=(18, 6))
    colors = {"total": "#58a6ff", "pv": "#3fb950", "chp": "#f78166"}
    labels = {"total": "Grid Import", "pv": "PV Generation", "chp": "CHP Generation"}

    for col in ["total", "pv", "chp"]:
        if col in daily.columns:
            ax.plot(daily.index, daily[col], linewidth=0.8, color=colors[col],
                    label=labels[col], alpha=0.9)

    add_regime_lines(ax)
    ax.set_ylabel("Power (kW, daily avg)")
    ax.set_title("Electricity Consumption & Production (6-Year Overview)", fontsize=14, fontweight="bold", pad=12)
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "01_electricity_overview.png", dpi=150)
    plt.close(fig)
    print("  ✅ 01_electricity_overview.png")


# ── 2. 난방 / 냉방 부하 계절 패턴 ─────────────────────────

def plot_heating_cooling(df: pd.DataFrame):
    """난방 총생산 vs 냉방 총생산 (일간 평균 kW)."""
    heat = df[(df["category"] == "heating") & (df["subcategory"] == "total") & (df["measurement"] == "P")].copy()
    cool = df[(df["category"] == "cooling") & (df["subcategory"] == "total") & (df["measurement"] == "P")].copy()

    fig, ax = plt.subplots(figsize=(18, 5))

    if not heat.empty:
        h_daily = heat.set_index("ts")["value"].resample("1D").mean() / 1000
        ax.fill_between(h_daily.index, 0, h_daily, alpha=0.5, color="#f47067", label="Heating Load")
    if not cool.empty:
        c_daily = cool.set_index("ts")["value"].resample("1D").mean() / 1000
        ax.fill_between(c_daily.index, 0, -c_daily, alpha=0.5, color="#58a6ff", label="Cooling Load")

    add_regime_lines(ax)
    ax.axhline(0, color="#30363d", linewidth=0.5)
    ax.set_ylabel("Thermal Power (kW, daily avg)")
    ax.set_title("Heating / Cooling Load (Seasonal Pattern)", fontsize=14, fontweight="bold", pad=12)
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "02_heating_cooling.png", dpi=150)
    plt.close(fig)
    print("  ✅ 02_heating_cooling.png")


# ── 3. 기상 데이터 (기온 + 일사량) ─────────────────────────

def plot_weather(df: pd.DataFrame):
    """기온(Ta)과 일사량(Igm) 추이 + 결측 구간 시각화."""
    weather = df[df["category"] == "weather"].copy()
    if weather.empty:
        print("  기상 데이터 없음, 스킵")
        return

    ta = weather[weather["measurement"] == "Ta"].set_index("ts")["value"]
    igm = weather[weather["measurement"] == "Igm"].set_index("ts")["value"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 7), sharex=True)

    # 기온
    if not ta.empty:
        ta_daily = ta.resample("1D").mean()
        ax1.plot(ta_daily.index, ta_daily, linewidth=0.7, color="#f0883e", alpha=0.9)
        ax1.set_ylabel("Temperature (°C)")
        ax1.set_title("Weather Data (6-Year Overview)", fontsize=14, fontweight="bold", pad=12)
        ax1.grid(True, alpha=0.3)

        # 결측 구간 하이라이트
        missing = ta_daily.isna()
        if missing.any():
            ax1.fill_between(ta_daily.index, ax1.get_ylim()[0], ax1.get_ylim()[1],
                           where=missing, alpha=0.3, color="#da3633", label="Missing")
            ax1.legend(loc="upper right")

    # 일사량
    if not igm.empty:
        igm_daily = igm.resample("1D").mean()
        ax2.plot(igm_daily.index, igm_daily, linewidth=0.7, color="#d2a8ff", alpha=0.9)
        ax2.set_ylabel("Irradiance (W/m2)")
        ax2.grid(True, alpha=0.3)

    add_regime_lines(ax1)
    add_regime_lines(ax2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "03_weather.png", dpi=150)
    plt.close(fig)
    print("  ✅ 03_weather.png")


# ── 4. 월별 에너지 수지 요약 ──────────────────────────────

def plot_monthly_energy_balance(df: pd.DataFrame):
    """월별 전기 소비(grid) vs PV + CHP 생산."""
    elec = df[(df["category"] == "electricity") & (df["measurement"] == "P")].copy()
    if elec.empty:
        return

    pivot = elec.pivot_table(
        index="ts", columns="subcategory", values="value", aggfunc="sum"
    ) / 1000  # kW

    monthly = pivot.resample("ME").mean()
    months = monthly.index

    fig, ax = plt.subplots(figsize=(18, 6))
    width = 20  # days

    if "total" in monthly.columns:
        ax.bar(months, monthly["total"], width=width, color="#58a6ff",
               alpha=0.8, label="Grid Import", zorder=2)
    if "pv" in monthly.columns:
        ax.bar(months, -monthly["pv"].fillna(0), width=width, color="#3fb950",
               alpha=0.8, label="PV Generation", zorder=2)
    if "chp" in monthly.columns:
        ax.bar(months, -monthly["chp"].fillna(0), width=width, color="#f78166",
               alpha=0.8, label="CHP Generation", bottom=-monthly["pv"].fillna(0), zorder=2)

    add_regime_lines(ax)
    ax.axhline(0, color="#8b949e", linewidth=0.5)
    ax.set_ylabel("Power (kW, monthly avg)")
    ax.set_title("Monthly Electricity Balance", fontsize=14, fontweight="bold", pad=12)
    ax.legend(loc="upper left")
    ax.grid(True, axis="y", alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "04_monthly_energy_balance.png", dpi=150)
    plt.close(fig)
    print("  ✅ 04_monthly_energy_balance.png")


# ── 5. 대표 주간 상세 (2021년 3월 첫째 주, 논문 Fig.8 재현) ───

def plot_representative_week(df: pd.DataFrame):
    """2021년 3월 1~7일 상세 패턴 (시간별)."""
    start = "2021-03-01"
    end = "2021-03-08"

    week = df[(df["ts"] >= start) & (df["ts"] < end)].copy()
    if week.empty:
        print("  대표 주간 데이터 없음, 스킵")
        return

    fig, axes = plt.subplots(3, 1, figsize=(18, 10), sharex=True)

    # Panel 1: 전기
    elec = week[(week["category"] == "electricity") & (week["measurement"] == "P")]
    if not elec.empty:
        pivot = elec.pivot_table(index="ts", columns="subcategory", values="value") / 1000
        colors = {"total": "#58a6ff", "pv": "#3fb950", "chp": "#f78166"}
        labels = {"total": "Grid", "pv": "PV", "chp": "CHP"}
        for col in ["total", "pv", "chp"]:
            if col in pivot.columns:
                axes[0].plot(pivot.index, pivot[col], linewidth=1.2,
                           color=colors[col], label=labels[col])
        axes[0].set_ylabel("Power (kW)")
        axes[0].legend(loc="upper right", fontsize=9)
        axes[0].set_title("Representative Week (2021-03-01 ~ 03-07)", fontsize=14,
                         fontweight="bold", pad=12)

    # Panel 2: 난방/냉방
    heat = week[(week["category"] == "heating") & (week["subcategory"] == "total") & (week["measurement"] == "P")]
    cool = week[(week["category"] == "cooling") & (week["subcategory"] == "total") & (week["measurement"] == "P")]
    if not heat.empty:
        axes[1].plot(heat["ts"], heat["value"] / 1000, color="#f47067",
                    linewidth=1.2, label="Heating")
    if not cool.empty:
        axes[1].plot(cool["ts"], -cool["value"] / 1000, color="#58a6ff",
                    linewidth=1.2, label="Cooling")
    axes[1].set_ylabel("Thermal (kW)")
    axes[1].legend(loc="upper right", fontsize=9)
    axes[1].axhline(0, color="#30363d", linewidth=0.5)

    # Panel 3: 기상
    ta = week[(week["category"] == "weather") & (week["measurement"] == "Ta")]
    igm = week[(week["category"] == "weather") & (week["measurement"] == "Igm")]
    if not ta.empty:
        axes[2].plot(ta["ts"], ta["value"], color="#f0883e", linewidth=1.2, label="Temp (C)")
    ax2r = axes[2].twinx()
    if not igm.empty:
        ax2r.fill_between(igm["ts"], 0, igm["value"], alpha=0.3, color="#d2a8ff", label="Irradiance")
        ax2r.set_ylabel("Irradiance (W/m2)", color="#d2a8ff")
    axes[2].set_ylabel("Temp (C)")
    axes[2].legend(loc="upper left", fontsize=9)
    ax2r.legend(loc="upper right", fontsize=9)

    for ax in axes:
        ax.grid(True, alpha=0.3)
        # 주말 하이라이트
        for d in pd.date_range(start, end, freq="D"):
            if d.weekday() >= 5:
                ax.axvspan(d, d + pd.Timedelta(days=1),
                          alpha=0.08, color="#8b949e", zorder=0)

    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "05_representative_week.png", dpi=150)
    plt.close(fig)
    print("  ✅ 05_representative_week.png")


# ── 6. 냉각 전력 vs 외기온 상관 ──────────────────────────────

def plot_cooling_vs_temperature(df: pd.DataFrame):
    """냉각기 전력 소비와 외기온의 상관관계."""
    cool_elec = df[(df["category"] == "cooling") & (df["subcategory"] == "cool_elec")
                   & (df["measurement"] == "P")].copy()
    ta = df[(df["category"] == "weather") & (df["measurement"] == "Ta")].copy()

    if cool_elec.empty or ta.empty:
        print("  냉각/기온 데이터 없음, 스킵")
        return

    cool_daily = cool_elec.set_index("ts")["value"].resample("1D").mean() / 1000
    ta_daily = ta.set_index("ts")["value"].resample("1D").mean()

    merged = pd.concat([cool_daily.rename("cool_kw"), ta_daily.rename("ta")], axis=1).dropna()

    fig, ax = plt.subplots(figsize=(10, 8))
    sc = ax.scatter(merged["ta"], merged["cool_kw"], s=4, alpha=0.4,
                   c=merged.index.month, cmap="twilight", edgecolors="none")
    cbar = fig.colorbar(sc, ax=ax, label="Month")
    cbar.ax.yaxis.label.set_color("#c9d1d9")
    cbar.ax.tick_params(colors="#8b949e")

    ax.set_xlabel("Outdoor Temperature (C)")
    ax.set_ylabel("Cooling Power (kW, daily avg)")
    ax.set_title("Cooling Power vs Outdoor Temperature", fontsize=14, fontweight="bold", pad=12)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "06_cooling_vs_temperature.png", dpi=150)
    plt.close(fig)
    print("  ✅ 06_cooling_vs_temperature.png")


# ── 메인 ───────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("EMS 에너지 흐름 시각화 시작")
    print("=" * 60)

    df = load_reduced_data()

    print("\n[1/6] 전기 소비/생산 6년 추이...")
    plot_electricity_overview(df)

    print("[2/6] 난방/냉방 계절 패턴...")
    plot_heating_cooling(df)

    print("[3/6] 기상 데이터...")
    plot_weather(df)

    print("[4/6] 월별 에너지 수지...")
    plot_monthly_energy_balance(df)

    print("[5/6] 대표 주간 상세 (2021-03 첫째 주)...")
    plot_representative_week(df)

    print("[6/6] 냉각 전력 vs 외기온 상관...")
    plot_cooling_vs_temperature(df)

    print(f"\n산출물 저장 위치: {OUT_DIR.resolve()}")
    print("완료!")


if __name__ == "__main__":
    main()
