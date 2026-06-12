"""
전체 연도(2023) 추론 결과 HTML 리포트 생성.
기존 sample 리포트(inference_report.html)와 별도.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from energy_v84.common.config import ARTIFACTS_DIR

INPUT_DIR   = ARTIFACTS_DIR / "inference_results_full_year"
OUTPUT_HTML = INPUT_DIR / "full_year_report.html"

SEV_COLOR = {"high": "#e74c3c", "medium": "#e67e22", "low": "#f1c40f", "": "#bdc3c7"}


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로드 & 집계
# ─────────────────────────────────────────────────────────────────────────────

def load_full_year() -> pd.DataFrame:
    frames = []
    for h in [1, 3]:
        p = INPUT_DIR / f"predictions_{h}h_full_year.csv"
        if p.exists():
            df = pd.read_csv(p, parse_dates=["timestamp"])
            df["horizon"] = h
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"CSV 없음: {INPUT_DIR}")
    return pd.concat(frames, ignore_index=True)


def build_meter_stats(df: pd.DataFrame) -> pd.DataFrame:
    """계량기 × horizon 기본 통계."""
    rows = []
    for (urn, h), g in df.groupby(["meter_urn", "horizon"]):
        n_total   = len(g)
        n_success = (g["status"] == "success").sum()
        n_insuf   = (g["status"] == "insufficient_data").sum()
        n_error   = (g["status"].isin(["error", "no_artifact"])).sum()

        succ = g[g["status"] == "success"]
        n_high = n_low = 0
        for step in range(1, h + 1):
            wt = f"warning_type_t_plus_{step}"
            if wt in succ.columns:
                n_high += (succ[wt] == "high").sum()
                n_low  += (succ[wt] == "low").sum()
        n_warn = (succ["warning_flag"] == True).sum() if "warning_flag" in succ.columns else 0
        warn_rate = n_warn / n_success * 100 if n_success > 0 else float("nan")

        tags = g["meter_issue_types"].dropna().replace("", float("nan")).dropna()
        tag  = str(tags.iloc[0]) if len(tags) > 0 else ""
        sevs = g["meter_issue_severity"].dropna().replace("", float("nan")).dropna()
        sev  = str(sevs.iloc[0]) if len(sevs) > 0 else ""
        detail_col = g["meter_issue_detail"].dropna().replace("", float("nan")).dropna()
        detail = str(detail_col.iloc[0]) if len(detail_col) > 0 else ""

        rows.append({
            "meter_urn": urn, "horizon": h,
            "n_total": n_total, "n_success": int(n_success),
            "n_insuf": int(n_insuf), "n_error": int(n_error),
            "warn_rate": round(warn_rate, 1),
            "n_high": int(n_high), "n_low": int(n_low), "n_warn": int(n_warn),
            "issue_types": tag, "severity": sev, "issue_detail": detail,
        })
    return pd.DataFrame(rows)


def build_monthly(df: pd.DataFrame) -> dict:
    """horizon → month → meter → {warn_rate, n_high, n_low, n_success}"""
    result = {}
    for h in [1, 3]:
        sub = df[(df["horizon"] == h) & (df["status"] == "success")].copy()
        if sub.empty:
            result[h] = {}
            continue
        sub["month"] = sub["timestamp"].dt.strftime("%Y-%m")
        wf_col = "warning_flag"
        wt_col = f"warning_type_t_plus_1"
        monthly = {}
        for (month, urn), g in sub.groupby(["month", "meter_urn"]):
            n_s  = len(g)
            n_w  = g[wf_col].sum() if wf_col in g.columns else 0
            n_hi = (g[wt_col] == "high").sum() if wt_col in g.columns else 0
            n_lo = (g[wt_col] == "low").sum()  if wt_col in g.columns else 0
            if month not in monthly:
                monthly[month] = {}
            monthly[month][urn] = {
                "warn_rate": round(n_w / n_s * 100, 1) if n_s > 0 else 0,
                "n_high": int(n_hi), "n_low": int(n_lo), "n_success": int(n_s),
            }
        result[h] = monthly
    return result


def build_global_summary(df: pd.DataFrame, stats: pd.DataFrame) -> dict:
    """상단 요약 카드용 지표."""
    out = {}
    for h in [1, 3]:
        s = stats[stats["horizon"] == h]
        n_meter  = s["meter_urn"].nunique()
        avg_warn = s[s["warn_rate"].notna()]["warn_rate"].mean()
        n_hi_meter = (s["warn_rate"] > 30).sum()
        n_tagged = (s["issue_types"] != "").sum()
        n_insuf  = (s["n_insuf"] > 0).sum()
        out[h] = {
            "n_meter": int(n_meter),
            "avg_warn": round(float(avg_warn), 1),
            "n_hi_meter": int(n_hi_meter),
            "n_tagged": int(n_tagged),
            "n_insuf": int(n_insuf),
        }
    return out


def load_meter_tags() -> dict:
    """meter_urn → [(since, until), ...] 태그 구간 목록"""
    p = Path("src/energy_v84/common/meter_tags.csv")
    if not p.exists():
        return {}
    tags = pd.read_csv(p)
    tags["since"] = pd.to_datetime(tags["since"], utc=True, errors="coerce")
    tags["until"] = pd.to_datetime(tags["until"], utc=True, errors="coerce")
    result: dict = {}
    for _, row in tags.iterrows():
        result.setdefault(row["meter_urn"], []).append((row["since"], row["until"]))
    return result


def build_tag_mask(df: pd.DataFrame, tag_dict: dict) -> pd.Series:
    """태그 구간(since~until)에 해당하는 row → True"""
    ts = pd.to_datetime(df["timestamp"], utc=True)
    mask = pd.Series(False, index=df.index)
    for urn, intervals in tag_dict.items():
        m_urn = df["meter_urn"] == urn
        for since, until in intervals:
            m = m_urn.copy()
            if pd.notna(since):
                m &= ts >= since
            if pd.notna(until):
                m &= ts < until
            mask |= m
    return mask


# ─────────────────────────────────────────────────────────────────────────────
# HTML 렌더링
# ─────────────────────────────────────────────────────────────────────────────

def _table_rows(s: pd.DataFrame) -> str:
    rows = []
    for _, r in s.iterrows():
        sc = SEV_COLOR.get(r["severity"], "#bdc3c7")
        issue = r["issue_types"] or "—"
        sev_badge = (f'<span class="badge" style="background:{sc};color:#fff">'
                     f'{r["severity"] or "—"}</span>') if r["severity"] else "—"
        wr = r["warn_rate"]
        wr_style = "color:#e74c3c;font-weight:700" if wr > 30 else ("color:#e67e22" if wr > 10 else "")
        rows.append(f"""<tr>
          <td class="urn">{r['meter_urn']}</td>
          <td style="text-align:right">{r['n_success']:,}</td>
          <td style="text-align:right">{r['n_insuf']:,}</td>
          <td style="text-align:right;{wr_style}">{wr:.1f}%</td>
          <td style="text-align:right">{r['n_high']:,}</td>
          <td style="text-align:right">{r['n_low']:,}</td>
          <td style="font-size:11px;max-width:160px;overflow:hidden;text-overflow:ellipsis"
              title="{r['issue_detail']}">{issue}</td>
          <td>{sev_badge}</td>
        </tr>""")
    return "\n".join(rows)


def render_html(stats: pd.DataFrame, monthly: dict, summary: dict,
                stats_nc: pd.DataFrame, monthly_nc: dict, summary_nc: dict,
                n_excluded: int) -> str:
    s1 = stats[stats["horizon"] == 1].sort_values("warn_rate", ascending=False)
    s3 = stats[stats["horizon"] == 3].sort_values("warn_rate", ascending=False)

    # Plotly용 JSON
    def monthly_heatmap_json(h: int) -> str:
        m = monthly.get(h, {})
        months = sorted(m.keys())
        if not months:
            return "null"
        meters = sorted({u for mo in m.values() for u in mo})
        z   = [[m.get(mo, {}).get(u, {}).get("warn_rate", 0) for u in meters] for mo in months]
        n_hi= [[m.get(mo, {}).get(u, {}).get("n_high", 0) for u in meters] for mo in months]
        n_lo= [[m.get(mo, {}).get(u, {}).get("n_low",  0) for u in meters] for mo in months]
        return json.dumps({"months": months, "meters": meters, "z": z, "n_high": n_hi, "n_low": n_lo})

    def monthly_meter_json(h: int) -> str:
        m = monthly.get(h, {})
        months = sorted(m.keys())
        meters = sorted({u for mo in m.values() for u in mo})
        result = {}
        for u in meters:
            result[u] = {
                "months":    months,
                "warn_rate": [m.get(mo, {}).get(u, {}).get("warn_rate", 0) for mo in months],
                "n_high":    [m.get(mo, {}).get(u, {}).get("n_high", 0)    for mo in months],
                "n_low":     [m.get(mo, {}).get(u, {}).get("n_low",  0)    for mo in months],
            }
        return json.dumps(result)

    def warn_dist_json(h: int) -> str:
        s = stats[stats["horizon"] == h]["warn_rate"].dropna()
        bins   = [0, 5, 10, 20, 30, 50, 100]
        labels = ["0-5%", "5-10%", "10-20%", "20-30%", "30-50%", ">50%"]
        counts = [int(((s >= bins[i]) & (s < bins[i+1])).sum()) for i in range(len(labels))]
        return json.dumps({"labels": labels, "counts": counts})

    def nc_heatmap_json(h: int) -> str:
        m = monthly_nc.get(h, {})
        months = sorted(m.keys())
        if not months:
            return "null"
        meters = sorted({u for mo in m.values() for u in mo})
        z    = [[m.get(mo, {}).get(u, {}).get("warn_rate", None) for u in meters] for mo in months]
        n_hi = [[m.get(mo, {}).get(u, {}).get("n_high", 0)     for u in meters] for mo in months]
        n_lo = [[m.get(mo, {}).get(u, {}).get("n_low",  0)     for u in meters] for mo in months]
        return json.dumps({"months": months, "meters": meters, "z": z, "n_high": n_hi, "n_low": n_lo})

    def nc_meter_json(h: int) -> str:
        m = monthly_nc.get(h, {})
        months = sorted(m.keys())
        meters = sorted({u for mo in m.values() for u in mo})
        result = {}
        for u in meters:
            result[u] = {
                "months":    months,
                "warn_rate": [m.get(mo, {}).get(u, {}).get("warn_rate", 0) for mo in months],
                "n_high":    [m.get(mo, {}).get(u, {}).get("n_high", 0)    for mo in months],
                "n_low":     [m.get(mo, {}).get(u, {}).get("n_low",  0)    for mo in months],
            }
        return json.dumps(result)

    def nc_dist_json(h: int) -> str:
        s = stats_nc[stats_nc["horizon"] == h]["warn_rate"].dropna()
        bins   = [0, 5, 10, 20, 30, 50, 100]
        labels = ["0-5%", "5-10%", "10-20%", "20-30%", "30-50%", ">50%"]
        counts = [int(((s >= bins[i]) & (s < bins[i+1])).sum()) for i in range(len(labels))]
        return json.dumps({"labels": labels, "counts": counts})

    su1, su3 = summary.get(1, {}), summary.get(3, {})
    snc1, snc3 = summary_nc.get(1, {}), summary_nc.get(3, {})
    s1_nc = stats_nc[stats_nc["horizon"] == 1].sort_values("warn_rate", ascending=False)
    s3_nc = stats_nc[stats_nc["horizon"] == 3].sort_values("warn_rate", ascending=False)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Full Year Inference Report — 2023</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#f4f6f9;color:#333;font-size:14px}}
header{{background:#1e2a3a;color:#fff;padding:18px 28px}}
header h1{{font-size:1.35rem;font-weight:600}}
header p{{font-size:0.8rem;opacity:.7;margin-top:4px}}
.container{{max-width:1500px;margin:0 auto;padding:20px 24px}}

/* 요약 카드 */
.cards{{display:flex;gap:14px;margin-bottom:22px;flex-wrap:wrap}}
.card{{background:#fff;border-radius:10px;padding:16px 20px;flex:1;min-width:150px;
       box-shadow:0 1px 4px rgba(0,0,0,.08);border-top:4px solid #ddd}}
.card.blue{{border-color:#3498db}}.card.green{{border-color:#2ecc71}}
.card.orange{{border-color:#e67e22}}.card.red{{border-color:#e74c3c}}
.card.gray{{border-color:#95a5a6}}
.card .val{{font-size:1.9rem;font-weight:700;margin:6px 0 2px;color:#1e2a3a}}
.card .lbl{{font-size:0.75rem;color:#888}}
.card .sub{{font-size:0.72rem;color:#aaa;margin-top:2px}}

/* 탭 */
.tabs{{display:flex;gap:8px;margin-bottom:0}}
.tab-btn{{padding:8px 22px;border:none;border-radius:6px 6px 0 0;cursor:pointer;
          background:#dde3ea;font-size:0.85rem;font-weight:600;color:#555}}
.tab-btn.active{{background:#1e2a3a;color:#fff}}

/* 섹션 */
.section{{background:#fff;border-radius:0 10px 10px 10px;padding:20px 24px;
          box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:24px}}
.section.notab{{border-radius:10px}}
.section h2{{font-size:0.95rem;font-weight:700;color:#1e2a3a;margin-bottom:14px;
             display:flex;align-items:center;gap:8px}}
.section h2 .badge-h{{background:#1e2a3a;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px}}

/* 테이블 */
.tbl-wrap{{overflow-x:auto;margin-top:12px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th{{background:#1e2a3a;color:#fff;padding:8px 10px;text-align:left;white-space:nowrap;position:sticky;top:0}}
td{{padding:6px 10px;border-bottom:1px solid #eef0f3;white-space:nowrap}}
tr:hover td{{background:#f7f9fc}}
.badge{{display:inline-block;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600}}
.urn{{font-family:monospace;font-size:12px}}

/* 계량기 상세 */
.meter-ctrl{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}}
.meter-ctrl label{{font-size:0.83rem;font-weight:600;color:#555}}
select{{padding:7px 12px;border:1px solid #ccd2d9;border-radius:6px;font-size:0.85rem;
        background:#fff;cursor:pointer;min-width:160px}}
.tag-info{{background:#f0f4ff;border:1px solid #c5d3f0;border-radius:6px;
           padding:8px 14px;font-size:12px;color:#2c3e7a;margin-bottom:12px;display:none}}

/* 범례 */
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin-bottom:10px}}
.legend span{{display:flex;align-items:center;gap:5px}}
.dot{{width:13px;height:13px;border-radius:3px;display:inline-block}}

/* 2열 레이아웃 */
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px}}
@media(max-width:900px){{.two-col{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <h1>Full Year Inference Report — 2023 (test period)</h1>
  <p>생성: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp;
     2023-01-01 ~ 2023-12-31 &nbsp;|&nbsp; 8,760 timestamps &nbsp;|&nbsp;
     threshold: val actual P 기준 2~98 percentile</p>
</header>

<div class="container">

<!-- ── 요약 카드 ─────────────────────────────────────────────────────── -->
<div class="cards">
  <div class="card blue">
    <div class="val">{su1.get('n_meter', '—')}</div>
    <div class="lbl">예측 계량기 수</div>
    <div class="sub">전체 63개 기준</div>
  </div>
  <div class="card orange">
    <div class="val">{su1.get('avg_warn', '—')}%</div>
    <div class="lbl">1h 평균 경보율</div>
    <div class="sub">전체 계량기 평균</div>
  </div>
  <div class="card orange">
    <div class="val">{su3.get('avg_warn', '—')}%</div>
    <div class="lbl">3h 평균 경보율</div>
    <div class="sub">전체 계량기 평균</div>
  </div>
  <div class="card red">
    <div class="val">{su1.get('n_hi_meter', '—')}</div>
    <div class="lbl">경보율 30% 초과 계량기</div>
    <div class="sub">1h 기준</div>
  </div>
  <div class="card gray">
    <div class="val">{su1.get('n_tagged', '—')}</div>
    <div class="lbl">이슈 태그 계량기</div>
    <div class="sub">meter_tags.csv 기준</div>
  </div>
  <div class="card gray">
    <div class="val">{su1.get('n_insuf', '—')}</div>
    <div class="lbl">데이터 부족 계량기</div>
    <div class="sub">insufficient_data 포함</div>
  </div>
</div>

<!-- ── 경보율 분포 + 히트맵 (나란히) ──────────────────────────────────── -->
<div class="tabs" id="tabs-top">
  <button class="tab-btn active" onclick="switchTop(1,this)">1h</button>
  <button class="tab-btn" onclick="switchTop(3,this)">3h</button>
</div>
<div class="section" id="sec-top">
  <div class="two-col">
    <div>
      <h2>경보율 분포 <span class="badge-h" id="dist-badge">1h</span></h2>
      <div style="font-size:12px;color:#888;margin-bottom:8px">
        threshold가 너무 민감하면 낮은 경보율 구간(0~5%)의 계량기 수가 줄고 높은 구간이 늘어납니다.
      </div>
      <div id="chart-dist" style="height:260px"></div>
    </div>
    <div>
      <h2>HIGH vs LOW 경보 비율 <span class="badge-h" id="hilo-badge">1h</span></h2>
      <div style="font-size:12px;color:#888;margin-bottom:8px">
        HIGH(예측 > 상한) vs LOW(예측 < 하한) 경보 계량기별 수. 한쪽으로 치우치면 모델 편향 가능성.
      </div>
      <div id="chart-hilo" style="height:260px"></div>
    </div>
  </div>
</div>

<!-- ── 월별 경보율 히트맵 ──────────────────────────────────────────────── -->
<div class="tabs" id="tabs-hm">
  <button class="tab-btn active" onclick="switchHm(1,this)">1h</button>
  <button class="tab-btn" onclick="switchHm(3,this)">3h</button>
</div>
<div class="section">
  <h2>월별 경보율 히트맵 (계량기 × 월) <span class="badge-h" id="hm-badge">1h</span></h2>
  <div style="font-size:12px;color:#888;margin-bottom:8px">
    x축: 계량기 (63개), y축: 월(2023-01 ~ 2023-12). 특정 월만 빨간색이면 계절/이벤트성 이상, 연중 내내 빨간색이면 구조적 문제 계량기.
  </div>
  <div class="legend">
    <span><span class="dot" style="background:#27ae60"></span>0%</span>
    <span><span class="dot" style="background:#f1c40f"></span>~10%</span>
    <span><span class="dot" style="background:#e67e22"></span>~30%</span>
    <span><span class="dot" style="background:#e74c3c"></span>>30%</span>
  </div>
  <div id="chart-hm" style="height:520px"></div>
</div>

<!-- ── 계량기별 통계 테이블 ──────────────────────────────────────────────── -->
<div class="tabs" id="tabs-tbl">
  <button class="tab-btn active" onclick="switchTbl(1,this)">1h</button>
  <button class="tab-btn" onclick="switchTbl(3,this)">3h</button>
</div>
<div class="section">
  <h2>계량기별 통계 <span class="badge-h" id="tbl-badge">1h</span></h2>
  <div style="font-size:12px;color:#888;margin-bottom:10px">
    경보율 = warning_flag=True / 성공 예측 수. HIGH = 예측값이 상한 초과 횟수, LOW = 하한 미달 횟수.
    이슈유형: <b>dormant</b> 미가동, <b>level_change</b> 운영 레벨 급변, <b>sign_flip</b> P 부호 반전,
    <b>data_cutoff</b> DB 데이터 단절, <b>mid_change</b> test 중 설비 변경, <b>variance_spike</b> 간헐적 극단값.
    경보율 <span style="color:#e74c3c;font-weight:700">≥30%</span> /
    <span style="color:#e67e22">≥10%</span> 색으로 구분.
  </div>
  <div id="tbl-1" class="tbl-wrap">
    <table>
      <thead><tr><th>계량기</th><th>성공</th><th>데이터부족</th><th>경보율</th>
      <th>HIGH수</th><th>LOW수</th><th>이슈유형</th><th>심각도</th></tr></thead>
      <tbody>{_table_rows(s1)}</tbody>
    </table>
  </div>
  <div id="tbl-3" class="tbl-wrap" style="display:none">
    <table>
      <thead><tr><th>계량기</th><th>성공</th><th>데이터부족</th><th>경보율</th>
      <th>HIGH수</th><th>LOW수</th><th>이슈유형</th><th>심각도</th></tr></thead>
      <tbody>{_table_rows(s3)}</tbody>
    </table>
  </div>
</div>

<!-- ── 계량기별 월별 상세 ──────────────────────────────────────────────── -->
<div class="tabs" id="tabs-detail">
  <button class="tab-btn active" onclick="switchDetail(1,this)">1h</button>
  <button class="tab-btn" onclick="switchDetail(3,this)">3h</button>
</div>
<div class="section">
  <h2>계량기별 월별 상세</h2>
  <div style="font-size:12px;color:#888;margin-bottom:10px">
    계량기를 선택하면 월별 경보율 추이와 HIGH/LOW 경보 수를 확인할 수 있습니다.
    이슈 태그가 있는 계량기는 파란 박스에 원인이 표시됩니다.
  </div>
  <div class="meter-ctrl">
    <label>계량기</label>
    <select id="meter-sel" onchange="renderMeter()"></select>
    <label><input type="checkbox" id="warn-only" onchange="populateSel()"> 경보 계량기만</label>
    <label><input type="checkbox" id="tagged-only" onchange="populateSel()"> 태그 계량기만</label>
  </div>
  <div class="tag-info" id="tag-info"></div>
  <div class="two-col">
    <div>
      <h2 style="margin-bottom:8px">월별 경보율</h2>
      <div id="chart-meter-warn" style="height:280px"></div>
    </div>
    <div>
      <h2 style="margin-bottom:8px">월별 HIGH / LOW 경보 수</h2>
      <div id="chart-meter-hilo" style="height:280px"></div>
    </div>
  </div>
  <div class="tbl-wrap" style="margin-top:16px">
    <table>
      <thead><tr><th>월</th><th>예측 수</th><th>경보율</th><th>HIGH 수</th><th>LOW 수</th></tr></thead>
      <tbody id="meter-tbl-body"></tbody>
    </table>
  </div>
</div>

</div><!-- /container -->

<!-- ══════════════════════════════════════════════════════════════════════ -->
<!-- 정상 후보 분석 (태그 구간 제외)                                        -->
<!-- ══════════════════════════════════════════════════════════════════════ -->
<div style="background:#1e2a3a;color:#fff;padding:10px 28px;margin-top:8px;
            font-size:0.9rem;font-weight:600;letter-spacing:.04em">
  정상 후보 분석 — 이슈 태그 구간 제외
  <span style="font-weight:400;font-size:0.78rem;opacity:.7;margin-left:12px">
    meter_tags.csv 기준 since~until 구간 제거 후 집계
  </span>
</div>

<div class="container">

<!-- NC 요약 카드 ─────────────────────────────────────────────────────── -->
<div class="cards" style="margin-top:18px">
  <div class="card blue">
    <div class="val">{snc1.get('n_meter','—')}</div>
    <div class="lbl">정상 후보 계량기 수</div>
    <div class="sub">1h 기준 NC 데이터 있는 계량기</div>
  </div>
  <div class="card green">
    <div class="val">{snc1.get('avg_warn','—')}%</div>
    <div class="lbl">1h NC 평균 경보율</div>
    <div class="sub">태그 구간 제외</div>
  </div>
  <div class="card green">
    <div class="val">{snc3.get('avg_warn','—')}%</div>
    <div class="lbl">3h NC 평균 경보율</div>
    <div class="sub">태그 구간 제외</div>
  </div>
  <div class="card red">
    <div class="val">{snc1.get('n_hi_meter','—')}</div>
    <div class="lbl">NC 경보율 30% 초과</div>
    <div class="sub">1h 기준 (태그 제외 후도 높은 계량기)</div>
  </div>
  <div class="card gray">
    <div class="val">{n_excluded}</div>
    <div class="lbl">전 기간 제외 계량기</div>
    <div class="sub">NC 데이터가 0인 계량기</div>
  </div>
</div>

<!-- NC 경보율 분포 + HIGH/LOW ───────────────────────────────────────── -->
<div class="tabs" id="tabs-nc-top">
  <button class="tab-btn active" onclick="switchNcTop(1,this)">1h</button>
  <button class="tab-btn" onclick="switchNcTop(3,this)">3h</button>
</div>
<div class="section" id="sec-nc-top">
  <div class="two-col">
    <div>
      <h2>NC 경보율 분포 <span class="badge-h" id="nc-dist-badge">1h</span></h2>
      <div style="font-size:12px;color:#888;margin-bottom:8px">
        태그 구간 제외 후 계량기별 경보율 분포. 전체 대비 낮은 구간 비율이 높아지면 floor 효과 확인됨.
      </div>
      <div id="chart-nc-dist" style="height:260px"></div>
    </div>
    <div>
      <h2>NC HIGH vs LOW <span class="badge-h" id="nc-hilo-badge">1h</span></h2>
      <div style="font-size:12px;color:#888;margin-bottom:8px">
        태그 제외 후 경보율 상위 20개 계량기의 HIGH/LOW 경보 수.
      </div>
      <div id="chart-nc-hilo" style="height:260px"></div>
    </div>
  </div>
</div>

<!-- NC 월별 히트맵 ─────────────────────────────────────────────────── -->
<div class="tabs" id="tabs-nc-hm">
  <button class="tab-btn active" onclick="switchNcHm(1,this)">1h</button>
  <button class="tab-btn" onclick="switchNcHm(3,this)">3h</button>
</div>
<div class="section">
  <h2>NC 월별 경보율 히트맵 <span class="badge-h" id="nc-hm-badge">1h</span></h2>
  <div style="font-size:12px;color:#888;margin-bottom:8px">
    태그 구간 제외 후 계량기 × 월 히트맵. 태그된 전 기간 계량기는 제외됨.
    회색(null)은 해당 월에 NC 데이터가 없는 구간.
  </div>
  <div class="legend">
    <span><span class="dot" style="background:#27ae60"></span>0%</span>
    <span><span class="dot" style="background:#f1c40f"></span>~10%</span>
    <span><span class="dot" style="background:#e67e22"></span>~30%</span>
    <span><span class="dot" style="background:#e74c3c"></span>>30%</span>
    <span><span class="dot" style="background:#bdc3c7"></span>제외 구간</span>
  </div>
  <div id="chart-nc-hm" style="height:520px"></div>
</div>

<!-- NC 계량기별 통계 테이블 ─────────────────────────────────────────── -->
<div class="tabs" id="tabs-nc-tbl">
  <button class="tab-btn active" onclick="switchNcTbl(1,this)">1h</button>
  <button class="tab-btn" onclick="switchNcTbl(3,this)">3h</button>
</div>
<div class="section">
  <h2>NC 계량기별 통계 <span class="badge-h" id="nc-tbl-badge">1h</span></h2>
  <div style="font-size:12px;color:#888;margin-bottom:10px">
    태그 구간 제외 후 성공 예측 기준. 여기서도 높은 경보율이 나오면 모델/threshold 자체 이슈.
  </div>
  <div id="nc-tbl-1" class="tbl-wrap">
    <table>
      <thead><tr><th>계량기</th><th>성공(NC)</th><th>데이터부족</th><th>경보율</th>
      <th>HIGH수</th><th>LOW수</th><th>이슈유형</th><th>심각도</th></tr></thead>
      <tbody>{_table_rows(s1_nc)}</tbody>
    </table>
  </div>
  <div id="nc-tbl-3" class="tbl-wrap" style="display:none">
    <table>
      <thead><tr><th>계량기</th><th>성공(NC)</th><th>데이터부족</th><th>경보율</th>
      <th>HIGH수</th><th>LOW수</th><th>이슈유형</th><th>심각도</th></tr></thead>
      <tbody>{_table_rows(s3_nc)}</tbody>
    </table>
  </div>
</div>

</div><!-- /nc container -->

<script>
// ── 데이터 ──────────────────────────────────────────────────────────────────
const HM1   = {monthly_heatmap_json(1)};
const HM3   = {monthly_heatmap_json(3)};
const MM1   = {monthly_meter_json(1)};
const MM3   = {monthly_meter_json(3)};
const DIST1 = {warn_dist_json(1)};
const DIST3 = {warn_dist_json(3)};
const NC_HM1   = {nc_heatmap_json(1)};
const NC_HM3   = {nc_heatmap_json(3)};
const NC_MM1   = {nc_meter_json(1)};
const NC_MM3   = {nc_meter_json(3)};
const NC_DIST1 = {nc_dist_json(1)};
const NC_DIST3 = {nc_dist_json(3)};
const NC_STATS1 = {{}};
const NC_STATS3 = {{}};
{chr(10).join(
    'NC_STATS1["{}"] = {{warn_rate:{},issue:"{}",sev:"{}",detail:{}}};'.format(
        r.meter_urn,
        'null' if pd.isna(r.warn_rate) else round(r.warn_rate, 1),
        r.issue_types, r.severity, json.dumps(r.issue_detail)
    )
    for _, r in s1_nc.iterrows()
)}
{chr(10).join(
    'NC_STATS3["{}"] = {{warn_rate:{},issue:"{}",sev:"{}",detail:{}}};'.format(
        r.meter_urn,
        'null' if pd.isna(r.warn_rate) else round(r.warn_rate, 1),
        r.issue_types, r.severity, json.dumps(r.issue_detail)
    )
    for _, r in s3_nc.iterrows()
)}

// stats per meter (for tagged info and select)
const STATS1 = {{}};
const STATS3 = {{}};
{chr(10).join(
    'STATS1["{}"] = {{warn_rate:{},issue:"{}",sev:"{}",detail:{}}};'.format(
        r.meter_urn,
        'null' if pd.isna(r.warn_rate) else round(r.warn_rate, 1),
        r.issue_types, r.severity, json.dumps(r.issue_detail)
    )
    for _, r in s1.iterrows()
)}
{chr(10).join(
    'STATS3["{}"] = {{warn_rate:{},issue:"{}",sev:"{}",detail:{}}};'.format(
        r.meter_urn,
        'null' if pd.isna(r.warn_rate) else round(r.warn_rate, 1),
        r.issue_types, r.severity, json.dumps(r.issue_detail)
    )
    for _, r in s3.iterrows()
)}

const SEV_COLOR = {{high:'#e74c3c',medium:'#e67e22',low:'#f1c40f','':'#bdc3c7'}};
let curH = 1;

// ── 탭 스위치 ─────────────────────────────────────────────────────────────
function setTabActive(grp, btn) {{
  document.querySelectorAll('#' + grp + ' .tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}}
function switchTop(h, btn) {{
  setTabActive('tabs-top', btn);
  document.getElementById('dist-badge').textContent = h+'h';
  document.getElementById('hilo-badge').textContent = h+'h';
  renderDist(h); renderHiLo(h);
}}
function switchHm(h, btn) {{
  setTabActive('tabs-hm', btn);
  document.getElementById('hm-badge').textContent = h+'h';
  renderHm(h);
}}
function switchTbl(h, btn) {{
  setTabActive('tabs-tbl', btn);
  document.getElementById('tbl-badge').textContent = h+'h';
  document.getElementById('tbl-1').style.display = h===1 ? '' : 'none';
  document.getElementById('tbl-3').style.display = h===3 ? '' : 'none';
}}
function switchDetail(h, btn) {{
  setTabActive('tabs-detail', btn);
  curH = h;
  populateSel();
  renderMeter();
}}

// ── 경보율 분포 히스토그램 ─────────────────────────────────────────────────
function renderDist(h) {{
  const d = h===1 ? DIST1 : DIST3;
  const colors = d.counts.map((c,i) => ['#27ae60','#2ecc71','#f1c40f','#e67e22','#e74c3c','#c0392b'][i]);
  Plotly.react('chart-dist', [{{
    type:'bar', x:d.labels, y:d.counts, marker:{{color:colors}},
    text:d.counts, textposition:'outside',
    hovertemplate:'%{{x}}: %{{y}}개 계량기<extra></extra>',
  }}], {{
    margin:{{l:40,r:10,t:10,b:40}},
    yaxis:{{title:'계량기 수', tickfont:{{size:11}}}},
    xaxis:{{tickfont:{{size:11}}}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fafbfc',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── HIGH vs LOW 경보 막대 ──────────────────────────────────────────────────
function renderHiLo(h) {{
  const st = h===1 ? STATS1 : STATS3;
  const meters = Object.keys(st).sort((a,b)=>
    (st[b].warn_rate||0)-(st[a].warn_rate||0)).slice(0,20);
  Plotly.react('chart-hilo', [
    {{type:'bar', name:'HIGH', x:meters,
      y:meters.map(m=>{{const s=h===1?MM1[m]:MM3[m];if(!s)return 0;return s.n_high.reduce((a,b)=>a+b,0)}}),
      marker:{{color:'#e74c3c'}}, hovertemplate:'%{{x}}<br>HIGH: %{{y}}<extra></extra>'}},
    {{type:'bar', name:'LOW',  x:meters,
      y:meters.map(m=>{{const s=h===1?MM1[m]:MM3[m];if(!s)return 0;return s.n_low.reduce((a,b)=>a+b,0)}}),
      marker:{{color:'#3498db'}}, hovertemplate:'%{{x}}<br>LOW: %{{y}}<extra></extra>'}},
  ], {{
    barmode:'stack', margin:{{l:40,r:10,t:10,b:100}},
    xaxis:{{tickangle:-45, tickfont:{{size:10}}}},
    yaxis:{{title:'경보 횟수', tickfont:{{size:11}}}},
    legend:{{orientation:'h',y:1.05}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fafbfc',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── 월별 히트맵 ───────────────────────────────────────────────────────────
function renderHm(h) {{
  const d = h===1 ? HM1 : HM3;
  if(!d){{Plotly.purge('chart-hm');return;}}
  const text = d.months.map((mo,i)=>d.meters.map((u,j)=>
    `${{u}}<br>${{mo}}<br>경보율: ${{d.z[i][j]}}%<br>HIGH: ${{d.n_high[i][j]}} LOW: ${{d.n_low[i][j]}}`
  ));
  Plotly.react('chart-hm', [{{
    type:'heatmap', x:d.meters, y:d.months, z:d.z, text,
    hovertemplate:'%{{text}}<extra></extra>',
    colorscale:[[0,'#eafaf1'],[0.1,'#27ae60'],[0.3,'#f1c40f'],[0.6,'#e67e22'],[1,'#e74c3c']],
    zmin:0, zmax:100,
    colorbar:{{title:'경보율(%)',thickness:14,titlefont:{{size:11}}}},
  }}], {{
    margin:{{l:70,r:80,t:10,b:120}},
    xaxis:{{tickangle:-45,tickfont:{{size:10}}}},
    yaxis:{{tickfont:{{size:11}}}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── 계량기 select 채우기 ─────────────────────────────────────────────────
function populateSel() {{
  const warnOnly  = document.getElementById('warn-only').checked;
  const tagOnly   = document.getElementById('tagged-only').checked;
  const st = curH===1 ? STATS1 : STATS3;
  let meters = Object.keys(st).sort();
  if(warnOnly)  meters = meters.filter(m=>(st[m].warn_rate||0)>0);
  if(tagOnly)   meters = meters.filter(m=>st[m].issue);
  const sel = document.getElementById('meter-sel');
  const prev = sel.value;
  sel.innerHTML = meters.map(m=>{{
    const s=st[m], tag=s.issue?` [${{s.issue}}]`:'', wr=s.warn_rate?` ${{s.warn_rate}}%`:'';
    return `<option value="${{m}}"${{m===prev?' selected':''}}>${{m}}${{wr}}${{tag}}</option>`;
  }}).join('');
  if(!meters.includes(sel.value) && meters.length) sel.value=meters[0];
  renderMeter();
}}

// ── 계량기 상세 렌더링 ────────────────────────────────────────────────────
function renderMeter() {{
  const urn = document.getElementById('meter-sel').value;
  if(!urn) return;
  const mm = curH===1 ? MM1 : MM3;
  const st = curH===1 ? STATS1 : STATS3;
  const d  = mm[urn];
  const s  = st[urn] || {{}};

  // 태그 정보 박스
  const ti = document.getElementById('tag-info');
  if(s.issue) {{
    const sc = SEV_COLOR[s.sev] || '#bdc3c7';
    ti.style.display = '';
    ti.innerHTML = `<strong style="color:${{sc}}">[${{s.sev.toUpperCase()}}]</strong>
      &nbsp;<strong>${{s.issue}}</strong> — ${{s.detail}}`;
  }} else {{
    ti.style.display = 'none';
  }}

  if(!d) {{
    Plotly.purge('chart-meter-warn');
    Plotly.purge('chart-meter-hilo');
    document.getElementById('meter-tbl-body').innerHTML='<tr><td colspan=5>데이터 없음</td></tr>';
    return;
  }}

  // 월별 경보율 바 차트
  const barColors = d.warn_rate.map(w => w>30?'#e74c3c':w>10?'#e67e22':w>0?'#f1c40f':'#27ae60');
  Plotly.react('chart-meter-warn', [{{
    type:'bar', x:d.months, y:d.warn_rate, marker:{{color:barColors}},
    text:d.warn_rate.map(w=>w.toFixed(1)+'%'), textposition:'outside',
    hovertemplate:'%{{x}}<br>경보율: %{{y:.1f}}%<extra></extra>',
  }}], {{
    margin:{{l:50,r:10,t:10,b:60}},
    yaxis:{{title:'경보율(%)',range:[0,Math.max(...d.warn_rate,10)*1.2],tickfont:{{size:11}}}},
    xaxis:{{tickangle:-30,tickfont:{{size:11}}}},
    paper_bgcolor:'#fff',plot_bgcolor:'#fafbfc',
  }}, {{responsive:true,displayModeBar:false}});

  // 월별 HIGH/LOW 스택 바
  Plotly.react('chart-meter-hilo', [
    {{type:'bar',name:'HIGH',x:d.months,y:d.n_high,marker:{{color:'#e74c3c'}},
      hovertemplate:'%{{x}}<br>HIGH: %{{y}}<extra></extra>'}},
    {{type:'bar',name:'LOW', x:d.months,y:d.n_low, marker:{{color:'#3498db'}},
      hovertemplate:'%{{x}}<br>LOW: %{{y}}<extra></extra>'}},
  ], {{
    barmode:'stack',
    margin:{{l:50,r:10,t:10,b:60}},
    yaxis:{{title:'경보 횟수',tickfont:{{size:11}}}},
    xaxis:{{tickangle:-30,tickfont:{{size:11}}}},
    legend:{{orientation:'h',y:1.05}},
    paper_bgcolor:'#fff',plot_bgcolor:'#fafbfc',
  }}, {{responsive:true,displayModeBar:false}});

  // 월별 테이블
  const tbody = document.getElementById('meter-tbl-body');
  tbody.innerHTML = d.months.map((mo,i)=>{{
    const wr = d.warn_rate[i];
    const cls = wr>30?'color:#e74c3c;font-weight:700':wr>10?'color:#e67e22':'';
    return `<tr>
      <td>${{mo}}</td>
      <td style="text-align:right">${{(d.n_high[i]+d.n_low[i])>0 ? (d.n_high[i]+d.n_low[i]+Math.round((1-wr/100)*((d.n_high[i]+d.n_low[i])/(wr/100||1)))) : '—'}}</td>
      <td style="text-align:right;${{cls}}">${{wr.toFixed(1)}}%</td>
      <td style="text-align:right;color:#e74c3c">${{d.n_high[i]}}</td>
      <td style="text-align:right;color:#3498db">${{d.n_low[i]}}</td>
    </tr>`;
  }}).join('');
}}

// ── NC 탭 스위치 ─────────────────────────────────────────────────────────
function switchNcTop(h, btn) {{
  setTabActive('tabs-nc-top', btn);
  document.getElementById('nc-dist-badge').textContent = h+'h';
  document.getElementById('nc-hilo-badge').textContent = h+'h';
  renderNcDist(h); renderNcHiLo(h);
}}
function switchNcHm(h, btn) {{
  setTabActive('tabs-nc-hm', btn);
  document.getElementById('nc-hm-badge').textContent = h+'h';
  renderNcHm(h);
}}
function switchNcTbl(h, btn) {{
  setTabActive('tabs-nc-tbl', btn);
  document.getElementById('nc-tbl-badge').textContent = h+'h';
  document.getElementById('nc-tbl-1').style.display = h===1 ? '' : 'none';
  document.getElementById('nc-tbl-3').style.display = h===3 ? '' : 'none';
}}

// ── NC 경보율 분포 ────────────────────────────────────────────────────────
function renderNcDist(h) {{
  const d = h===1 ? NC_DIST1 : NC_DIST3;
  const colors = d.counts.map((c,i) => ['#27ae60','#2ecc71','#f1c40f','#e67e22','#e74c3c','#c0392b'][i]);
  Plotly.react('chart-nc-dist', [{{
    type:'bar', x:d.labels, y:d.counts, marker:{{color:colors}},
    text:d.counts, textposition:'outside',
    hovertemplate:'%{{x}}: %{{y}}개 계량기<extra></extra>',
  }}], {{
    margin:{{l:40,r:10,t:10,b:40}},
    yaxis:{{title:'계량기 수', tickfont:{{size:11}}}},
    xaxis:{{tickfont:{{size:11}}}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fafbfc',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── NC HIGH vs LOW ────────────────────────────────────────────────────────
function renderNcHiLo(h) {{
  const st = h===1 ? NC_STATS1 : NC_STATS3;
  const mm = h===1 ? NC_MM1   : NC_MM3;
  const meters = Object.keys(st).sort((a,b)=>
    (st[b].warn_rate||0)-(st[a].warn_rate||0)).slice(0,20);
  Plotly.react('chart-nc-hilo', [
    {{type:'bar', name:'HIGH', x:meters,
      y:meters.map(m=>{{const s=mm[m];if(!s)return 0;return s.n_high.reduce((a,b)=>a+b,0)}}),
      marker:{{color:'#e74c3c'}}, hovertemplate:'%{{x}}<br>HIGH: %{{y}}<extra></extra>'}},
    {{type:'bar', name:'LOW', x:meters,
      y:meters.map(m=>{{const s=mm[m];if(!s)return 0;return s.n_low.reduce((a,b)=>a+b,0)}}),
      marker:{{color:'#3498db'}}, hovertemplate:'%{{x}}<br>LOW: %{{y}}<extra></extra>'}},
  ], {{
    barmode:'stack', margin:{{l:40,r:10,t:10,b:100}},
    xaxis:{{tickangle:-45, tickfont:{{size:10}}}},
    yaxis:{{title:'경보 횟수', tickfont:{{size:11}}}},
    legend:{{orientation:'h',y:1.05}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fafbfc',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── NC 월별 히트맵 ────────────────────────────────────────────────────────
function renderNcHm(h) {{
  const d = h===1 ? NC_HM1 : NC_HM3;
  if(!d){{Plotly.purge('chart-nc-hm');return;}}
  const text = d.months.map((mo,i)=>d.meters.map((u,j)=>
    d.z[i][j]===null
      ? `${{u}}<br>${{mo}}<br>제외 구간`
      : `${{u}}<br>${{mo}}<br>경보율: ${{d.z[i][j]}}%<br>HIGH: ${{d.n_high[i][j]}} LOW: ${{d.n_low[i][j]}}`
  ));
  Plotly.react('chart-nc-hm', [{{
    type:'heatmap', x:d.meters, y:d.months, z:d.z, text,
    hovertemplate:'%{{text}}<extra></extra>',
    colorscale:[[0,'#eafaf1'],[0.1,'#27ae60'],[0.3,'#f1c40f'],[0.6,'#e67e22'],[1,'#e74c3c']],
    zmin:0, zmax:100,
    colorbar:{{title:'경보율(%)',thickness:14,titlefont:{{size:11}}}},
  }}], {{
    margin:{{l:70,r:80,t:10,b:120}},
    xaxis:{{tickangle:-45,tickfont:{{size:10}}}},
    yaxis:{{tickfont:{{size:11}}}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── 초기화 ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', ()=>{{
  renderDist(1); renderHiLo(1);
  renderHm(1);
  populateSel();
  renderNcDist(1); renderNcHiLo(1);
  renderNcHm(1);
}});
</script>
</body>
</html>"""


