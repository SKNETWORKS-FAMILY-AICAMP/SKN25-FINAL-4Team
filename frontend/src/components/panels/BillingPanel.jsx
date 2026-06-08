import { useEffect, useState } from 'react'
import { Wallet } from 'lucide-react'
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from 'recharts'
import { getBilling } from '../../api/client'

const fmtEur = v => (v == null) ? '–' : '€ ' + Math.round(v).toLocaleString('de-DE')
const fmtKrw = (eur, rate) => (eur == null) ? '' : '≈ ₩ ' + Math.round(eur * rate).toLocaleString('ko-KR')

const STATUS_COLOR = { '정상': '#3fb950', '주의': '#d29922', '초과 위험': '#f85149' }

const tt = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 11, padding: '6px 10px' },
  labelStyle: { color: 'var(--text)', marginBottom: 4 },
  itemStyle: { color: 'var(--text3)', padding: 0 },
}

export default function BillingPanel() {
  const [data,    setData]    = useState(null)
  const [error,   setError]   = useState('')
  const [loading, setLoading] = useState(true)

  // 사용자가 조정 가능한 값 (시연 시 SettingsPanel에서 연동 예정)
  const [unitPrice, setUnitPrice] = useState(0.20)
  const [target,    setTarget]    = useState(50000)

  const load = () => {
    setLoading(true); setError('')
    getBilling(null, unitPrice, null, target)
      .then(r => {
        if (r.data.error) throw new Error(r.data.error)
        setData(r.data)
      })
      .catch(e => setError(e.message ?? '로드 실패'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])   // 초기 로드만 자동

  const statusColor = data ? (STATUS_COLOR[data.status] ?? 'var(--text3)') : 'var(--text3)'
  const overPct     = data && data.target_eur ? (data.projected_eur - data.target_eur) / data.target_eur * 100 : 0

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <div>
          <div style={s.title}><Wallet size={18} color="#0d9488" /> 목표 요금 관리</div>
          <div style={s.sub}>AI 예측 모델 기반 월말 요금 추정 · 피크 전력 위험 모니터링</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--text4)' }}>단가</span>
          <input type="number" step="0.01" value={unitPrice} onChange={e => setUnitPrice(parseFloat(e.target.value) || 0)} style={s.input}/>
          <span style={{ fontSize: 11, color: 'var(--text4)' }}>€/kWh</span>
          <span style={{ marginLeft: 12, fontSize: 11, color: 'var(--text4)' }}>목표</span>
          <input type="number" step="1000" value={target} onChange={e => setTarget(parseFloat(e.target.value) || 0)} style={{ ...s.input, width: 80 }}/>
          <span style={{ fontSize: 11, color: 'var(--text4)' }}>€</span>
          <button onClick={load} style={s.applyBtn}>적용</button>
        </div>
      </div>

      {loading && <div style={s.loading}>비용 분석 중...</div>}

      {!loading && error && (
        <div style={s.errorBox}>
          {error}
          <button onClick={load} style={s.retryBtn}>다시 시도</button>
        </div>
      )}

      {!loading && data && (
        <div style={s.body}>

          {/* 상단 KPI 카드 4개 */}
          <div style={s.kpiRow}>
            <KpiCard label="이번 달 목표 요금" value={fmtEur(data.target_eur)} sub={fmtKrw(data.target_eur, data.krw_rate)} color="var(--text)"/>
            <KpiCard label="현재까지 누적" value={fmtEur(data.actual_eur)} sub={`${data.actual_kwh.toLocaleString()} kWh · ${data.days_elapsed}/${data.days_in_month}일`} color="#3fb950"/>
            <KpiCard
              label="AI 예상 최종 요금"
              value={fmtEur(data.projected_eur)}
              sub={`${overPct >= 0 ? '▲' : '▼'} ${Math.abs(overPct).toFixed(1)}% vs 목표`}
              color={statusColor}
              highlight
            />
            <KpiCard label="상태" value={data.status} sub={`피크 위험 ${data.peak_risk_pct}%`} color={statusColor}/>
          </div>

          {/* AI 분석 */}
          {data.narrative && (
            <div style={s.narrativeBox}>
              <div style={s.narrativeTitle}>🤖 AI 비용 분석 · 권고</div>
              <div style={s.narrativeText}>{data.narrative}</div>
            </div>
          )}

          {/* 2-컬럼: 일별 비용 차트 + 일별 피크 차트 */}
          <div style={s.chartsRow}>
            <div style={s.chartBox}>
              <div style={s.chartTitle}>일별 누적 비용 추이 (€)</div>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart
                  data={data.daily.map((d, i, arr) => ({
                    date: d.date.slice(5),
                    cost: arr.slice(0, i + 1).reduce((sum, x) => sum + x.cost_eur, 0),
                  }))}
                  margin={{ top: 8, right: 12, left: -10, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false}/>
                  <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${(v / 1000).toFixed(0)}k`}/>
                  <Tooltip {...tt} formatter={v => [`€ ${Math.round(v).toLocaleString()}`, '누적']}/>
                  <ReferenceLine y={data.target_eur} stroke="#f85149" strokeDasharray="5 3"
                    label={{ value: `목표 €${(data.target_eur / 1000).toFixed(0)}k`, fill: '#f85149', fontSize: 10, position: 'insideTopRight' }}/>
                  <Bar dataKey="cost" fill="#2563eb" radius={[2, 2, 0, 0]}/>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div style={s.chartBox}>
              <div style={s.chartTitle}>일별 피크 전력 (kW) · 계약전력 기준선</div>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart
                  data={data.daily.map(d => ({ date: d.date.slice(5), peak: d.peak_kw }))}
                  margin={{ top: 8, right: 12, left: -10, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false}/>
                  <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v}`}/>
                  <Tooltip {...tt} formatter={v => [`${Math.round(v)} kW`, '피크']}/>
                  <ReferenceLine y={data.max_peak_kw} stroke="#d29922" strokeDasharray="5 3"
                    label={{ value: `최대 ${Math.round(data.max_peak_kw)}kW`, fill: '#d29922', fontSize: 10, position: 'insideTopRight' }}/>
                  <Line type="monotone" dataKey="peak" stroke="#d29922" strokeWidth={2}
                    dot={{ fill: '#d29922', r: 2 }} activeDot={{ r: 4 }}/>
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* 시연 안내 */}
          <div style={s.infoBox}>
            <span style={{ fontSize: 11, color: 'var(--text4)' }}>
              💡 현재 독일 산업용 전기요금({unitPrice} €/kWh) 기준 ·
              한국 적용 시 <strong style={{ color: '#2563eb' }}>⚙️ 시스템 설정</strong>에서 요금표 교체 가능
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

function KpiCard({ label, value, sub, color, highlight }) {
  return (
    <div style={{ ...s.kpiCard, ...(highlight ? s.kpiHighlight : {}) }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 4 }}>{sub}</div>
    </div>
  )
}

const s = {
  wrap:        { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header:      { padding: '14px 24px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)', gap: 16, flexWrap: 'wrap' },
  title:       { fontWeight: 700, fontSize: 16, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8 },
  sub:         { fontSize: 11, color: 'var(--text3)', marginTop: 3 },
  input:       { width: 64, padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 4, fontSize: 12, textAlign: 'right' },
  applyBtn:    { padding: '5px 12px', background: '#2563eb', border: 'none', borderRadius: 4, color: '#fff', fontSize: 11, fontWeight: 600, cursor: 'pointer', marginLeft: 4 },

  loading:     { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)' },
  errorBox:    { margin: 20, padding: '14px 18px', background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, color: '#f85149', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 },
  retryBtn:    { padding: '3px 10px', background: 'none', border: '1px solid #f85149', borderRadius: 4, color: '#f85149', cursor: 'pointer', fontSize: 11 },

  body:        { flex: 1, overflowY: 'auto', padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 14 },
  kpiRow:      { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 },
  kpiCard:     { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 18px' },
  kpiHighlight:{ background: 'linear-gradient(135deg, #2563eb14, var(--surface))', border: '1px solid #2563eb55', boxShadow: '0 0 16px rgba(31, 111, 235, 0.1)' },

  narrativeBox:  { background: 'linear-gradient(135deg, #a371f70a, var(--surface))', border: '1px solid #a371f733', borderRadius: 10, padding: '14px 18px' },
  narrativeTitle:{ fontSize: 12, fontWeight: 700, color: '#a371f7', marginBottom: 6 },
  narrativeText: { fontSize: 13, color: 'var(--text2)', lineHeight: 1.7, whiteSpace: 'pre-wrap' },

  chartsRow:   { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 },
  chartBox:    { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '12px 14px' },
  chartTitle:  { fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 8 },

  infoBox:     { padding: '10px 14px', background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, textAlign: 'center' },
}
