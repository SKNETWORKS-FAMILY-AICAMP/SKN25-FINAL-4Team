import { useState, useEffect, useMemo } from 'react'
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { getReport } from '../api/client'

const tooltip = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e6edf3' },
}

// ── 집계 유틸 ──────────────────────────────────────────────────────
const SEASON_ORDER = { '봄': 1, '여름': 2, '가을': 3, '겨울': 4 }
const SEASON_COLOR = { '봄': '#3fb950', '여름': '#f85149', '가을': '#d29922', '겨울': '#58a6ff' }
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
  return aggregate(
    items,
    item => item.period.slice(0, 4),
    (_, key) => key + '년',
    (a, b) => a.localeCompare(b),
  )
}

function groupBySeason(items) {
  return aggregate(
    items,
    item => {
      const y = parseInt(item.period.slice(0, 4))
      const m = parseInt(item.period.slice(5, 7))
      // 12월은 다음 해 겨울로 분류
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

// ── 컴포넌트 ────────────────────────────────────────────────────────
function KpiCard({ label, value, unit, color = '#58a6ff' }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value ?? '–'}</div>
      <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>{unit}</div>
      <div style={{ fontSize: 12, color: '#6e7681', marginTop: 4 }}>{label}</div>
    </div>
  )
}

function MiniProgressBar({ pct, color }) {
  return (
    <div style={{ background: '#21262d', borderRadius: 4, height: 5, overflow: 'hidden', marginTop: 3 }}>
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

export default function ReportPanel() {
  const [raw,     setRaw]     = useState([])
  const [months,  setMonths]  = useState(72)
  const [view,    setView]    = useState('monthly')   // 'monthly' | 'seasonal' | 'yearly'
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    getReport(months)
      .then(r => {
        if (r.data.error) throw new Error(r.data.error)
        setRaw((r.data.items ?? []).reverse())
      })
      .catch(e => setError(e.message ?? '데이터 로드 실패'))
      .finally(() => setLoading(false))
  }, [months])

  // 뷰에 따라 집계
  const items = useMemo(() => {
    if (view === 'yearly')   return groupByYear(raw)
    if (view === 'seasonal') return groupBySeason(raw)
    return raw
  }, [raw, view])

  const latest = items[items.length - 1]

  const viewLabel = { monthly: '월별', seasonal: '계절별', yearly: '연도별' }

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.header}>
        <span style={s.title}>KPI 보고서</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto', flexWrap: 'wrap' }}>
          {/* 집계 단위 */}
          <div style={s.segGroup}>
            {[['monthly','월별'], ['seasonal','계절별'], ['yearly','연도별']].map(([v, l]) => (
              <button key={v} style={{ ...s.seg, ...(view === v ? s.segActive : {}) }}
                onClick={() => setView(v)}>{l}</button>
            ))}
          </div>
          {/* 조회 기간 */}
          <div style={s.segGroup}>
            {[12, 24, 72].map(m => (
              <button key={m} style={{ ...s.seg, ...(months === m ? s.segActive : {}) }}
                onClick={() => setMonths(m)}>{m}개월</button>
            ))}
          </div>
          {items.length > 0 && (
            <button style={s.csvBtn} onClick={() => downloadCSV(items, view)}>
              CSV 다운로드
            </button>
          )}
        </div>
      </div>

      {/* 에러 */}
      {!loading && error && (
        <div style={{ margin: '24px', padding: '14px 18px', background: '#2d1517', border: '1px solid #f85149', borderRadius: 10, color: '#f85149', fontSize: 13 }}>
          데이터 로드 실패: {error}
          <button onClick={() => { setError(''); setLoading(true); getReport(months).then(r => setRaw((r.data.items ?? []).reverse())).catch(e => setError(e.message ?? '')).finally(() => setLoading(false)) }}
            style={{ marginLeft: 12, padding: '3px 10px', background: 'none', border: '1px solid #f85149', borderRadius: 6, color: '#f85149', cursor: 'pointer', fontSize: 12 }}>
            재시도
          </button>
        </div>
      )}

      {/* 로딩 스켈레톤 */}
      {loading && (
        <div style={s.body}>
          <div style={s.kpiRow}>
            {[1,2,3,4].map(i => (
              <div key={i} style={s.kpiCard}>
                <div style={sk} /><div style={{ ...sk, width: '70%', marginTop: 8 }}/><div style={{ ...sk, width: '45%', marginTop: 8 }}/>
              </div>
            ))}
          </div>
          <div style={s.charts}>{[1,2].map(i => <div key={i} style={s.chartBox}><div style={{ ...sk, width: '40%', marginBottom: 12 }}/><div style={{ ...sk, height: 180, borderRadius: 8 }}/></div>)}</div>
          <div style={{ ...sk, height: 300, borderRadius: 8 }}/>
        </div>
      )}

      {!loading && !latest && <div style={s.empty}>데이터 없음</div>}

      {!loading && latest && (
        <div style={s.body}>
          {/* KPI 카드 — 최신 기간 기준 */}
          <div style={{ fontSize: 11, color: '#6e7681', marginBottom: 8 }}>
            최신: <b style={{ color: '#e6edf3' }}>{latest.period}</b> 기준
          </div>
          <div style={s.kpiRow}>
            <KpiCard label="자급률"      value={`${latest.self_sufficiency_pct?.toFixed(1)}%`} unit="Self-Sufficiency" color="#3fb950"/>
            <KpiCard label="평균 COP"    value={latest.avg_cop?.toFixed(2)}                    unit="성능계수"         color="#58a6ff"/>
            <KpiCard label="그리드 의존도" value={`${latest.grid_dependency_pct?.toFixed(1)}%`} unit="Grid Dependency" color="#d29922"/>
            <KpiCard label="이상탐지"    value={latest.anomaly_count}                          unit="건"              color="#f85149"/>
          </div>

          {/* 트렌드 차트 */}
          <div style={s.charts}>
            <div style={s.chartBox}>
              <div style={s.chartTitle}>자급률 · 그리드 의존도 추이 (%)</div>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}
                    interval={Math.max(0, Math.floor(items.length / 8) - 1)}
                    tickFormatter={v => view === 'monthly' ? v?.slice(2) : v}/>
                  <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false} domain={[0, 100]}/>
                  <Tooltip {...tooltip}/>
                  <Legend wrapperStyle={{ fontSize: 11 }}/>
                  <Line type="monotone" dataKey="self_sufficiency_pct"  name="자급률 (%)"       stroke="#3fb950" strokeWidth={2} dot={items.length < 30}/>
                  <Line type="monotone" dataKey="grid_dependency_pct"   name="그리드 의존도 (%)" stroke="#d29922" strokeWidth={2} dot={items.length < 30} strokeDasharray="4 2"/>
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div style={s.chartBox}>
              <div style={s.chartTitle}>평균 COP 추이</div>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}
                    interval={Math.max(0, Math.floor(items.length / 8) - 1)}
                    tickFormatter={v => view === 'monthly' ? v?.slice(2) : v}/>
                  <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltip}/>
                  <Line type="monotone" dataKey="avg_cop" name="COP" stroke="#58a6ff" strokeWidth={2} dot={items.length < 30}/>
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* 계절별 전용 — 계절 평균 자급률 비교 바 */}
          {view === 'seasonal' && (
            <div style={s.chartBox}>
              <div style={s.chartTitle}>계절별 평균 자급률 · 소비량 비교</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis yAxisId="left" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v.toFixed(0)}%`}/>
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${(v/1000).toFixed(0)}k`}/>
                  <Tooltip {...tooltip} formatter={(v, n) => n === '자급률' ? [`${v.toFixed(1)}%`, n] : [`${v.toLocaleString()} kWh`, n]}/>
                  <Legend wrapperStyle={{ fontSize: 11 }}/>
                  <Bar yAxisId="left"  dataKey="self_sufficiency_pct"  name="자급률"  fill="#3fb950" radius={[3,3,0,0]} opacity={0.85}/>
                  <Bar yAxisId="right" dataKey="total_consumption_kwh" name="소비량"  fill="#1f6feb" radius={[3,3,0,0]} opacity={0.6}/>
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
                  // 계절 추출 (계절별 뷰에서만)
                  const seasonName = view === 'seasonal'
                    ? Object.keys(SEASON_COLOR).find(sn => row.period?.includes(sn))
                    : null
                  const sColor = seasonName ? SEASON_COLOR[seasonName] : null
                  return (
                    <tr key={i} style={s.tr}>
                      <td style={{ ...s.td, fontWeight: 600 }}>
                        {sColor
                          ? <span style={{ color: sColor }}>{row.period}</span>
                          : <span style={{ color: '#e6edf3' }}>{row.period}</span>
                        }
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
                      <td style={{ ...s.td, color: (row.anomaly_count ?? 0) > 0 ? '#f85149' : '#6e7681' }}>
                        {row.anomaly_count}건
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: 11, color: '#6e7681', textAlign: 'right' }}>
            {viewLabel[view]} · 총 {items.length}개 행 · {months}개월 원본 기준
          </div>
        </div>
      )}
    </div>
  )
}

