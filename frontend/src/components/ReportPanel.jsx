import { useState, useEffect } from 'react'
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { getReport } from '../api/client'

const tooltip = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e6edf3' },
}

function KpiCard({ label, value, unit, color = '#58a6ff' }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value ?? '–'}</div>
      <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>{unit}</div>
      <div style={{ fontSize: 12, color: '#6e7681', marginTop: 4 }}>{label}</div>
    </div>
  )
}

function Bar2({ pct, color }) {
  return (
    <div style={{ background: '#21262d', borderRadius: 4, height: 6, overflow: 'hidden' }}>
      <div style={{ width: `${Math.min(100, pct ?? 0)}%`, height: '100%', background: color, borderRadius: 4, transition: 'width .4s' }} />
    </div>
  )
}

function downloadCSV(items) {
  const headers = ['기간', '소비량(kWh)', '자급률(%)', 'COP', '그리드의존도(%)', '이상건수']
  const rows = [...items].reverse().map(r => [
    r.period,
    r.total_consumption_kwh?.toFixed(0) ?? '',
    r.self_sufficiency_pct?.toFixed(1) ?? '',
    r.avg_cop?.toFixed(2) ?? '',
    r.grid_dependency_pct?.toFixed(1) ?? '',
    r.anomaly_count ?? '',
  ])
  const csv = [headers, ...rows].map(r => r.join(',')).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url; a.download = 'energy_kpi_report.csv'; a.click()
  URL.revokeObjectURL(url)
}

