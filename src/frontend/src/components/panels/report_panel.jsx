import { useState, useEffect, useMemo } from 'react'
import { FileText } from 'lucide-react'
import {
  LineChart, Line, BarChart, Bar, ScatterChart, Scatter, ComposedChart,
  XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
  ReferenceLine,
} from 'recharts'
import {
  getReport, getBalanceReport, getEnergyIntensity, monthlyDownloadUrl,
  getBilling, getDailyReport, getLatestDataDate, dailyDownloadUrl,
  getOpsReportLatest, generateOpsReport,
} from '../../api/client'

const tt = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: 'var(--text)' },
}

const SEASON_ORDER = { '봄': 1, '여름': 2, '가을': 3, '겨울': 4 }
const SEASON_COLOR = { '봄': '#3fb950', '여름': '#f85149', '가을': '#d29922', '겨울': '#2563eb' }
const SEASON_EMOJI = { '봄': '🌸', '여름': '☀️', '가을': '🍂', '겨울': '❄️' }

function getSeason(period) {
  const m = parseInt(period.slice(5, 7))
  if (m >= 3 && m <= 5) return '봄'
  if (m >= 6 && m <= 8) return '여름'
  if (m >= 9 && m <= 11) return '가을'
  return '겨울'
}

function aggregate(items, keyFn, labelFn, sortFn) {
  const map = {}
  for (const item of items) {
    const key = keyFn(item)
    if (!map[key]) map[key] = { period: labelFn(item, key), _n: 0, total_consumption_kwh: 0, self_sufficiency_pct: 0, avg_cop: 0, grid_dependency_pct: 0, anomaly_count: 0, pv_kwh: 0, chp_kwh: 0 }
    const g = map[key]
    g.total_consumption_kwh += item.total_consumption_kwh ?? 0
    g.self_sufficiency_pct  += item.self_sufficiency_pct  ?? 0
    g.avg_cop               += item.avg_cop               ?? 0
    g.grid_dependency_pct   += item.grid_dependency_pct   ?? 0
    g.anomaly_count         += item.anomaly_count         ?? 0
    g.pv_kwh                += item.pv_kwh                ?? 0
    g.chp_kwh               += item.chp_kwh               ?? 0
    g._n++
  }
  return Object.entries(map)
    .sort(sortFn ? ([a], [b]) => sortFn(a, b) : undefined)
    .map(([, g]) => ({
      ...g,
      self_sufficiency_pct: g.self_sufficiency_pct / g._n,
      avg_cop:              g.avg_cop              / g._n,
      grid_dependency_pct:  g.grid_dependency_pct  / g._n,
    }))
}

function groupByYear(items) {
  return aggregate(items, item => item.period.slice(0, 4), (_, key) => key + '년', (a, b) => a.localeCompare(b))
}

function groupBySeason(items) {
  return aggregate(
    items,
    item => {
      const y = parseInt(item.period.slice(0, 4))
      const m = parseInt(item.period.slice(5, 7))
      const effYear = m === 12 ? y + 1 : y
      return `${effYear}-${getSeason(item.period)}`
    },
    (item, key) => {
      const [yr, sn] = [key.slice(0, 4), key.slice(5)]
      return `${yr} ${SEASON_EMOJI[sn]}${sn}`
    },
    (a, b) => {
      const [ay, as_] = [a.slice(0, 4), a.slice(5)]
      const [by, bs_] = [b.slice(0, 4), b.slice(5)]
      if (ay !== by) return ay.localeCompare(by)
      return (SEASON_ORDER[as_] ?? 5) - (SEASON_ORDER[bs_] ?? 5)
    },
  )
}

function NarrativeBox({ color, title, text, placeholder }) {
  if (!text) {
    return (
      <div style={{ border: '1px dashed ' + color + '44', borderRadius: 8, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color }}>{title}</span>
        <span style={{ fontSize: 11, color: 'var(--text4)', fontStyle: 'italic' }}>{placeholder}</span>
      </div>
    )
  }
  return (
    <div style={{ background: `linear-gradient(135deg, ${color}08, var(--surface))`, border: `1px solid ${color}44`, borderRadius: 8, padding: '12px 16px' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color, marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>{text}</div>
    </div>
  )
}

function YoyBadge({ pct, unit = '%' }) {
  if (pct == null) return null
  const up   = pct > 0
  const zero = pct === 0
  const color = zero ? 'var(--text3)' : up ? '#f85149' : '#3fb950'
  const arrow = zero ? '─' : up ? '▲' : '▼'
  return (
    <span style={{ fontSize: 10, color, background: color + '18', borderRadius: 4, padding: '1px 6px', marginLeft: 6, fontWeight: 600 }}>
      {arrow} {Math.abs(pct).toFixed(1)}{unit} YoY
    </span>
  )
}

function KpiCard({ label, value, unit, color = '#2563eb', yoy, yoyUnit, sub }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 22, fontWeight: 700, color }}>{value ?? '–'}</span>
        <YoyBadge pct={yoy} unit={yoyUnit} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{unit}</div>
      <div style={{ fontSize: 12, color: 'var(--text4)', marginTop: 4 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 3 }}>{sub}</div>}
    </div>
  )
}

function MiniProgressBar({ pct, color }) {
  return (
    <div style={{ background: 'var(--line)', borderRadius: 4, height: 5, overflow: 'hidden', marginTop: 3 }}>
      <div style={{ width: `${Math.min(100, pct ?? 0)}%`, height: '100%', background: color, borderRadius: 4, transition: 'width .4s' }}/>
    </div>
  )
}

function downloadCSV(items, view) {
  const headers = ['기간', '소비량(kWh)', '자급률(%)', 'COP', '그리드의존도(%)', '이상건수']
  const rows = [...items].reverse().map(r => [
    r.period,
    r.total_consumption_kwh?.toFixed(0) ?? '',
    r.self_sufficiency_pct?.toFixed(1)  ?? '',
    r.avg_cop?.toFixed(2)               ?? '',
    r.grid_dependency_pct?.toFixed(1)   ?? '',
    r.anomaly_count ?? '',
  ])
  const csv  = [headers, ...rows].map(r => r.join(',')).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = `energy_kpi_${view}.csv`; a.click()
  URL.revokeObjectURL(url)
}