const sk = { background: '#21262d', borderRadius: 4, height: 14, width: '60%' }

const s = {
  wrap:      { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:    { padding: '14px 20px 10px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' },
  title:     { fontWeight: 600, fontSize: 15, color: '#e6edf3' },
  empty:     { textAlign: 'center', color: '#8b949e', paddingTop: 60 },
  body:      { flex: 1, overflowY: 'auto', padding: '16px 20px 24px', display: 'flex', flexDirection: 'column', gap: 14 },
  kpiRow:    { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10 },
  kpiCard:   { background: '#0d1117', border: '1px solid #21262d', borderRadius: 8, padding: '12px 16px' },
  charts:    { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  chartBox:  { background: '#0d1117', border: '1px solid #21262d', borderRadius: 8, padding: '14px 16px' },
  chartTitle:{ fontSize: 12, fontWeight: 600, color: '#8b949e', marginBottom: 10 },
  tableWrap: { overflowX: 'auto' },
  table:     { width: '100%', borderCollapse: 'collapse' },
  th:        { textAlign: 'left', padding: '8px 12px', fontSize: 12, color: '#8b949e', borderBottom: '1px solid #21262d', fontWeight: 500, whiteSpace: 'nowrap' },
  tr:        { borderBottom: '1px solid #161b22' },
  td:        { padding: '9px 12px', fontSize: 13, color: '#8b949e', verticalAlign: 'middle' },
  segGroup:  { display: 'flex', background: '#161b22', border: '1px solid #30363d', borderRadius: 6, overflow: 'hidden' },
  seg:       { padding: '4px 12px', background: 'none', border: 'none', color: '#8b949e', fontSize: 12, cursor: 'pointer' },
  segActive: { background: '#1f6feb33', color: '#58a6ff', fontWeight: 600 },
  csvBtn:    { padding: '5px 12px', background: '#21262d', border: '1px solid #30363d', borderRadius: 6, color: '#e6edf3', fontSize: 12, cursor: 'pointer', fontWeight: 500 },
}