export default function ReportPanel() {
  const [items,   setItems]   = useState([])
  const [months,  setMonths]  = useState(24)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getReport(months).then(r => setItems((r.data.items ?? []).reverse())).finally(() => setLoading(false))
  }, [months])

  const latest = items[items.length - 1]

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <span style={s.title}>KPI 월간 보고서</span>
        <div style={{ display:'flex', alignItems:'center', gap:8, marginLeft:'auto' }}>
          {[12,24,72].map(m => (
            <button key={m} style={{ ...s.mBtn, ...(months===m ? s.mBtnActive : {}) }}
              onClick={() => setMonths(m)}>{m}개월</button>
          ))}
          {items.length > 0 && (
            <button style={s.csvBtn} onClick={() => downloadCSV(items)}>
              CSV 다운로드
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div style={s.body}>
          <div style={s.kpiRow}>
            {[1,2,3,4].map(i => (
              <div key={i} style={s.kpiCard}>
                <div className="skeleton" style={{ height: 28, width: '55%', marginBottom: 8 }}/>
                <div className="skeleton" style={{ height: 11, width: '70%', marginBottom: 8 }}/>
                <div className="skeleton" style={{ height: 13, width: '45%' }}/>
              </div>
            ))}
          </div>
          <div style={s.charts}>
            {[1,2].map(i => (
              <div key={i} style={s.chartBox}>
                <div className="skeleton" style={{ height: 13, width: '40%', marginBottom: 12 }}/>
                <div className="skeleton" style={{ height: 180, borderRadius: 8 }}/>
              </div>
            ))}
          </div>
          <div className="skeleton" style={{ height: 300, borderRadius: 8 }}/>
        </div>
      )}
      {!loading && !latest && <div style={s.empty}>데이터 없음</div>}

      {!loading && latest && (
        <div style={s.body}>
          {/* 최신 월 KPI 카드 */}
          <div style={s.kpiRow}>
            <KpiCard label="자급률" value={`${latest.self_sufficiency_pct?.toFixed(1)}%`} unit="Self-Sufficiency" color="#3fb950"/>
            <KpiCard label="평균 COP" value={latest.avg_cop?.toFixed(2)} unit="성능계수" color="#58a6ff"/>
            <KpiCard label="그리드 의존도" value={`${latest.grid_dependency_pct?.toFixed(1)}%`} unit="Grid Dependency" color="#d29922"/>
            <KpiCard label="이상탐지" value={latest.anomaly_count} unit="건" color="#f85149"/>
          </div>

          {/* 트렌드 차트 2열 */}
          <div style={s.charts}>
            <div style={s.chartBox}>
              <div style={s.chartTitle}>자급률 추이 (%)</div>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false} axisLine={false} domain={[0, 100]}/>
                  <Tooltip {...tooltip}/>
                  <Line type="monotone" dataKey="self_sufficiency_pct" name="자급률 (%)"
                    stroke="#3fb950" strokeWidth={2} dot={{ r: 3, fill: '#3fb950' }}/>
                  <Line type="monotone" dataKey="grid_dependency_pct" name="그리드 의존도 (%)"
                    stroke="#d29922" strokeWidth={2} dot={{ r: 3, fill: '#d29922' }} strokeDasharray="4 2"/>
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div style={s.chartBox}>
              <div style={s.chartTitle}>평균 COP 추이</div>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={items} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltip}/>
                  <Line type="monotone" dataKey="avg_cop" name="COP"
                    stroke="#58a6ff" strokeWidth={2} dot={{ r: 3, fill: '#58a6ff' }}/>
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* 월별 테이블 */}
          <div style={s.tableWrap}>
            <table style={s.table}>
              <thead>
                <tr>{['기간','소비량 (kWh)','자급률','COP','그리드 의존도','이상'].map(h =>
                  <th key={h} style={s.th}>{h}</th>
                )}</tr>
              </thead>
              <tbody>
                {[...items].reverse().map(row => (
                  <tr key={row.period} style={s.tr}>
                    <td style={{ ...s.td, color: '#e6edf3', fontWeight: 600 }}>{row.period}</td>
                    <td style={s.td}>{row.total_consumption_kwh?.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}</td>
                    <td style={s.td}>
                      <div style={{ display:'flex', flexDirection:'column', gap:3 }}>
                        <span style={{ color:'#3fb950' }}>{row.self_sufficiency_pct?.toFixed(1)}%</span>
                        <Bar2 pct={row.self_sufficiency_pct} color="#3fb950"/>
                      </div>
                    </td>
                    <td style={s.td}>{row.avg_cop?.toFixed(2)}</td>
                    <td style={s.td}>
                      <div style={{ display:'flex', flexDirection:'column', gap:3 }}>
                        <span style={{ color:'#d29922' }}>{row.grid_dependency_pct?.toFixed(1)}%</span>
                        <Bar2 pct={row.grid_dependency_pct} color="#d29922"/>
                      </div>
                    </td>
                    <td style={{ ...s.td, color: row.anomaly_count > 0 ? '#f85149' : '#6e7681' }}>
                      {row.anomaly_count}건
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

const s = {
  wrap:      { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:    { padding: '16px 20px 12px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 },
  title:     { fontWeight: 600, fontSize: 15, color: '#e6edf3' },
  sub:       { fontSize: 12, color: '#8b949e' },
  mBtn:      { padding: '4px 10px', background: '#161b22', border: '1px solid #30363d', borderRadius: 6, color: '#8b949e', fontSize: 12, cursor: 'pointer' },
  mBtnActive:{ borderColor: '#58a6ff', color: '#58a6ff', background: '#1f6feb22' },
  csvBtn:    { padding: '5px 12px', background: '#21262d', border: '1px solid #30363d', borderRadius: 6, color: '#e6edf3', fontSize: 12, cursor: 'pointer', fontWeight: 500 },
  empty:     { textAlign: 'center', color: '#8b949e', paddingTop: 60 },
  body:      { flex: 1, overflowY: 'auto', padding: '16px 20px 24px' },
  kpiRow:    { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 16 },
  kpiCard:   { background: '#0d1117', border: '1px solid #21262d', borderRadius: 8, padding: '12px 16px' },
  charts:    { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 },
  chartBox:  { background: '#0d1117', border: '1px solid #21262d', borderRadius: 8, padding: '14px 16px' },
  chartTitle:{ fontSize: 12, fontWeight: 600, color: '#8b949e', marginBottom: 10 },
  tableWrap: { overflowX: 'auto' },
  table:     { width: '100%', borderCollapse: 'collapse' },
  th:        { textAlign: 'left', padding: '8px 12px', fontSize: 12, color: '#8b949e', borderBottom: '1px solid #21262d', fontWeight: 500 },
  tr:        { borderBottom: '1px solid #161b22' },
  td:        { padding: '10px 12px', fontSize: 13, color: '#8b949e', verticalAlign: 'middle' },
}