def main():
    print("전체 연도 추론 결과 로드 중...")
    df = load_full_year()
    print(f"  총 {len(df):,}행 (계량기 {df['meter_urn'].nunique()}개, timestamp {df['timestamp'].nunique()}개)")

    print("계량기별 통계 계산 중...")
    stats = build_meter_stats(df)

    print("월별 경보율 매트릭스 계산 중...")
    monthly = build_monthly(df)

    print("전체 요약 계산 중...")
    summary = build_global_summary(df, stats)

    print("정상 후보 (태그 구간 제외) 계산 중...")
    tag_dict = load_meter_tags()
    tag_mask = build_tag_mask(df, tag_dict)
    df_nc    = df[~tag_mask].copy()
    print(f"  NC 행수: {len(df_nc):,} / {len(df):,} (제외 {tag_mask.sum():,}행)")
    stats_nc  = build_meter_stats(df_nc)
    monthly_nc = build_monthly(df_nc)
    summary_nc = build_global_summary(df_nc, stats_nc)
    # 전 기간 제외된 계량기 수 (NC 데이터가 0인 계량기)
    all_urns = set(df["meter_urn"].unique())
    nc_urns  = set(df_nc["meter_urn"].unique()) if len(df_nc) > 0 else set()
    n_excluded = len(all_urns - nc_urns)
    print(f"  NC 계량기: {len(nc_urns)}개 / 전체 {len(all_urns)}개 (전 기간 제외: {n_excluded}개)")

    html = render_html(stats, monthly, summary, stats_nc, monthly_nc, summary_nc, n_excluded)
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"리포트 저장: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