// ── 일일 보고서 뷰 ──────────────────────────────────────────────────
function DailyView() {
  const [date,         setDate]         = useState('')
  const [report,       setReport]       = useState(null)
  const [loading,      setLoading]      = useState(true)
  const [regenerating, setRegenerating] = useState(false)

  useEffect(() => {
    getLatestDataDate()
      .then(r => { if (r.data?.date) setDate(r.data.date) })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!date) return
    setLoading(true)
    setReport(null)
    getDailyReport(date)
      .then(r => setReport(r.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [date])

  const shiftDate = (days) => {
    if (!date) return
    const d = new Date(date)
    d.setDate(d.getDate() + days)
    setDate(d.toISOString().slice(0, 10))
  }

  const handleRegen = async () => {
    if (!date || regenerating) return
    setRegenerating(true)
    try {
      const r = await getDailyReport(date, true)
      if (r?.data) setReport(r.data)
    } finally { setRegenerating(false) }
  }

  const hourly = (report?.hourly_profile ?? []).map(h => ({
    ...h, hourLabel: String(h.hour).padStart(2, '0'),
  }))

  return (
    <div style={s.body}>
      {/* 날짜 내비게이션 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button onClick={() => shiftDate(-1)} style={s.dateNavBtn} title="전일">‹</button>
        <input type="date" value={date} onChange={e => setDate(e.target.value)}
          style={{ padding: '5px 10px', border: '1px solid var(--border)', borderRadius: 6,
                   background: 'var(--surface)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }}/>
        <button onClick={() => shiftDate(1)} style={s.dateNavBtn} title="다음날">›</button>
        {report?.date && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: 'var(--text4)' }}>다운로드:</span>
            {[['pdf','📄 PDF'],['docx','📝 DOCX'],['hwpx','📄 HWPX']].map(([fmt, label]) => (
              <button key={fmt} style={s.docBtn}
                onClick={() => {
                  const a = document.createElement('a')
                  a.href = dailyDownloadUrl(report.date, fmt)
                  a.target = '_blank'; a.rel = 'noopener'
                  document.body.appendChild(a); a.click(); a.remove()
                }}>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {loading && (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
          일일 보고서 불러오는 중…
        </div>
      )}

      {!loading && !report && (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
          {date ? `${date} 데이터가 없습니다. 시뮬레이터가 해당 날짜를 지났는지 확인하세요.` : '날짜를 선택하세요.'}
        </div>
      )}

      {!loading && report && (
        <>
          {/* KPI 5개 */}
          <div style={{ ...s.kpiRow, gridTemplateColumns: 'repeat(5,1fr)' }}>
            <KpiCard
              label="총 소비"
              value={(report.total_consumption_kwh ?? 0).toLocaleString('ko-KR', { maximumFractionDigits: 0 })}
              unit="kWh" color="#2563eb"
            />
            <KpiCard
              label="자급률"
              value={report.self_sufficiency_pct?.toFixed(1)}
              unit="%" color="#3fb950"
              sub="태양광 + CHP"
            />
            <KpiCard
              label="평균 COP"
              value={report.avg_cop?.toFixed(2)}
              unit="성능계수" color="#a371f7"
              sub="기준 2.06"
            />
            <KpiCard
              label="피크"
              value={report.peak_kw?.toFixed(0)}
              unit="kW" color="#f85149"
              sub={report.peak_hour != null ? `${report.peak_hour}시 최고` : null}
            />
            <KpiCard
              label="계통 전기요금"
              value={report.cost_eur != null ? `€${Math.round(report.cost_eur).toLocaleString()}` : '–'}
              unit="당일 계통 기준" color="#d29922"
            />
          </div>

          {/* AI 브리핑 + 시간대 프로파일 */}
          <div style={{ background: 'linear-gradient(135deg, #2563eb10, var(--surface))', border: '1px solid #2563eb33', borderRadius: 10, padding: '16px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: '#2563eb' }}>🤖 AI 운영 브리핑</span>
              {(report.anomaly_count ?? 0) > 0 && (
                <span style={{ fontSize: 10, color: '#f85149', background: '#f8514922', padding: '2px 8px', borderRadius: 4, fontWeight: 600 }}>
                  이상 {report.anomaly_count}건
                </span>
              )}
              <div style={{ marginLeft: 'auto' }}>
                {!report.ai_summary ? (
                  <button style={{ ...s.docBtn, color: '#a371f7', borderColor: '#a371f766' }}
                    onClick={handleRegen} disabled={regenerating}>
                    {regenerating ? '생성 중…' : '🪄 AI 요약 생성'}
                  </button>
                ) : (
                  <button style={{ ...s.docBtn, color: 'var(--text4)', borderColor: 'var(--border)' }}
                    onClick={handleRegen} disabled={regenerating} title="AI 요약 재생성 (LLM 1회 호출)">
                    {regenerating ? '재생성 중…' : '↺ 재생성'}
                  </button>
                )}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
              {/* 좌: 요약 + 조치 */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {report.ai_summary
                  ? <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>{report.ai_summary}</div>
                  : <div style={{ fontSize: 12, color: 'var(--text4)', fontStyle: 'italic', lineHeight: 1.7 }}>
                      KPI와 시간대 프로파일은 자동 수집됐습니다.<br/>
                      AI 요약이 필요하면 "🪄 AI 요약 생성"을 클릭하세요.
                    </div>
                }
                {report.today_actions && (
                  <div style={{ background: 'var(--bg)', border: '1px solid #3fb95044', borderRadius: 8, padding: '10px 14px' }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: '#3fb950', marginBottom: 6 }}>✅ 오늘 할 일</div>
                    <div style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.9, whiteSpace: 'pre-wrap' }}>{report.today_actions}</div>
                  </div>
                )}
              </div>

              {/* 우: 시간대 프로파일 */}
              <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>⏱ 시간대별 전력 프로파일 · COP</div>
                {hourly.length > 0 ? (
                  <ResponsiveContainer width="100%" height={170}>
                    <ComposedChart data={hourly} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false}/>
                      <XAxis dataKey="hourLabel" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} interval={3}/>
                      <YAxis yAxisId="kw"  tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false}/>
                      <YAxis yAxisId="cop" orientation="right" tick={{ fontSize: 9, fill: '#a371f7' }} tickLine={false} axisLine={false} domain={[0, 'auto']}/>
                      <Tooltip contentStyle={tt.contentStyle} labelStyle={tt.labelStyle}/>
                      {report.peak_hour != null && (
                        <ReferenceLine yAxisId="kw" x={String(report.peak_hour).padStart(2, '0')} stroke="#f85149" strokeDasharray="4 2"/>
                      )}
                      <Bar yAxisId="kw" dataKey="grid_kw"  stackId="a" name="계통"   fill="#d29922"/>
                      <Bar yAxisId="kw" dataKey="pv_kw"    stackId="a" name="태양광" fill="#3fb950"/>
                      <Bar yAxisId="kw" dataKey="chp_kw"   stackId="a" name="CHP"   fill="#2563eb" radius={[2, 2, 0, 0]}/>
                      <Line yAxisId="cop" type="monotone" dataKey="cop" name="COP" stroke="#a371f7" strokeWidth={2} dot={false} connectNulls/>
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ textAlign: 'center', color: 'var(--text4)', fontSize: 11, padding: '40px 0' }}>프로파일 없음</div>
                )}
              </div>
            </div>
          </div>

          {/* 이상탐지 이벤트 */}
          {(report.anomaly_events ?? []).length > 0 && (
            <div style={s.chartBox}>
              <div style={s.chartTitle}>⚠ 이상탐지 이벤트</div>
              {report.anomaly_events.map((ev, i) => {
                const col = ev.severity === 'HIGH' ? '#f85149' : '#d29922'
                return (
                  <div key={i} style={{ display: 'flex', gap: 10, padding: '7px 0', borderBottom: '1px solid var(--surface)', alignItems: 'flex-start' }}>
                    <span style={{ fontSize: 10, color: col, background: col + '22', padding: '1px 6px', borderRadius: 3, fontWeight: 700, flexShrink: 0 }}>{ev.severity}</span>
                    <span style={{ fontSize: 11, color: 'var(--text3)', flexShrink: 0, minWidth: 40 }}>{String(ev.timestamp ?? '').slice(11, 16)}</span>
                    <span style={{ fontSize: 11, color: 'var(--text)' }}>{ev.anomaly_type}: {ev.description}</span>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}


const OPS_CADENCES = [
  ['daily', '일간'],
  ['weekly', '주간'],
  ['monthly', '월간'],
]
const OPS_CADENCE_LABEL = { daily: '일간', weekly: '주간', monthly: '월간' }

function fmtOpsTime(value) {
  if (!value) return '–'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function fmtOpsDate(value) {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value ?? '–'
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일`
}

function fmtOpsNumber(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '–'
  return n.toLocaleString('ko-KR', { maximumFractionDigits: 1 })
}

function reportTitle(cadence, period) {
  if (!period) return `${OPS_CADENCE_LABEL[cadence] ?? cadence} 보고서`
  if (cadence === 'daily') return `${fmtOpsDate(period)} 일간 보고서`
  if (cadence === 'weekly' && String(period).includes('_')) {
    const [start, end] = String(period).split('_')
    const s = new Date(start), e = new Date(end)
    if (!Number.isNaN(s.getTime()) && !Number.isNaN(e.getTime())) {
      const sameYear = s.getFullYear() === e.getFullYear()
      const startText = `${s.getFullYear()}년 ${s.getMonth() + 1}월 ${s.getDate()}일`
      const endText = sameYear ? `${e.getMonth() + 1}월 ${e.getDate()}일` : `${e.getFullYear()}년 ${e.getMonth() + 1}월 ${e.getDate()}일`
      return `${startText}~${endText} 주간 보고서`
    }
  }
  if (cadence === 'monthly') {
    const [y, m] = String(period).split('-')
    if (y && m) return `${Number(y)}년 ${Number(m)}월 월간 보고서`
  }
  return `${period} ${OPS_CADENCE_LABEL[cadence] ?? cadence} 보고서`
}

function anomalySplit(rows) {
  const all = Array.isArray(rows) ? rows : []
  const warnings = all.filter(row => row?.warning_flag === true)
  const references = all.filter(row => row?.warning_flag !== true)
  return { all, warnings, references }
}

function topByMeter(points) {
  const best = new Map()
  for (const point of points) {
    const prev = best.get(point.meter)
    if (!prev || point.value > prev.value) best.set(point.meter, point)
  }
  return [...best.values()].sort((a, b) => b.value - a.value)
}

function buildOpsMeterFocus(item) {
  const chart = item?.chart_json ?? {}
  const forecast = []
  for (const series of chart.forecast_series ?? []) {
    for (const point of series.points ?? []) {
      const meter = point.source_meter_urn ?? point.logical_meter
      const value = Number(point.predicted_p_max)
      if (meter && Number.isFinite(value)) forecast.push({ meter, value, ts: point.target_ts })
    }
  }
  const observed = []
  const lowCoverage = []
  for (const series of chart.observed_feature_series ?? []) {
    const meter = series.meter_urn ?? '계량기 미확인'
    let peak = null
    let minCoverage = null
    for (const point of series.points ?? []) {
      const value = Number(point.observed_or_feature_peak ?? point.peak_value ?? point.max_value)
      if (Number.isFinite(value) && (!peak || value > peak.value)) peak = { meter, value, ts: point.window_ts }
      const coverage = Number(point.coverage_ratio)
      if (Number.isFinite(coverage) && (minCoverage == null || coverage < minCoverage)) minCoverage = coverage
    }
    if (peak) observed.push({ ...peak, coverage: minCoverage })
    if (minCoverage != null && minCoverage < 1) lowCoverage.push({ meter, coverage: minCoverage })
  }
  const { warnings, references } = anomalySplit(item?.anomaly_rows)
  const warningByMeter = new Map()
  for (const row of warnings) {
    const meter = row.meter_urn ?? '계량기 미확인'
    warningByMeter.set(meter, (warningByMeter.get(meter) ?? 0) + 1)
  }
  return {
    forecastTop: topByMeter(forecast).slice(0, 5),
    observedTop: observed.sort((a, b) => b.value - a.value).slice(0, 5),
    lowCoverage: lowCoverage.sort((a, b) => a.coverage - b.coverage).slice(0, 5),
    warningTop: [...warningByMeter.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([meter, count]) => ({ meter, count })),
    warningCount: warnings.length,
    referenceCount: references.length,
  }
}

function buildObservedChart(chart) {
  const ranked = []
  for (const series of chart?.observed_feature_series ?? []) {
    const meter = series.meter_urn ?? '계량기 미확인'
    let peak = 0
    for (const point of series.points ?? []) {
      const value = Number(point.observed_or_feature_peak ?? point.peak_value ?? point.max_value)
      if (Number.isFinite(value) && value > peak) peak = value
    }
    if (peak > 0) ranked.push({ meter, points: series.points ?? [], peak })
  }
  const selected = ranked.sort((a, b) => b.peak - a.peak).slice(0, 3)
  const byTs = new Map()
  selected.forEach((series, idx) => {
    for (const point of series.points) {
      const ts = point.window_ts
      const value = Number(point.observed_or_feature_peak ?? point.peak_value ?? point.max_value)
      if (!ts || !Number.isFinite(value)) continue
      const row = byTs.get(ts) ?? { ts }
      row[`m${idx}`] = value
      byTs.set(ts, row)
    }
  })
  return {
    rows: [...byTs.values()].sort((a, b) => String(a.ts).localeCompare(String(b.ts))),
    lines: selected.map((series, idx) => ({ key: `m${idx}`, name: series.meter, color: ['#2563eb', '#3fb950', '#d29922'][idx] ?? '#8b949e' })),
  }
}

function OpsForecastChart({ chart }) {
  const rows = Array.isArray(chart?.data) ? chart.data.filter(row => row.forecast_pmax != null) : []
  if (!rows.length) return <div style={{ ...s.empty, padding: '18px 0' }}>단기 참고 그래프에 표시할 데이터가 없습니다.</div>
  return (
    <div style={s.opsChartWrap}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis dataKey="ts" tick={{ fontSize: 10, fill: 'var(--text4)' }} minTickGap={28} tickFormatter={fmtOpsTime} />
          <YAxis tick={{ fontSize: 10, fill: 'var(--text4)' }} tickFormatter={fmtOpsNumber} width={60} />
          <Tooltip {...tt} labelFormatter={fmtOpsTime} formatter={(v) => [fmtOpsNumber(v), 'P-Max 예측']} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line type="monotone" dataKey="forecast_pmax" name="P-Max 예측" stroke="#f85149" strokeWidth={1.7} dot={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function OpsObservedChart({ chart }) {
  const { rows, lines } = buildObservedChart(chart)
  if (!rows.length || !lines.length) return <div style={{ ...s.empty, padding: '18px 0' }}>실측 그래프에 표시할 계량기 데이터가 없습니다.</div>
  return (
    <div style={s.opsChartWrap}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis dataKey="ts" tick={{ fontSize: 10, fill: 'var(--text4)' }} minTickGap={28} tickFormatter={fmtOpsTime} />
          <YAxis tick={{ fontSize: 10, fill: 'var(--text4)' }} tickFormatter={fmtOpsNumber} width={68} />
          <Tooltip {...tt} labelFormatter={fmtOpsTime} formatter={(v, name) => [fmtOpsNumber(v), name]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {lines.map(line => (
            <Line key={line.key} type="monotone" dataKey={line.key} name={line.name} stroke={line.color} strokeWidth={1.4} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function OpsMeterFocus({ item }) {
  const focus = buildOpsMeterFocus(item)
  return (
    <section style={s.opsSection}>
      <div style={s.opsSectionTitle}>비용·피크 관리</div>
      <div style={s.opsSummaryGrid}>
        <OpsMetric label="실제 이상 경고" value={`${focus.warningCount}건`} tone={focus.warningCount ? 'danger' : 'success'} />
        <OpsMetric label="정상/참고 행" value={`${focus.referenceCount}건`} />
        <OpsMetric label="실측 피크 계량기" value={focus.observedTop[0]?.meter ?? '–'} sub={focus.observedTop[0] ? fmtOpsNumber(focus.observedTop[0].value) : ''} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10, marginTop: 10 }}>
        <OpsMeterRank title="실측/피처 피크 상위" rows={focus.observedTop} valueLabel="피크" showCoverage />
        <OpsMeterRank title="실제 경고 계량기" rows={focus.warningTop.map(row => ({ meter: row.meter, value: row.count }))} valueLabel="경고" />
      </div>
      {focus.lowCoverage.length > 0 && <OpsCoverageRank rows={focus.lowCoverage} />}
    </section>
  )
}

function OpsMetric({ label, value, sub, tone }) {
  const color = tone === 'danger' ? '#f85149' : tone === 'success' ? '#3fb950' : 'var(--text)'
  return (
    <div style={s.opsMetric}>
      <div style={{ fontSize: 11, color: 'var(--text4)' }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 800, color, marginTop: 4 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function OpsMeterRank({ title, rows, valueLabel, showCoverage = false }) {
  if (!rows.length) return <div style={s.opsFocusBox}><div style={s.opsFocusTitle}>{title}</div><div style={{ fontSize: 11, color: 'var(--text4)' }}>표시할 데이터가 없습니다.</div></div>
  return (
    <div style={s.opsFocusBox}>
      <div style={s.opsFocusTitle}>{title}</div>
      {rows.map(row => (
        <div key={`${title}-${row.meter}`} style={s.opsFocusRow}>
          <span style={s.opsMeterName}>{row.meter}</span>
          <span>{valueLabel} {fmtOpsNumber(row.value)}</span>
          <span style={{ color: 'var(--text4)' }}>{fmtOpsTime(row.ts)}</span>
          {showCoverage && row.coverage != null && <span style={{ color: 'var(--text4)' }}>표본 {fmtOpsNumber(row.coverage)}</span>}
        </div>
      ))}
    </div>
  )
}

function OpsCoverageRank({ rows }) {
  return (
    <div style={{ ...s.opsFocusBox, marginTop: 10 }}>
      <div style={s.opsFocusTitle}>표본 확인 필요</div>
      {rows.map(row => (
        <div key={`coverage-${row.meter}`} style={s.opsFocusRow}>
          <span style={s.opsMeterName}>{row.meter}</span>
          <span>{fmtOpsNumber(row.coverage)}</span>
        </div>
      ))}
    </div>
  )
}

function OpsAnomalySection({ item }) {
  const { warnings, references } = anomalySplit(item?.anomaly_rows)
  return (
    <section style={s.opsSection}>
      <div style={s.opsSectionTitle}>점검 후보</div>
      {warnings.length === 0 ? (
        <div style={s.opsOkBox}>현재 보고서 기간에는 실제 이상 경고로 분류된 점검 후보가 없습니다. 아래 참고 행은 정상/참고로 분류된 행입니다.</div>
      ) : (
        <OpsAnomalyTable rows={warnings} title="실제 이상 경고" />
      )}
      {references.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer', color: 'var(--text3)', fontSize: 12 }}>정상/참고 행 {references.length}건 보기</summary>
          <OpsAnomalyTable rows={references} title="정상/참고 행" />
        </details>
      )}
    </section>
  )
}

function OpsAnomalyTable({ rows, title }) {
  const items = Array.isArray(rows) ? rows.slice(0, 10) : []
  if (!items.length) return <div style={{ ...s.empty, padding: '12px 0' }}>{title ?? '이상치'} 표에 표시할 행이 없습니다.</div>
  return (
    <div style={{ marginTop: 10, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ color: 'var(--text3)', borderBottom: '1px solid var(--line)' }}>
            <th style={s.opsTh}>시각</th>
            <th style={s.opsTh}>계량기</th>
            <th style={s.opsTh}>판정</th>
            <th style={s.opsTh}>사유</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, idx) => (
            <tr key={`${row.target_ts ?? row.forecast_origin_ts ?? idx}-${row.meter_urn ?? idx}`} style={{ borderBottom: '1px solid var(--line)' }}>
              <td style={s.opsTd}>{fmtOpsTime(row.target_ts ?? row.forecast_origin_ts)}</td>
              <td style={s.opsTd}>{row.meter_urn ?? '–'}</td>
              <td style={{ ...s.opsTd, color: row.warning_flag ? '#f85149' : '#3fb950', fontWeight: 700 }}>{row.warning_flag ? '실제 경고' : '정상/참고'}</td>
              <td style={s.opsTd}>{row.warning_type ?? row.warning_reason_code ?? '–'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Array.isArray(rows) && rows.length > items.length && (
        <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 6 }}>상위 {items.length}건 표시 / 전체 {rows.length}건</div>
      )}
    </div>
  )
}

function OpsReportDetail({ cadence, item }) {
  if (!item) return <div style={s.empty}>선택한 보고서 데이터가 없습니다.</div>
  return (
    <div style={s.opsDetail}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text)' }}>{reportTitle(cadence, item.period)}</div>
          <div style={{ fontSize: 12, color: 'var(--text4)', marginTop: 4 }}>작성 {fmtOpsTime(item.generated_at)}</div>
        </div>
        <span style={{ fontSize: 11, color: item.guard_result?.ok === false ? '#f85149' : '#3fb950', whiteSpace: 'nowrap' }}>{item.guard_result?.ok === false ? '검토 필요' : '확인 완료'}</span>
      </div>
      <section style={s.opsSection}>
        <div style={s.opsSectionTitle}>핵심 요약</div>
        <div style={s.opsSummaryText}>{item.summary ?? item.executive_summary ?? '요약이 없습니다.'}</div>
      </section>
      <OpsMeterFocus item={item} />
      {(item.operator_actions ?? []).length > 0 && (
        <section style={s.opsSection}>
          <div style={s.opsSectionTitle}>운영자 조치</div>
          <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text2)', fontSize: 12, lineHeight: 1.8 }}>
            {item.operator_actions.map((action, idx) => <li key={idx}>{action}</li>)}
          </ul>
        </section>
      )}
      <details style={s.opsSection}>
        <summary style={{ ...s.opsSectionTitle, cursor: 'pointer' }}>참고: 단기 예측 상태</summary>
        <OpsForecastChart chart={item.chart_json} />
      </details>
      <section style={s.opsSection}>
        <div style={s.opsSectionTitle}>사용 패턴 및 주요 계량기</div>
        <OpsObservedChart chart={item.chart_json} />
      </section>
      <OpsAnomalySection item={item} />
      {item.markdown && (
        <details style={s.opsSection}>
          <summary style={{ ...s.opsSectionTitle, cursor: 'pointer' }}>원문 Markdown</summary>
          <pre style={{ whiteSpace: 'pre-wrap', margin: '10px 0 0', color: 'var(--text2)', fontSize: 12, lineHeight: 1.7 }}>{item.markdown}</pre>
        </details>
      )}
      {item.limitations?.length > 0 && (
        <section style={s.opsSection}>
          <div style={s.opsSectionTitle}>한계</div>
          <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text3)', fontSize: 12, lineHeight: 1.7 }}>
            {item.limitations.map((x, idx) => <li key={idx}>{x}</li>)}
          </ul>
        </section>
      )}
    </div>
  )
}

function OpsReportListItem({ cadence, item, active, onClick }) {
  const focus = buildOpsMeterFocus(item)
  return (
    <button onClick={onClick} style={{ ...s.opsListItem, ...(active ? s.opsListItemActive : {}) }}>
      <div style={{ fontSize: 13, fontWeight: 800, color: 'var(--text)', textAlign: 'left' }}>{reportTitle(cadence, item?.period)}</div>
      <div style={{ fontSize: 11, color: 'var(--text4)', marginTop: 5, textAlign: 'left' }}>
        경고 {focus.warningCount}건 · 예측 {item?.chart_json?.forecast_series?.length ?? 0}계열 · 실측 {item?.chart_json?.observed_feature_series?.length ?? 0}계량기
      </div>
    </button>
  )
}

function OpsReportsView() {
  const [reports, setReports] = useState({})
  const [selected, setSelected] = useState('daily')
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [lastRun, setLastRun] = useState(null)

  const loadLatest = async () => {
    setLoading(true)
    setError('')
    try {
      const entries = await Promise.all(
        OPS_CADENCES.map(async ([cadence]) => {
          const r = await getOpsReportLatest(cadence)
          return [cadence, r.data?.item ?? null]
        })
      )
      setReports(Object.fromEntries(entries))
    } catch (e) {
      setError(e.message ?? '운영 보고서 조회 실패')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadLatest() }, [])

  const runSequential = async () => {
    if (running) return
    setRunning(true)
    setError('')
    const result = []
    try {
      for (const [cadence] of OPS_CADENCES) {
        const r = await generateOpsReport(cadence)
        result.push({ cadence, ok: r.data?.ok === true, period: r.data?.report?.period ?? r.data?.record?.period_key })
      }
      setLastRun(result)
      await loadLatest()
    } catch (e) {
      setError(e.message ?? '운영 보고서 순차 실행 실패')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div style={s.body}>
      <div style={s.chartBox}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <div style={s.chartTitle}>운영 보고서</div>
          <button style={s.docBtn} onClick={loadLatest} disabled={loading || running}>새로고침</button>
          <button style={s.aiAllBtn} onClick={runSequential} disabled={loading || running}>
            {running ? '순차 실행 중…' : '일간·주간·월간 순차 실행'}
          </button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text4)', marginBottom: 12 }}>
          보고서 목록을 선택하면 상세 내용이 열립니다. 실제 이상 경고와 정상/참고 행을 분리해 표시합니다.
        </div>
        {error && <div style={{ ...s.empty, color: '#f85149', padding: '12px 0' }}>{error}</div>}
        {lastRun && (
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
            마지막 순차 실행: {lastRun.map(r => `${r.cadence} ${r.ok ? 'OK' : 'FAIL'} ${r.period ?? ''}`).join(' / ')}
          </div>
        )}
        {loading ? (
          <div style={s.empty}>운영 보고서 불러오는 중…</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '260px minmax(0, 1fr)', gap: 14, alignItems: 'start' }}>
            <div style={{ display: 'grid', gap: 8 }}>
              {OPS_CADENCES.map(([cadence]) => (
                <OpsReportListItem key={cadence} cadence={cadence} item={reports[cadence]} active={selected === cadence} onClick={() => setSelected(cadence)} />
              ))}
            </div>
            <OpsReportDetail cadence={selected} item={reports[selected]} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── 메인 컴포넌트 ────────────────────────────────────────────────────
export default function report_panel() {
  const [activeTab,      setActiveTab]      = useState('energy')
  const [energySubTab,   setEnergySubTab]   = useState('monthly')
  const [raw,            setRaw]            = useState([])
  const [coolingVsTemp,  setCoolingVsTemp]  = useState([])
  const [trendNarrative, setTrendNarrative] = useState('')
  const [balance,        setBalance]        = useState(null)
  const [eiData,         setEiData]         = useState(null)
  const [billingCurrent, setBillingCurrent] = useState(null)
  const [loadingAllAi,   setLoadingAllAi]   = useState(false)
  const [months,         setMonths]         = useState(72)
  const [view,           setView]           = useState('monthly')
  const [loading,        setLoading]        = useState(true)
  const [error,          setError]          = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      getReport(months),
      getBalanceReport(Math.max(months, 24)),
      getEnergyIntensity(Math.max(months, 24)),
      getBilling(),
    ]).then(([r, b, ei, bill]) => {
      if (r.status === 'fulfilled') {
        if (r.value.data.error) throw new Error(r.value.data.error)
        setRaw(r.value.data.items ?? [])
        setCoolingVsTemp(r.value.data.cooling_vs_temp ?? [])
        setTrendNarrative(r.value.data.trend_narrative ?? '')
      }
      if (b.status    === 'fulfilled') setBalance(b.value.data)
      if (ei.status   === 'fulfilled') setEiData(ei.value.data)
      if (bill.status === 'fulfilled' && !bill.value.data?.error) setBillingCurrent(bill.value.data)
    })
      .catch(e => setError(e.message ?? '데이터 로드 실패'))
      .finally(() => setLoading(false))
  }, [months])

  const generateAllAi = async () => {
    setLoadingAllAi(true)
    try {
      const [r, b, ei] = await Promise.allSettled([
        getReport(months, false),
        getBalanceReport(Math.max(months, 24), false),
        getEnergyIntensity(Math.max(months, 24), false),
      ])
      if (r.status  === 'fulfilled') setTrendNarrative(r.value.data.trend_narrative ?? '')
      if (b.status  === 'fulfilled') setBalance(b.value.data)
      if (ei.status === 'fulfilled') setEiData(ei.value.data)
    } finally { setLoadingAllAi(false) }
  }

  const items = useMemo(() => {
    if (view === 'yearly')   return groupByYear(raw)
    if (view === 'seasonal') return groupBySeason(raw)
    return raw
  }, [raw, view])

  const latest = items[items.length - 1]

  const momData = useMemo(() => {
    if (view !== 'monthly' || raw.length < 2) return []
    return raw.slice(1).map((curr, i) => {
      const prev = raw[i]
      const consumptionDelta = prev.total_consumption_kwh
        ? (curr.total_consumption_kwh - prev.total_consumption_kwh) / prev.total_consumption_kwh * 100
        : null
      const selfDelta = prev.self_sufficiency_pct != null && curr.self_sufficiency_pct != null
        ? curr.self_sufficiency_pct - prev.self_sufficiency_pct
        : null
      return { period: curr.period.slice(2), consumption_delta: consumptionDelta, self_delta: selfDelta }
    }).slice(-12)
  }, [raw, view])

  const viewLabel = { monthly: '월별', seasonal: '계절별', yearly: '연도별' }
  const aiReady   = !!(trendNarrative || balance?.narrative || eiData?.narrative)
  const statusColor = { '정상': '#3fb950', '주의': '#d29922', '초과 위험': '#f85149' }

  return (
    <div style={s.wrap}>
      {/* ── 헤더 ── */}
      <div style={s.header}>
        <span style={s.title}><FileText size={17} color="#0d9488"/> 에너지 분석</span>

        {/* 탭 */}
        <div style={s.tabGroup}>
          {[['energy', '에너지 현황'], ['ops', '운영 보고서']].map(([v, l]) => (
            <button key={v} onClick={() => setActiveTab(v)}
              style={{ ...s.tab, ...(activeTab === v ? s.tabActive : {}) }}>{l}</button>
          ))}
        </div>

        {activeTab === 'energy' && energySubTab === 'monthly' && (
          <>
            <div style={s.segGroup}>
              {[['monthly','월별'], ['seasonal','계절별'], ['yearly','연도별']].map(([v, l]) => (
                <button key={v} style={{ ...s.seg, ...(view === v ? s.segActive : {}) }}
                  onClick={() => setView(v)}>{l}</button>
              ))}
            </div>
            <div style={s.segGroup}>
              {[12, 24, 72].map(m => (
                <button key={m} style={{ ...s.seg, ...(months === m ? s.segActive : {}) }}
                  onClick={() => setMonths(m)}>{m}개월</button>
              ))}
            </div>

            <button
              style={{ ...s.aiAllBtn, ...(loadingAllAi ? { opacity: 0.6, cursor: 'not-allowed' } : {}) }}
              onClick={generateAllAi} disabled={loadingAllAi || loading}
              title="트렌드·EI·데이터품질 AI 분석을 한 번에 생성 (LLM 3회 호출)">
              {loadingAllAi ? '🤖 분석 중…' : aiReady ? '🤖 AI 재분석' : '🪄 AI 전체 분석'}
            </button>

            {items.length > 0 && (
              <button style={s.csvBtn} onClick={() => downloadCSV(items, view)}>CSV</button>
            )}
            {raw.length > 0 && (
              <>
                <button style={s.docBtn}
                  onClick={() => window.open(monthlyDownloadUrl(months, 'pdf'), '_blank')}>📄 PDF</button>
                <button style={s.docBtn}
                  onClick={() => window.open(monthlyDownloadUrl(months, 'docx'), '_blank')}>📝 DOCX</button>
              </>
            )}
          </>
        )}
      </div>

      {/* ── 에너지 현황 서브 탭 네비게이션 ── */}
      {activeTab === 'energy' && (
        <div style={{ display: 'flex', gap: 0, padding: '0 20px', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>
          {[['monthly', '월간 분석'], ['daily', '일일 현황']].map(([v, l]) => (
            <button key={v} onClick={() => setEnergySubTab(v)}
              style={{
                padding: '9px 18px', background: 'none', border: 'none', cursor: 'pointer',
                fontSize: 12, fontWeight: energySubTab === v ? 700 : 500,
                color: energySubTab === v ? '#0d9488' : 'var(--text3)',
                borderBottom: energySubTab === v ? '2px solid #0d9488' : '2px solid transparent',
                marginBottom: -1, transition: 'all .15s',
              }}>
              {l}
            </button>
          ))}
        </div>
      )}

      {/* ── 일일 현황 ── */}
      {activeTab === 'energy' && energySubTab === 'daily' && <DailyView />}

      {/* ── 운영 보고서 탭 ── */}
      {activeTab === 'ops' && <OpsReportsView />}

      {/* ── 월간 분석 ── */}
      {activeTab === 'energy' && energySubTab === 'monthly' && (
        <>
          {!loading && error && (
            <div style={{ margin: '24px', padding: '14px 18px', background: '#fee2e2', border: '1px solid #f85149', borderRadius: 10, color: '#f85149', fontSize: 13 }}>
              데이터 로드 실패: {error}
            </div>
          )}

          {loading && (
            <div style={s.body}>
              <div style={s.loadingBanner}>
                <div style={s.loadingSpinner}/>
                <div>
                  <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 600 }}>📊 보고서 데이터 집계 중…</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                    월간 KPI · 데이터 품질 · EI · 비용을 동시에 조회하고 있습니다
                  </div>
                </div>
              </div>
              <div style={{ ...s.kpiRow, gridTemplateColumns: 'repeat(5,1fr)' }}>
                {[1,2,3,4,5].map(i => (
                  <div key={i} style={s.kpiCard}>
                    <div style={sk}/><div style={{ ...sk, width: '70%', marginTop: 8 }}/><div style={{ ...sk, width: '45%', marginTop: 8 }}/>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!loading && !latest && <div style={s.empty}>데이터 없음</div>}

          {!loading && latest && (
            <div style={s.body}>
              {/* AI 트렌드 내러티브 */}
              <NarrativeBox
                color="#a371f7"
                title={`🤖 AI 트렌드 분석 — ${latest.period}`}
                text={trendNarrative}
                placeholder='상단 "🪄 AI 전체 분석" 버튼으로 생성'
              />

              {/* KPI 5개 */}
              <div style={{ fontSize: 11, color: 'var(--text4)', marginBottom: -4 }}>
                최신: <b style={{ color: 'var(--text)' }}>{latest.period}</b> 기준
              </div>
              <div style={{ ...s.kpiRow, gridTemplateColumns: 'repeat(5,1fr)' }}>
                <KpiCard
                  label="자급률"
                  value={`${latest.self_sufficiency_pct?.toFixed(1)}%`}
                  unit="Self-Sufficiency" color="#3fb950"
                  yoy={latest.yoy_self_pct} yoyUnit="%p"
                />
                <KpiCard label="평균 COP"      value={latest.avg_cop?.toFixed(2)}                   unit="성능계수"         color="#2563eb"/>
                <KpiCard label="그리드 의존도"  value={`${latest.grid_dependency_pct?.toFixed(1)}%`} unit="Grid Dependency" color="#d29922"/>
                <KpiCard
                  label="이달 전기요금"
                  value={billingCurrent?.actual_eur != null ? `€${Math.round(billingCurrent.actual_eur).toLocaleString()}` : '–'}
                  unit={billingCurrent ? `누적 ${billingCurrent.days_elapsed}/${billingCurrent.days_in_month}일` : '누적'}
                  color={statusColor[billingCurrent?.status] ?? '#d29922'}
                  sub={billingCurrent?.projected_eur != null ? `예상 €${Math.round(billingCurrent.projected_eur).toLocaleString()} · ${billingCurrent.status}` : null}
                />
                <KpiCard label="이상탐지" value={latest.anomaly_count} unit="건" color="#f85149"/>
              </div>

              {/* 추이 차트 */}
              <div style={s.charts}>
                <div style={s.chartBox}>
                  <div style={s.chartTitle}>자급률 · 그리드 의존도 추이 (%)</div>
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                      <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                        interval={Math.max(0, Math.floor(items.length / 8) - 1)}
                        tickFormatter={v => view === 'monthly' ? v?.slice(2) : v}/>
                      <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false} domain={[0, 100]}/>
                      <Tooltip {...tt}/>
                      <Legend wrapperStyle={{ fontSize: 11 }}/>
                      <Line type="monotone" dataKey="self_sufficiency_pct" name="자급률 (%)"       stroke="#3fb950" strokeWidth={2} dot={items.length < 30}/>
                      <Line type="monotone" dataKey="grid_dependency_pct"  name="그리드 의존도 (%)" stroke="#d29922" strokeWidth={2} dot={items.length < 30} strokeDasharray="4 2"/>
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div style={s.chartBox}>
                  <div style={s.chartTitle}>평균 COP 추이</div>
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                      <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                        interval={Math.max(0, Math.floor(items.length / 8) - 1)}
                        tickFormatter={v => view === 'monthly' ? v?.slice(2) : v}/>
                      <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}/>
                      <Tooltip {...tt}/>
                      <Line type="monotone" dataKey="avg_cop" name="COP" stroke="#2563eb" strokeWidth={2} dot={items.length < 30}/>
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* 계절별 바 */}
              {view === 'seasonal' && (
                <div style={s.chartBox}>
                  <div style={s.chartTitle}>계절별 평균 자급률 · 소비량 비교</div>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                      <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}/>
                      <YAxis yAxisId="left"  tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false} tickFormatter={v => `${v.toFixed(0)}%`}/>
                      <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false} tickFormatter={v => `${(v/1000).toFixed(0)}k`}/>
                      <Tooltip {...tt} formatter={(v, n) => n === '자급률' ? [`${v.toFixed(1)}%`, n] : [`${v.toLocaleString()} kWh`, n]}/>
                      <Legend wrapperStyle={{ fontSize: 11 }}/>
                      <Bar yAxisId="left"  dataKey="self_sufficiency_pct"  name="자급률" fill="#3fb950" radius={[3,3,0,0]} opacity={0.85}/>
                      <Bar yAxisId="right" dataKey="total_consumption_kwh" name="소비량" fill="#2563eb" radius={[3,3,0,0]} opacity={0.6}/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* 냉방 vs 외기온 */}
              {coolingVsTemp.length > 1 && (
                <div style={s.chartBox}>
                  <div style={s.chartTitle}>냉방 부하 vs 외기온 상관관계 (월별 평균)</div>
                  <ResponsiveContainer width="100%" height={200}>
                    <ScatterChart margin={{ top: 4, right: 24, left: -10, bottom: 16 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                      <XAxis type="number" dataKey="avg_ta" name="외기온" unit="°C"
                        tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                        label={{ value: '외기온 (°C)', position: 'insideBottom', offset: -6, fill: '#909aa8', fontSize: 10 }}/>
                      <YAxis type="number" dataKey="avg_cool_kw" name="냉방부하" unit=" kW"
                        tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}/>
                      <ZAxis range={[30, 30]}/>
                      <Tooltip {...tt} cursor={{ strokeDasharray: '3 3' }}
                        content={({ payload }) => {
                          if (!payload?.length) return null
                          const d = payload[0].payload
                          return (
                            <div style={{ ...tt.contentStyle, padding: '8px 12px' }}>
                              <div style={{ color: 'var(--text)', marginBottom: 4, fontWeight: 600 }}>{d.period}</div>
                              <div style={{ color: '#f85149' }}>외기온: {d.avg_ta}°C</div>
                              <div style={{ color: '#2563eb' }}>냉방 부하: {d.avg_cool_kw} kW</div>
                            </div>
                          )
                        }}/>
                      <Scatter data={coolingVsTemp} fill="#f85149" opacity={0.75}/>
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* 전월 대비 변화 */}
              {view === 'monthly' && momData.length > 0 && (
                <div style={s.chartBox}>
                  <div style={s.chartTitle}>전월 대비 변화 (최근 12개월)</div>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={momData} margin={{ top: 4, right: 12, left: -10, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                      <XAxis dataKey="period" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false}
                        interval={Math.max(0, Math.floor(momData.length / 8) - 1)}/>
                      <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
                        tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}/>
                      <ReferenceLine y={0} stroke="#e2e7ef" strokeWidth={1}/>
                      <Tooltip {...tt} formatter={(v, n) => v != null ? [`${v > 0 ? '+' : ''}${v.toFixed(1)}%`, n] : ['–', n]}/>
                      <Legend wrapperStyle={{ fontSize: 11 }}/>
                      <Bar dataKey="consumption_delta" name="소비량 변화%"  fill="#2563eb" radius={[2,2,0,0]} opacity={0.8}/>
                      <Bar dataKey="self_delta"         name="자급률 변화%p" fill="#3fb950" radius={[2,2,0,0]} opacity={0.8}/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* 데이터 테이블 */}
              <div style={s.tableWrap}>
                <table style={s.table}>
                  <thead>
                    <tr>
                      {['기간', '소비량 (kWh)', '자급률', 'COP', '그리드 의존도', '이상'].map(h =>
                        <th key={h} style={s.th}>{h}</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {[...items].reverse().map((row, i) => {
                      const seasonName = view === 'seasonal'
                        ? Object.keys(SEASON_COLOR).find(sn => row.period?.includes(sn))
                        : null
                      const sColor = seasonName ? SEASON_COLOR[seasonName] : null
                      return (
                        <tr key={i} style={s.tr}>
                          <td style={{ ...s.td, fontWeight: 600 }}>
                            <span style={{ color: sColor ?? 'var(--text)' }}>{row.period}</span>
                          </td>
                          <td style={s.td}>{row.total_consumption_kwh?.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}</td>
                          <td style={s.td}>
                            <span style={{ color: '#3fb950' }}>{row.self_sufficiency_pct?.toFixed(1)}%</span>
                            <MiniProgressBar pct={row.self_sufficiency_pct} color="#3fb950"/>
                          </td>
                          <td style={s.td}>{row.avg_cop?.toFixed(2)}</td>
                          <td style={s.td}>
                            <span style={{ color: '#d29922' }}>{row.grid_dependency_pct?.toFixed(1)}%</span>
                            <MiniProgressBar pct={row.grid_dependency_pct} color="#d29922"/>
                          </td>
                          <td style={{ ...s.td, color: (row.anomaly_count ?? 0) > 0 ? '#f85149' : 'var(--text4)' }}>
                            {row.anomaly_count}건
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text4)', textAlign: 'right' }}>
                {viewLabel[view]} · 총 {items.length}개 행 · {months}개월 원본 기준
              </div>

              {/* EI 섹션 */}
              {eiData && (eiData.items ?? []).some(it => it.ei_total != null) && (
                <div style={s.eiBox}>
                  <div style={s.eiHeader}>
                    <span style={s.eiTitle}>🌡️ 외기온 정규화 에너지 원단위 (EI)</span>
                    {eiData.ei_avg != null && (
                      <span style={s.eiAvgBadge}>전체 평균 {eiData.ei_avg} kWh/DD</span>
                    )}
                  </div>
                  <div style={s.eiDesc}>날씨 영향을 제거한 실질 효율 지표 — 낮을수록 에너지 효율이 높음 (DD = Degree Days, 기준온도 18/22°C)</div>
                  <NarrativeBox color="#d29922" title="🤖 AI EI 분석" text={eiData.narrative ?? ''} placeholder='상단 "🪄 AI 전체 분석" 버튼으로 생성'/>
                  <div style={{ marginTop: 10 }}>
                    <ResponsiveContainer width="100%" height={200}>
                      <LineChart data={(eiData.items ?? []).filter(it => it.ei_total != null)} margin={{ top: 8, right: 16, left: -10, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                        <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} tickFormatter={v => v?.slice(2)}
                          interval={Math.floor((eiData.items?.filter(it => it.ei_total != null).length ?? 0) / 10)}/>
                        <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}/>
                        <Tooltip contentStyle={tt.contentStyle} labelStyle={tt.labelStyle} formatter={(v, n) => [`${v} kWh/DD`, n === 'ei_total' ? '정규화 EI' : n]}/>
                        {eiData.ei_avg != null && (
                          <ReferenceLine y={eiData.ei_avg} stroke="#2563eb44" strokeDasharray="6 3"
                            label={{ value: `평균 ${eiData.ei_avg}`, fill: '#2563eb', fontSize: 10, position: 'right' }}/>
                        )}
                        <Line type="monotone" dataKey="ei_total" name="ei_total" stroke="#d29922" strokeWidth={2} dot={false} connectNulls/>
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* 데이터 품질 섹션 */}
              {balance && (
                <div style={s.balanceBox}>
                  <div style={s.balanceHeader}>
                    <span style={s.balanceTitle}>⚖️ 데이터 품질 검증</span>
                    <div style={s.balanceSummary}>
                      <span style={{ color: '#3fb950' }}>✓ 정상 {balance.ok_count}개월</span>
                      {balance.warn_count > 0 && <span style={{ color: '#d29922', marginLeft: 12 }}>⚠ 주의 {balance.warn_count}개월</span>}
                      {balance.bad_count  > 0 && <span style={{ color: '#f85149', marginLeft: 12 }}>✗ 불량 {balance.bad_count}개월</span>}
                    </div>
                  </div>
                  <NarrativeBox color="#2563eb" title="🤖 AI 품질 분석" text={balance.narrative ?? ''} placeholder='상단 "🪄 AI 전체 분석" 버튼으로 생성'/>
                  <div style={{ ...s.qualityGrid, marginTop: 10 }}>
                    {(balance.items ?? []).map(it => {
                      const score = it.quality_score ?? 100
                      const color = score >= 80 ? '#3fb950' : score >= 50 ? '#d29922' : '#f85149'
                      return (
                        <div key={it.period} title={(it.balance_flags ?? []).length > 0 ? it.balance_flags.join(', ') : '정상'}
                          style={{ ...s.qualityCell, background: color + '22', border: `1px solid ${color}55`, color }}>
                          <div style={{ fontSize: 9 }}>{it.period.slice(2)}</div>
                          <div style={{ fontSize: 11, fontWeight: 700 }}>{score}</div>
                        </div>
                      )
                    })}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 6 }}>셀에 마우스를 올리면 품질 이슈 상세 확인</div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

const sk = { background: 'var(--line)', borderRadius: 4, height: 14, width: '60%' }

const s = {
  wrap:      { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:    { padding: '12px 20px 10px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, flexWrap: 'wrap' },
  title:     { fontWeight: 600, fontSize: 15, color: 'var(--text)', display: 'inline-flex', alignItems: 'center', gap: 8, marginRight: 4 },
  empty:     { textAlign: 'center', color: 'var(--text3)', paddingTop: 60 },
  body:      { flex: 1, overflowY: 'auto', padding: '16px 20px 24px', display: 'flex', flexDirection: 'column', gap: 14 },
  kpiRow:    { display: 'grid', gap: 10 },
  kpiCard:   { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, padding: '12px 16px' },
  charts:    { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  chartBox:  { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, padding: '14px 16px' },
  chartTitle:{ fontSize: 12, fontWeight: 600, color: 'var(--text3)', marginBottom: 10 },
  tableWrap: { overflowX: 'auto' },
  table:     { width: '100%', borderCollapse: 'collapse' },
  th:        { textAlign: 'left', padding: '8px 12px', fontSize: 12, color: 'var(--text3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap' },
  tr:        { borderBottom: '1px solid var(--surface)' },
  td:        { padding: '9px 12px', fontSize: 13, color: 'var(--text3)', verticalAlign: 'middle' },
  opsTh:     { padding: '6px 8px', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' },
  opsTd:     { padding: '6px 8px', color: 'var(--text3)', whiteSpace: 'nowrap' },
  opsChartWrap: { height: 220, marginTop: 8 },
  opsSection: { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '12px 14px', marginTop: 12 },
  opsSectionTitle: { fontSize: 13, fontWeight: 800, color: 'var(--text)', marginBottom: 8 },
  opsSummaryGrid: { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8 },
  opsMetric: { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '9px 10px' },
  opsDetail: { minWidth: 0, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: '16px 18px' },
  opsSummaryText: { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '12px 14px', color: 'var(--text2)', fontSize: 13, lineHeight: 1.7, whiteSpace: 'pre-wrap' },
  opsOkBox: { background: '#3fb95014', border: '1px solid #3fb95055', color: '#3fb950', borderRadius: 8, padding: '10px 12px', fontSize: 12, lineHeight: 1.6 },
  opsListItem: { width: '100%', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '12px 13px', cursor: 'pointer' },
  opsListItemActive: { border: '1px solid #0d9488', boxShadow: '0 0 0 1px #0d948844 inset', background: '#0d948812' },
  opsFocusBox: { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 10px' },
  opsFocusTitle: { fontSize: 11, fontWeight: 700, color: 'var(--text3)', marginBottom: 6 },
  opsFocusRow: { display: 'grid', gridTemplateColumns: 'minmax(64px, 1fr) auto auto auto', gap: 8, alignItems: 'center', fontSize: 11, color: 'var(--text3)', padding: '3px 0' },
  opsMeterName: { color: 'var(--text)', fontWeight: 700, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' },

  tabGroup:  { display: 'flex', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', marginRight: 4 },
  tab:       { padding: '5px 14px', background: 'none', border: 'none', color: 'var(--text3)', fontSize: 12, cursor: 'pointer', fontWeight: 500 },
  tabActive: { background: '#0d948822', color: '#0d9488', fontWeight: 700 },

  segGroup:  { display: 'flex', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' },
  seg:       { padding: '4px 12px', background: 'none', border: 'none', color: 'var(--text3)', fontSize: 12, cursor: 'pointer' },
  segActive: { background: '#2563eb33', color: '#2563eb', fontWeight: 600 },

  aiAllBtn:  { padding: '5px 14px', background: 'linear-gradient(135deg, #a371f722, #7c3aed11)', border: '1px solid #a371f766', borderRadius: 6, color: '#a371f7', fontSize: 12, fontWeight: 700, cursor: 'pointer' },

  csvBtn:    { padding: '5px 12px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', fontWeight: 500 },
  docBtn:    { padding: '5px 12px', background: 'var(--surface)', border: '1px solid var(--brand)', borderRadius: 6, color: 'var(--brand)', fontSize: 12, cursor: 'pointer', fontWeight: 600 },

  loadingBanner:  { display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px', background: 'linear-gradient(135deg, #2563eb15, var(--surface))', border: '1px solid #2563eb44', borderRadius: 10 },
  loadingSpinner: { width: 22, height: 22, borderRadius: '50%', border: '2px solid var(--border)', borderTopColor: '#2563eb', animation: 'spin 0.8s linear infinite', flexShrink: 0 },

  dateNavBtn: { padding: '5px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 16, cursor: 'pointer', lineHeight: 1 },

  eiBox:      { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px' },
  eiHeader:   { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6, flexWrap: 'wrap', gap: 8 },
  eiTitle:    { fontSize: 13, fontWeight: 700, color: '#d29922' },
  eiAvgBadge: { fontSize: 11, color: '#2563eb', background: '#2563eb18', borderRadius: 4, padding: '2px 8px' },
  eiDesc:     { fontSize: 11, color: 'var(--text4)', marginBottom: 8 },

  balanceBox:     { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px' },
  balanceHeader:  { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexWrap: 'wrap', gap: 8 },
  balanceTitle:   { fontSize: 13, fontWeight: 700, color: '#2563eb' },
  balanceSummary: { fontSize: 12 },
  qualityGrid:    { display: 'flex', flexWrap: 'wrap', gap: 4 },
  qualityCell:    { borderRadius: 4, padding: '4px 6px', textAlign: 'center', minWidth: 36, cursor: 'default' },
}
