"""
추론 결과 HTML 리포트 생성.

사용법:
  conda run -n skn25 python -m energy_v84.generate_inference_report
"""
import json
import pathlib
import pandas as pd
import numpy as np
from datetime import datetime

from energy_v84.common.config import ARTIFACTS_DIR

RESULTS_DIR = ARTIFACTS_DIR / "inference_results"
OUTPUT_PATH = RESULTS_DIR / "inference_report.html"


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_all() -> pd.DataFrame:
    dfs = []
    for f in sorted(RESULTS_DIR.glob("predictions_*.csv")):
        stem = f.stem  # predictions_1h_20230601T0900
        parts = stem.split("_")
        horizon = int(parts[1].replace("h", ""))
        ts_str  = parts[2]                          # 20230601T0900
        ts = datetime.strptime(ts_str, "%Y%m%dT%H%M")
        df = pd.read_csv(f)
        df["horizon"]  = horizon
        df["pred_ts"]  = ts.strftime("%Y-%m-%d %H:%M")
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# HTML 생성
# ─────────────────────────────────────────────────────────────────────────────

def build_html(df: pd.DataFrame) -> str:
    meters    = sorted(df["meter_urn"].unique())
    horizons  = sorted(df["horizon"].unique())
    all_ts    = {h: sorted(df[df["horizon"]==h]["pred_ts"].unique()) for h in horizons}

    # 전체 데이터를 JSON으로 직렬화
    data_json = df.replace({np.nan: None}).to_dict(orient="records")

    # 요약 통계
    total_meters = len(meters)
    warn_counts  = {}
    for h in horizons:
        sub = df[df["horizon"] == h]
        total_cases = len(sub)
        warn_cases  = sub["warning_flag"].sum()
        warn_counts[int(h)] = {"total": int(total_cases), "warned": int(warn_cases),
                               "rate": round(float(warn_cases) / total_cases * 100, 1) if total_cases else 0}

    summary_json = json.dumps(warn_counts)
    meters_json  = json.dumps(meters)
    ts_json      = json.dumps({str(k): v for k, v in all_ts.items()})
    horizons_json = json.dumps([int(h) for h in horizons])

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Inference Report — energy_v84</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', sans-serif; background: #f4f6f9; color: #333; }}
header {{ background: #1e2a3a; color: #fff; padding: 18px 28px; }}
header h1 {{ font-size: 1.4rem; font-weight: 600; }}
header p  {{ font-size: 0.82rem; opacity: 0.7; margin-top: 4px; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px 24px; }}

/* 요약 카드 */
.summary-grid {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
.card {{ background: #fff; border-radius: 10px; padding: 18px 22px; flex: 1; min-width: 180px;
         box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
.card .val {{ font-size: 2rem; font-weight: 700; margin: 6px 0 2px; }}
.card .lbl {{ font-size: 0.78rem; color: #777; }}
.card.warn .val {{ color: #e05a3a; }}
.card.ok   .val {{ color: #2ecc71; }}
.card.info .val {{ color: #3498db; }}

/* 탭 */
.tabs {{ display: flex; gap: 8px; margin-bottom: 16px; }}
.tab-btn {{ padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer;
            background: #dde3ea; font-size: 0.88rem; font-weight: 600; color: #555; }}
.tab-btn.active {{ background: #1e2a3a; color: #fff; }}

/* 섹션 박스 */
.section {{ background: #fff; border-radius: 10px; padding: 20px 24px;
            box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 24px; }}
.section h2 {{ font-size: 1rem; font-weight: 700; margin-bottom: 14px; color: #1e2a3a; }}

/* 히트맵 */
#heatmap-container {{ width: 100%; overflow-x: auto; }}

/* 계량기 선택 + 상세 */
.meter-controls {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }}
.meter-controls label {{ font-size: 0.85rem; font-weight: 600; color: #555; }}
select {{ padding: 7px 12px; border: 1px solid #ccd2d9; border-radius: 6px;
          font-size: 0.88rem; background: #fff; cursor: pointer; }}
#warn-only-cb {{ margin-right: 4px; cursor: pointer; }}

/* 계량기 상세 테이블 */
#detail-table-wrap {{ overflow-x: auto; margin-top: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{ background: #1e2a3a; color: #fff; padding: 8px 10px; text-align: left; white-space: nowrap; }}
td {{ padding: 6px 10px; border-bottom: 1px solid #eee; white-space: nowrap; }}
tr:hover td {{ background: #f7f9fc; }}
.warn-row td {{ background: #fff4f2; }}
.phys-row td {{ background: #fffbf0; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
          font-size: 0.75rem; font-weight: 700; }}
.badge-warn {{ background: #fde8e3; color: #c0392b; }}
.badge-high {{ background: #fde8e3; color: #c0392b; }}
.badge-low  {{ background: #e8f0fd; color: #2980b9; }}
.badge-none {{ background: #eaf7ee; color: #27ae60; }}
.badge-phys {{ background: #fef9e3; color: #d35400; }}
</style>
</head>
<body>

<header>
  <h1>Inference Report — energy_v84</h1>
  <p>생성: {datetime.now().strftime("%Y-%m-%d %H:%M")} | 계량기 {total_meters}개 | 2~98 percentile threshold (val actual P 기준)</p>
</header>

<div class="container">

<!-- ── 요약 카드 ─────────────────────────────────────────────────── -->
<div class="summary-grid" id="summary-cards"></div>

<!-- ── horizon 탭 ──────────────────────────────────────────────── -->
<div class="tabs" id="horizon-tabs"></div>

<!-- ── 히트맵 ────────────────────────────────────────────────────── -->
<div class="section">
  <h2>경보 현황 히트맵 (계량기 × timestamp)</h2>
  <div style="display:flex;gap:16px;align-items:center;margin-bottom:10px;font-size:0.82rem;">
    <span style="display:flex;align-items:center;gap:5px"><span style="width:16px;height:16px;background:#2ecc71;display:inline-block;border-radius:3px"></span>정상</span>
    <span style="display:flex;align-items:center;gap:5px"><span style="width:16px;height:16px;background:#f1c40f;display:inline-block;border-radius:3px"></span>물리이상만</span>
    <span style="display:flex;align-items:center;gap:5px"><span style="width:16px;height:16px;background:#e67e22;display:inline-block;border-radius:3px"></span>LOW 경보</span>
    <span style="display:flex;align-items:center;gap:5px"><span style="width:16px;height:16px;background:#e74c3c;display:inline-block;border-radius:3px"></span>HIGH 경보</span>
    <span style="display:flex;align-items:center;gap:5px"><span style="width:16px;height:16px;background:#c8d6e5;display:inline-block;border-radius:3px"></span>데이터없음</span>
  </div>
  <div id="heatmap-container"><div id="heatmap-chart" style="height:460px"></div></div>
</div>

<!-- ── 계량기 상세 ──────────────────────────────────────────────── -->
<div class="section">
  <h2>계량기별 상세</h2>
  <div class="meter-controls">
    <label>계량기 선택</label>
    <select id="meter-select"></select>
    <label><input type="checkbox" id="warn-only-cb"> 경보 계량기만</label>
  </div>
  <div id="meter-chart" style="height:360px"></div>
  <div id="detail-table-wrap"></div>
</div>

</div><!-- /container -->

<script>
const RAW   = {json.dumps(data_json, ensure_ascii=False)};
const METERS= {meters_json};
const TS_MAP= {ts_json};
const HORIZONS= {horizons_json};
const WARN_CNT= {summary_json};

let curHorizon = HORIZONS[0];
let curMeter   = METERS[0];

// ── 요약 카드 ────────────────────────────────────────────────────────────
function renderSummaryCards() {{
  const el = document.getElementById('summary-cards');
  let html = `<div class="card info"><div class="val">${{METERS.length}}</div><div class="lbl">예측 계량기</div></div>`;
  HORIZONS.forEach(h => {{
    const s = WARN_CNT[h];
    html += `<div class="card warn">
      <div class="val">${{s.rate}}%</div>
      <div class="lbl">${{h}}h 경보율 (${{s.warned}}/${{s.total}})</div>
    </div>`;
  }});
  el.innerHTML = html;
}}

// ── 탭 ──────────────────────────────────────────────────────────────────
function renderTabs() {{
  const el = document.getElementById('horizon-tabs');
  el.innerHTML = HORIZONS.map(h =>
    `<button class="tab-btn ${{h===curHorizon?'active':''}}" onclick="switchHorizon(${{h}})">${{h}}h horizon</button>`
  ).join('');
}}

function switchHorizon(h) {{
  curHorizon = h;
  renderTabs();
  renderHeatmap();
  populateMeterSelect();
  renderMeterDetail();
}}

// ── 히트맵 ───────────────────────────────────────────────────────────────
function renderHeatmap() {{
  const ts = TS_MAP[curHorizon];
  const sub = RAW.filter(r => r.horizon === curHorizon);

  // meter × ts 경보 매트릭스
  const warnMatrix = {{}};
  const physMatrix  = {{}};
  sub.forEach(r => {{
    warnMatrix[r.meter_urn] = warnMatrix[r.meter_urn] || {{}};
    physMatrix[r.meter_urn]  = physMatrix[r.meter_urn]  || {{}};
    warnMatrix[r.meter_urn][r.pred_ts] = r.warning_flag ? 1 : 0;
    physMatrix[r.meter_urn][r.pred_ts]  = r.physical_flag ? 1 : 0;
  }});

  const meters = [...new Set(sub.map(r => r.meter_urn))].sort();
  // z 인코딩: -1=데이터없음, 0=정상, 0.33=물리이상만, 0.67=LOW경보, 1=HIGH경보
  // Plotly colorscale 값은 0~1 정규화 필요: fraction = (z + 1) / 2
  // z=-1→0, z=0→0.5, z=0.33→0.665, z=0.67→0.835, z=1→1
  const z = meters.map(m => ts.map(t => {{
    const r = sub.find(x => x.meter_urn===m && x.pred_ts===t);
    if (!r) return -1;
    let hasHigh = false, hasLow = false;
    for (let k=1; k<=curHorizon; k++) {{
      const wt = r[`warning_type_t_plus_${{k}}`];
      if (wt === 'high') hasHigh = true;
      if (wt === 'low')  hasLow  = true;
    }}
    if (hasHigh) return 1;
    if (hasLow)  return 0.67;
    if (r.physical_flag) return 0.33;
    return 0;
  }}));
  const text = meters.map(m => ts.map(t => {{
    const r = sub.find(x => x.meter_urn===m && x.pred_ts===t);
    if (!r) return '데이터없음';
    const steps = [...Array(curHorizon)].map((_,i)=>{{
      const k = i+1;
      const wt = r[`warning_type_t_plus_${{k}}`];
      const pv = r[`pred_t_plus_${{k}}`];
      return `t+${{k}}: ${{pv?.toFixed(0)}} [${{wt}}]`;
    }});
    return `${{m}}<br>${{steps.join('<br>')}}`;
  }}));

  Plotly.newPlot('heatmap-chart', [{{
    type: 'heatmap', z, x: ts, y: meters, text, hovertemplate: '%{{text}}<extra></extra>',
    colorscale: [[0,'#c8d6e5'],[0.5,'#2ecc71'],[0.665,'#f1c40f'],[0.835,'#e67e22'],[1,'#e74c3c']],
    zmin:-1, zmax:1, showscale:false,
    xgap:2, ygap:2,
  }}], {{
    margin: {{l:90,r:10,t:10,b:80}},
    xaxis: {{tickangle:-30, tickfont:{{size:11}}}},
    yaxis: {{tickfont:{{size:10}}}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
  }}, {{responsive:true, displayModeBar:false}});
}}

// ── 계량기 Select ────────────────────────────────────────────────────────
function populateMeterSelect() {{
  const showWarnOnly = document.getElementById('warn-only-cb').checked;
  const sub = RAW.filter(r => r.horizon === curHorizon);
  const warnMeters = new Set(sub.filter(r=>r.warning_flag).map(r=>r.meter_urn));
  const list = showWarnOnly ? METERS.filter(m=>warnMeters.has(m)) : METERS;
  const sel = document.getElementById('meter-select');
  sel.innerHTML = list.map(m=>
    `<option value="${{m}}" ${{m===curMeter?'selected':''}}>${{m}}${{warnMeters.has(m)?' ⚠':''}})</option>`
  ).join('');
  if (!list.includes(curMeter)) curMeter = list[0] || METERS[0];
}}

// ── 계량기 상세 차트 + 테이블 ───────────────────────────────────────────
function renderMeterDetail() {{
  curMeter = document.getElementById('meter-select').value || curMeter;
  const sub = RAW.filter(r => r.horizon===curHorizon && r.meter_urn===curMeter);
  if (!sub.length) return;

  const ts = sub.map(r=>r.pred_ts);
  const horizon = curHorizon;
  const colors = ['#3498db','#e67e22','#9b59b6'];

  const traces = [];
  for (let k=1; k<=horizon; k++) {{
    const preds = sub.map(r=>r[`pred_t_plus_${{k}}`]);
    const lower = sub.map(r=>r[`threshold_lower_t_plus_${{k}}`]);
    const upper = sub.map(r=>r[`threshold_upper_t_plus_${{k}}`]);
    const warns = sub.map(r=>r[`warning_t_plus_${{k}}`]);
    const col = colors[k-1];

    // threshold band
    traces.push({{
      x:[...ts,...[...ts].reverse()],
      y:[...upper,...[...lower].reverse()],
      fill:'toself', fillcolor:col.replace(')',',0.08)').replace('rgb','rgba'),
      line:{{color:'transparent'}}, showlegend:false, hoverinfo:'skip', type:'scatter',
    }});
    // upper/lower lines
    traces.push({{x:ts, y:upper, mode:'lines', line:{{color:col,dash:'dash',width:1}},
      name:`t+${{k}} upper`, showlegend:false, hovertemplate:'upper: %{{y:.0f}}<extra></extra>'}});
    traces.push({{x:ts, y:lower, mode:'lines', line:{{color:col,dash:'dot',width:1}},
      name:`t+${{k}} lower`, showlegend:false, hovertemplate:'lower: %{{y:.0f}}<extra></extra>'}});
    // prediction line
    traces.push({{x:ts, y:preds, mode:'lines+markers', name:`pred t+${{k}}`,
      line:{{color:col,width:2}}, marker:{{size:7}},
      hovertemplate:`t+${{k}}: %{{y:.1f}}<extra></extra>`}});
    // warning markers
    const wx=ts.filter((_,i)=>warns[i]), wy=preds.filter((_,i)=>warns[i]);
    if (wx.length) traces.push({{x:wx, y:wy, mode:'markers',
      marker:{{color:'#e74c3c',size:13,symbol:'circle-open',line:{{width:3}}}},
      name:'경보', showlegend:k===1, hovertemplate:'⚠ 경보: %{{y:.1f}}<extra></extra>'}});
  }}

  Plotly.newPlot('meter-chart', traces, {{
    title:{{text:`${{curMeter}} — ${{horizon}}h 예측 vs threshold`,font:{{size:13}}}},
    xaxis:{{tickangle:-20, tickfont:{{size:11}}}},
    yaxis:{{title:'P (kW/kWh)', tickfont:{{size:11}}}},
    legend:{{orientation:'h', y:-0.2}},
    margin:{{l:60,r:20,t:40,b:60}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fafbfc',
    hovermode:'x unified',
  }}, {{responsive:true, displayModeBar:false}});

  // 테이블
  let tbl = `<table><thead><tr>
    <th>timestamp</th><th>physical</th>
    ${{[...Array(horizon)].map((_,i)=>`<th>pred t+${{i+1}}</th><th>lower</th><th>upper</th><th>type</th>`).join('')}}
    <th>warning_flag</th>
  </tr></thead><tbody>`;

  sub.forEach(r => {{
    const isWarn = r.warning_flag;
    const isPhys = r.physical_flag;
    const rowCls = isWarn ? 'warn-row' : (isPhys ? 'phys-row' : '');
    const physBadge = isPhys ? '<span class="badge badge-phys">PHYS</span>' : '—';
    const steps = [...Array(horizon)].map((_,i)=>{{
      const k=i+1;
      const wt = r[`warning_type_t_plus_${{k}}`];
      const pv = r[`pred_t_plus_${{k}}`];
      const lo = r[`threshold_lower_t_plus_${{k}}`];
      const hi = r[`threshold_upper_t_plus_${{k}}`];
      const badge = wt==='none' ? '<span class="badge badge-none">✓</span>'
                  : wt==='high' ? '<span class="badge badge-high">↑HIGH</span>'
                  : '<span class="badge badge-low">↓LOW</span>';
      return `<td>${{pv?.toFixed(1)??'—'}}</td><td>${{lo?.toFixed(1)??'—'}}</td><td>${{hi?.toFixed(1)??'—'}}</td><td>${{badge}}</td>`;
    }}).join('');
    const warnBadge = isWarn ? '<span class="badge badge-warn">⚠ 경보</span>' : '—';
    tbl += `<tr class="${{rowCls}}"><td>${{r.pred_ts}}</td><td>${{physBadge}}</td>${{steps}}<td>${{warnBadge}}</td></tr>`;
  }});

  tbl += '</tbody></table>';
  document.getElementById('detail-table-wrap').innerHTML = tbl;
}}

// ── 이벤트 바인딩 ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {{
  renderSummaryCards();
  renderTabs();
  renderHeatmap();
  populateMeterSelect();
  renderMeterDetail();

  document.getElementById('meter-select').addEventListener('change', renderMeterDetail);
  document.getElementById('warn-only-cb').addEventListener('change', () => {{
    populateMeterSelect(); renderMeterDetail();
  }});
}});
</script>
</body>
</html>"""
    return html


def main():
    df = load_all()
    if df.empty:
        print("inference 결과 CSV가 없습니다.")
        return
    print(f"로드 완료: {len(df)}행 ({df['horizon'].unique()} horizon, {df['pred_ts'].nunique()}개 timestamp)")
    html = build_html(df)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"리포트 저장: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
