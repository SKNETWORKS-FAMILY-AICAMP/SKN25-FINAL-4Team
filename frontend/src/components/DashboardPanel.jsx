import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from 'recharts'
import { getReport, getAnomalySummary, getAnomalies } from '../api/client'

const SEV_COLOR  = { HIGH: '#f85149', MEDIUM: '#d29922', LOW: '#58a6ff' }
const TYPE_LABEL = {
  COPDrop: 'COP 급락', CHPOutage: 'CHP 정지', PowerSpike: '전력 급등',
  NightConsumption: '야간 소비', PVNightNonZero: 'PV 야간 비정상', Unknown: '기타',
}

const tt = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e6edf3' },
  itemStyle: { color: '#8b949e' },
}

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

function MiniBar({ label, kwh, total, color }) {
  const pct = total > 0 ? (kwh / total) * 100 : 0
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: '#8b949e' }}>{label}</span>
        <span style={{ color, fontWeight: 600 }}>
          {(kwh / 1000).toFixed(0)} MWh
          <span style={{ color: '#6e7681', fontWeight: 400, marginLeft: 6 }}>({pct.toFixed(1)}%)</span>
        </span>
      </div>
      <div style={{ background: '#21262d', borderRadius: 4, height: 7, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width .5s' }}/>
      </div>
    </div>
  )
}

