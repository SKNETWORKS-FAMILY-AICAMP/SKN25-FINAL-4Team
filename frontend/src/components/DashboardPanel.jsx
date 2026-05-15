import { useState, useEffect } from 'react'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { getReport, getAnomalySummary, getAnomalyTimeline } from '../api/client'

const SEV_COLOR = { HIGH: '#f85149', MEDIUM: '#d29922', LOW: '#58a6ff' }

function KpiCard({ label, value, sub, color = '#58a6ff', trend }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ fontSize: 26, fontWeight: 700, color }}>{value ?? '–'}</div>
      <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>{sub}</div>
      <div style={{ fontSize: 13, color: '#e6edf3', marginTop: 6 }}>{label}</div>
      {trend !== undefined && (
        <div style={{ fontSize: 11, color: trend >= 0 ? '#3fb950' : '#f85149', marginTop: 4 }}>
          {trend >= 0 ? '▲' : '▼'} {Math.abs(trend).toFixed(1)}%p (전월 대비)
        </div>
      )}
    </div>
  )
}

const tooltipStyle = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e6edf3' },
  itemStyle: { color: '#8b949e' },
}

export default function DashboardPanel() {
  const [report,   setReport]   = useState([])
  const [summary,  setSummary]  = useState([])
  const [timeline, setTimeline] = useState([])
  const [loading,  setLoading]  = useState(true)

  useEffect(() => {
    Promise.allSettled([getReport(24), getAnomalySummary(), getAnomalyTimeline()])
      .then(([r, s, t]) => {
        if (r.status === 'fulfilled') setReport((r.value.data.items ?? []).reverse())
        if (s.status === 'fulfilled') setSummary(s.value.data.summary ?? [])
        if (t.status === 'fulfilled') setTimeline(t.value.data.timeline ?? [])
      })
      .finally(() => setLoading(false))
  }, [])

  const latest = report[report.length - 1]
  const prev   = report[report.length - 2]
  const totalAnomalies = summary.reduce((a, b) => a + b.count, 0)

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <span style={s.title}>대시보드</span>
        <span style={s.sub}>Honda R&D Europe · 독일 오펜바흐</span>
      </div>

      {loading && (
        <div style={{ ...s.body }}>
          <div style={s.kpiRow}>
            {[1,2,3,4].map(i => (
              <div key={i} style={s.kpiCard}>
                <div className="skeleton" style={{ height: 32, width: '60%', marginBottom: 8 }}/>
                <div className="skeleton" style={{ height: 12, width: '80%', marginBottom: 8 }}/>
                <div className="skeleton" style={{ height: 14, width: '50%' }}/>
              </div>
            ))}
          </div>
          <div style={s.chartGrid}>
            {[1,2,3,4].map(i => (
              <div key={i} style={s.chartBox}>
                <div className="skeleton" style={{ height: 14, width: '40%', marginBottom: 12 }}/>
                <div className="skeleton" style={{ height: 200, borderRadius: 8 }}/>
              </div>
            ))}
          </div>
        </div>
      )}

      {!loading && latest && (
        <div style={s.body}>
          {/* KPI 카드 */}
          <div style={s.kpiRow}>
            <KpiCard
              label="자급률" value={`${latest.self_sufficiency_pct?.toFixed(1)}%`}
              sub="Self-Sufficiency" color="#3fb950"
              trend={prev ? latest.self_sufficiency_pct - prev.self_sufficiency_pct : undefined}
            />
            <KpiCard
              label="평균 COP" value={latest.avg_cop?.toFixed(2)}
              sub="냉방 성능계수" color="#58a6ff"
              trend={prev ? latest.avg_cop - prev.avg_cop : undefined}
            />
            <KpiCard
              label="그리드 의존도" value={`${latest.grid_dependency_pct?.toFixed(1)}%`}
              sub="Grid Dependency" color="#d29922"
              trend={prev ? -(latest.grid_dependency_pct - prev.grid_dependency_pct) : undefined}
            />
            <KpiCard
              label="누적 이상탐지" value={totalAnomalies}
              sub="전체 기간" color="#f85149"
            />
          </div>

          {/* 차트 2열 */}
          <div style={s.chartGrid}>
            {/* 자급률 트렌드 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>자급률 · 그리드 의존도 추이 (%)</div>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={report} margin={{ top: 8, right: 16, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gSS" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#3fb950" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#3fb950" stopOpacity={0}/>
                    </linearGradient>
                    <linearGradient id="gGD" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#d29922" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#d29922" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltipStyle}/>
                  <Area type="monotone" dataKey="self_sufficiency_pct" name="자급률"
                    stroke="#3fb950" fill="url(#gSS)" strokeWidth={2} dot={false}/>
                  <Area type="monotone" dataKey="grid_dependency_pct" name="그리드 의존도"
                    stroke="#d29922" fill="url(#gGD)" strokeWidth={2} dot={false}/>
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* COP 트렌드 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>평균 COP 추이</div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={report} margin={{ top: 8, right: 16, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltipStyle}/>
                  <Line type="monotone" dataKey="avg_cop" name="COP"
                    stroke="#58a6ff" strokeWidth={2} dot={{ r: 3, fill: '#58a6ff' }}/>
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* 월별 소비량 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 총 소비량 (kWh)</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={report} margin={{ top: 8, right: 16, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${(v/1000).toFixed(0)}k`}/>
                  <Tooltip {...tooltipStyle} formatter={v => [`${v.toLocaleString()} kWh`]}/>
                  <Bar dataKey="total_consumption_kwh" name="소비량" fill="#1f6feb" radius={[3,3,0,0]}/>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* 이상탐지 타임라인 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 이상탐지 현황</div>
              {timeline.length === 0
                ? <div style={{ color: '#8b949e', fontSize: 13, paddingTop: 40, textAlign: 'center' }}>배치 실행 후 표시됩니다</div>
                : (
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={timeline} margin={{ top: 8, right: 16, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                      <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false}/>
                      <YAxis tick={{ fontSize: 11, fill: '#8b949e' }} tickLine={false} axisLine={false}/>
                      <Tooltip {...tooltipStyle}/>
                      <Legend wrapperStyle={{ fontSize: 11, color: '#8b949e' }}/>
                      <Bar dataKey="HIGH"   name="HIGH"   stackId="a" fill={SEV_COLOR.HIGH}   radius={[0,0,0,0]}/>
                      <Bar dataKey="MEDIUM" name="MEDIUM" stackId="a" fill={SEV_COLOR.MEDIUM}/>
                      <Bar dataKey="LOW"    name="LOW"    stackId="a" fill={SEV_COLOR.LOW} radius={[3,3,0,0]}/>
                    </BarChart>
                  </ResponsiveContainer>
                )
              }
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:     { padding: '16px 24px 12px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 },
  title:      { fontWeight: 700, fontSize: 16, color: '#e6edf3' },
  sub:        { fontSize: 12, color: '#8b949e' },
  empty:      { textAlign: 'center', color: '#8b949e', paddingTop: 60 },
  body:       { flex: 1, overflowY: 'auto', padding: '20px 24px' },
  kpiRow:     { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 },
  kpiCard:    { background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '16px 18px' },
  chartGrid:  { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 },
  chartBox:   { background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '16px 18px' },
  chartTitle: { fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 12 },
}