export default function DashboardPanel() {
  const [report,   setReport]   = useState([])
  const [summary,  setSummary]  = useState([])
  const [recent,   setRecent]   = useState([])
  const [loading,  setLoading]  = useState(true)

  useEffect(() => {
    Promise.allSettled([
      getReport(24),
      getAnomalySummary(),
      getAnomalies(5, 'HIGH'),
    ]).then(([r, s, a]) => {
      if (r.status === 'fulfilled') setReport((r.value.data.items ?? []).reverse())
      if (s.status === 'fulfilled') setSummary(s.value.data.summary ?? [])
      if (a.status === 'fulfilled') setRecent(a.value.data.items ?? [])
    }).finally(() => setLoading(false))
  }, [])

  const latest = report[report.length - 1]
  const prev   = report[report.length - 2]
  const totalAnomalies = summary.reduce((a, b) => a + b.count, 0)

  // 에너지 믹스 — Grid kWh 역산
  const mixData = report.slice(-12).map(r => {
    const grid = Math.max(0, (r.total_consumption_kwh ?? 0) - (r.pv_kwh ?? 0) - (r.chp_kwh ?? 0))
    return {
      period: r.period,
      PV:   Math.round((r.pv_kwh ?? 0) / 1000),
      CHP:  Math.round((r.chp_kwh ?? 0) / 1000),
      Grid: Math.round(grid / 1000),
    }
  })

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <div>
          <div style={s.title}>대시보드</div>
          <div style={s.sub}>Honda R&D Europe · 독일 오펜바흐 · 실시간 현황</div>
        </div>
        {latest && (
          <div style={{ fontSize: 13, color: '#8b949e' }}>
            최근 보고: <span style={{ color: '#e6edf3', fontWeight: 600 }}>{latest.period}</span>
          </div>
        )}
      </div>

      {loading && (
        <div style={s.body}>
          <div style={s.kpiRow}>
            {[1,2,3,4].map(i => (
              <div key={i} style={s.kpiCard}>
                <div style={sk.bar} />
                <div style={{ ...sk.bar, width: '80%', marginTop: 8 }} />
                <div style={{ ...sk.bar, width: '50%', marginTop: 8 }} />
              </div>
            ))}
          </div>
          <div style={s.chartGrid}>
            {[1,2,3,4].map(i => (
              <div key={i} style={s.chartBox}>
                <div style={{ ...sk.bar, width: '40%', marginBottom: 12 }}/>
                <div style={{ ...sk.bar, height: 200, borderRadius: 8 }}/>
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
              label="누적 이상탐지" value={totalAnomalies.toLocaleString()}
              sub="전체 기간" color="#f85149"
            />
          </div>

          <div style={s.chartGrid}>
            {/* 에너지 소스 믹스 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 에너지 소스 믹스 (MWh)</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={mixData} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}
                    tickFormatter={v => v?.slice(2)}/>
                  <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v}M`}/>
                  <Tooltip {...tt} formatter={(v, n) => [`${v} MWh`, n]}/>
                  <Legend wrapperStyle={{ fontSize: 11 }}/>
                  <Bar dataKey="PV"   stackId="a" fill="#3fb950" name="태양광 (PV)"/>
                  <Bar dataKey="CHP"  stackId="a" fill="#58a6ff" name="열병합 (CHP)"/>
                  <Bar dataKey="Grid" stackId="a" fill="#d29922" name="외부 계통"
                    radius={[3, 3, 0, 0]}/>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* 최근 이상탐지 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>최근 HIGH 이상탐지</div>
              {recent.length === 0
                ? <div style={{ color: '#8b949e', fontSize: 13, paddingTop: 40, textAlign: 'center' }}>탐지된 HIGH 이상 없음</div>
                : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                    {recent.map(item => (
                      <div key={item.id} style={s.anomalyRow}>
                        <span style={{ ...s.sevDot, background: SEV_COLOR[item.severity] }}/>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 12, fontWeight: 600, color: '#e6edf3', display: 'flex', justifyContent: 'space-between' }}>
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type}
                            </span>
                            <span style={{ fontSize: 11, color: '#6e7681', flexShrink: 0, marginLeft: 8 }}>
                              {item.timestamp?.slice(0, 10)}
                            </span>
                          </div>
                          <div style={{ fontSize: 11, color: '#6e7681', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {item.description}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )
              }
            </div>

            {/* 월별 소비량 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 총 소비량 (kWh)</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={report.slice(-12)} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}
                    tickFormatter={v => v?.slice(2)}/>
                  <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${(v/1000).toFixed(0)}k`}/>
                  <Tooltip {...tt} formatter={v => [`${v.toLocaleString()} kWh`]}/>
                  <Bar dataKey="total_consumption_kwh" name="소비량" fill="#1f6feb" radius={[3,3,0,0]}/>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* 이번 달 에너지 현황 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>이번 달 에너지 현황 — {latest.period}</div>
              <div style={{ marginTop: 16 }}>
                <MiniBar
                  label="태양광 (PV)"
                  kwh={latest.pv_kwh ?? 0}
                  total={latest.total_consumption_kwh ?? 1}
                  color="#3fb950"
                />
                <MiniBar
                  label="열병합 (CHP)"
                  kwh={latest.chp_kwh ?? 0}
                  total={latest.total_consumption_kwh ?? 1}
                  color="#58a6ff"
                />
                <MiniBar
                  label="외부 계통 (Grid)"
                  kwh={Math.max(0, (latest.total_consumption_kwh ?? 0) - (latest.pv_kwh ?? 0) - (latest.chp_kwh ?? 0))}
                  total={latest.total_consumption_kwh ?? 1}
                  color="#d29922"
                />
                <div style={{ borderTop: '1px solid #21262d', marginTop: 12, paddingTop: 12, display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span style={{ color: '#8b949e' }}>총 소비량</span>
                  <span style={{ color: '#e6edf3', fontWeight: 600 }}>
                    {((latest.total_consumption_kwh ?? 0) / 1000).toFixed(0)} MWh
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginTop: 6 }}>
                  <span style={{ color: '#8b949e' }}>이상탐지</span>
                  <span style={{ color: (latest.anomaly_count ?? 0) > 50 ? '#f85149' : '#3fb950', fontWeight: 600 }}>
                    {latest.anomaly_count ?? 0}건
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const sk = { bar: { background: '#21262d', borderRadius: 4, height: 14, width: '60%' } }

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:     { padding: '16px 24px 12px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 },
  title:      { fontWeight: 700, fontSize: 16, color: '#e6edf3' },
  sub:        { fontSize: 12, color: '#8b949e', marginTop: 3 },
  body:       { flex: 1, overflowY: 'auto', padding: '20px 24px' },
  kpiRow:     { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 },
  kpiCard:    { background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '16px 18px' },
  chartGrid:  { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 },
  chartBox:   { background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '16px 18px' },
  chartTitle: { fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 12 },
  anomalyRow: { display: 'flex', alignItems: 'flex-start', gap: 8, padding: '7px 0', borderBottom: '1px solid #21262d' },
  sevDot:     { width: 8, height: 8, borderRadius: '50%', flexShrink: 0, marginTop: 3 },
}
