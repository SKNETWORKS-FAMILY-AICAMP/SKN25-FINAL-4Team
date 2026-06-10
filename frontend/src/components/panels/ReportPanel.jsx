import { useState, useEffect, useMemo } from 'react'
import { FileText } from 'lucide-react'
import {
  LineChart, Line, BarChart, Bar, ScatterChart, Scatter,
  XAxis, YAxis, ZAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
  ReferenceLine,
} from 'recharts'
import { getReport, getBalanceReport, getEnergyIntensity, monthlyDownloadUrl } from '../../api/client'

const tooltip = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: 'var(--text)' },
}

// ── 집계 유틸 ──────────────────────────────────────────────────────
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
function AiSection({ color, title, text, loading, onGenerate, compact = false }) {
  const box   = compact ? s.aiBoxCompact : s.aiBox
  const titleStyle = { ...s.aiTitle, color }
  if (text) {
    return (
      <div style={{ ...box, borderColor: color + '44' }}>
        <div style={titleStyle}>{title}</div>
        <div style={compact ? s.aiTextCompact : s.aiText}>{text}</div>
      </div>
    )
  }
  return (
    <div style={{ ...box, borderColor: color + '22', borderStyle: 'dashed' }}>
      <div style={titleStyle}>{title}</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 11, color: 'var(--text4)', fontStyle: 'italic' }}>
          데이터·차트는 아래 자동 표시됩니다. AI 분석문이 필요하면 우측 버튼을 눌러주세요. (LLM 1회 호출)
        </span>
        <button onClick={onGenerate} disabled={loading}
          style={{ padding: '5px 12px', background: 'transparent', border: `1px solid ${color}66`,
                   borderRadius: 4, color, fontSize: 11, fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}>
          {loading ? '생성 중...' : '🪄 AI 분석 생성'}
        </button>
      </div>
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

function KpiCard({ label, value, unit, color = '#2563eb', yoy, yoyUnit }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 22, fontWeight: 700, color }}>{value ?? '–'}</span>
        <YoyBadge pct={yoy} unit={yoyUnit} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{unit}</div>
      <div style={{ fontSize: 12, color: 'var(--text4)', marginTop: 4 }}>{label}</div>
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

export default function ReportPanel() {
  const [raw,             setRaw]             = useState([])
  const [coolingVsTemp,   setCoolingVsTemp]   = useState([])
  const [trendNarrative,  setTrendNarrative]  = useState('')
  const [balance,         setBalance]         = useState(null)
  const [eiData,          setEiData]          = useState(null)
  const [loadingAi,       setLoadingAi]       = useState({ trend: false, balance: false, ei: false })
  const [months,          setMonths]          = useState(72)
  const [view,            setView]            = useState('monthly')
  const [loading,         setLoading]         = useState(true)
  const [error,           setError]           = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      getReport(months),
      getBalanceReport(Math.max(months, 24)),
      getEnergyIntensity(Math.max(months, 24)),
    ]).then(([r, b, ei]) => {
      if (r.status === 'fulfilled') {
        if (r.value.data.error) throw new Error(r.value.data.error)
        setRaw(r.value.data.items ?? [])   // 백엔드가 이미 오래된→최신 순으로 응답
        setCoolingVsTemp(r.value.data.cooling_vs_temp ?? [])
        setTrendNarrative(r.value.data.trend_narrative ?? '')
      }
      if (b.status  === 'fulfilled') setBalance(b.value.data)
      if (ei.status === 'fulfilled') setEiData(ei.value.data)
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

  // 전월 대비 변화 (monthly 뷰, 최근 12개월)
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

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.header}>
        <span style={s.title}><FileText size={17} color="#0d9488" /> 보고서</span>
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
              CSV
            </button>
          )}
          {raw.length > 0 && (
            <>
              <button style={s.docBtn} title="월간 보고서 PDF (차트·비용·CO₂ 포함)"
                onClick={() => window.open(monthlyDownloadUrl(months, 'pdf'), '_blank')}>
                📄 PDF
              </button>
              <button style={s.docBtn} title="월간 보고서 Word"
                onClick={() => window.open(monthlyDownloadUrl(months, 'docx'), '_blank')}>
                📝 DOCX
              </button>
            </>
          )}
        </div>
      </div>

      {/* 에러 */}
      {!loading && error && (
        <div style={{ margin: '24px', padding: '14px 18px', background: '#fee2e2', border: '1px solid #f85149', borderRadius: 10, color: '#f85149', fontSize: 13 }}>
          데이터 로드 실패: {error}
          <button onClick={() => { setError(''); setLoading(true); getReport(months).then(r => { setRaw(r.data.items ?? []); setCoolingVsTemp(r.data.cooling_vs_temp ?? []) }).catch(e => setError(e.message ?? '')).finally(() => setLoading(false)) }}
            style={{ marginLeft: 12, padding: '3px 10px', background: 'none', border: '1px solid #f85149', borderRadius: 6, color: '#f85149', cursor: 'pointer', fontSize: 12 }}>
            재시도
          </button>
        </div>
      )}

      {/* 로딩 상태 */}
      {loading && (
        <div style={s.body}>
          <div style={s.loadingBanner}>
            <div style={s.loadingSpinner} />
            <div>
              <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 600 }}>📊 보고서 데이터 집계 중…</div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                월간 KPI · 데이터 품질 · 외기온 정규화 EI를 동시에 조회하고 있습니다 (수 초 소요)
              </div>
            </div>
          </div>
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

          {/* AI 트렌드 narrative */}
          <AiSection
            color="#a371f7"
            title={`🤖 AI 트렌드 분석 — ${latest.period}`}
            text={trendNarrative}
            loading={loadingAi.trend}
            onGenerate={async () => {
              setLoadingAi(p => ({ ...p, trend: true }))
              try {
                const r = await getReport(months, false)
                if (r?.data) setTrendNarrative(r.data.trend_narrative ?? '')
              } finally { setLoadingAi(p => ({ ...p, trend: false })) }
            }}
          />

          {/* KPI 카드 — 최신 기간 기준 */}
          <div style={{ fontSize: 11, color: 'var(--text4)', marginBottom: 8, marginTop: 12 }}>
            최신: <b style={{ color: 'var(--text)' }}>{latest.period}</b> 기준
          </div>
          <div style={s.kpiRow}>
            <KpiCard label="자급률"        value={`${latest.self_sufficiency_pct?.toFixed(1)}%`} unit="Self-Sufficiency" color="#3fb950"
              yoy={latest.yoy_self_pct} yoyUnit="%p"/>
            <KpiCard label="평균 COP"      value={latest.avg_cop?.toFixed(2)}                    unit="성능계수"         color="#2563eb"/>
            <KpiCard label="그리드 의존도"  value={`${latest.grid_dependency_pct?.toFixed(1)}%`}  unit="Grid Dependency" color="#d29922"/>
            <KpiCard label="이상탐지"      value={latest.anomaly_count}                          unit="건"              color="#f85149"/>
          </div>

          {/* 트렌드 차트 */}
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
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                    interval={Math.max(0, Math.floor(items.length / 8) - 1)}
                    tickFormatter={v => view === 'monthly' ? v?.slice(2) : v}/>
                  <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltip}/>
                  <Line type="monotone" dataKey="avg_cop" name="COP" stroke="#2563eb" strokeWidth={2} dot={items.length < 30}/>
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
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}/>
                  <YAxis yAxisId="left" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v.toFixed(0)}%`}/>
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${(v/1000).toFixed(0)}k`}/>
                  <Tooltip {...tooltip} formatter={(v, n) => n === '자급률' ? [`${v.toFixed(1)}%`, n] : [`${v.toLocaleString()} kWh`, n]}/>
                  <Legend wrapperStyle={{ fontSize: 11 }}/>
                  <Bar yAxisId="left"  dataKey="self_sufficiency_pct"  name="자급률"  fill="#3fb950" radius={[3,3,0,0]} opacity={0.85}/>
                  <Bar yAxisId="right" dataKey="total_consumption_kwh" name="소비량"  fill="#2563eb" radius={[3,3,0,0]} opacity={0.6}/>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 냉방 부하 vs 외기온 상관관계 */}
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
                  <Tooltip {...tooltip} cursor={{ strokeDasharray: '3 3' }}
                    content={({ payload }) => {
                      if (!payload?.length) return null
                      const d = payload[0].payload
                      return (
                        <div style={{ ...tooltip.contentStyle, padding: '8px 12px' }}>
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

          {/* 전월 대비 변화 (월별 뷰) */}
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
                  <Tooltip {...tooltip}
                    formatter={(v, n) => v != null ? [`${v > 0 ? '+' : ''}${v.toFixed(1)}%`, n] : ['–', n]}/>
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
                          : <span style={{ color: 'var(--text)' }}>{row.period}</span>
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

          {/* 외기온 정규화 에너지 원단위 (EI) */}
          {eiData && (eiData.items ?? []).some(it => it.ei_total != null) && (
            <div style={s.eiBox}>
              <div style={s.eiHeader}>
                <span style={s.eiTitle}>🌡️ 외기온 정규화 에너지 원단위 (EI)</span>
                {eiData.ei_avg != null && (
                  <span style={s.eiAvgBadge}>전체 평균 {eiData.ei_avg} kWh/DD</span>
                )}
              </div>
              <div style={s.eiDesc}>
                날씨 영향을 제거한 실질 효율 지표 — 낮을수록 에너지 효율이 높음 (DD = Degree Days, 기준온도 18/22°C)
              </div>
              <AiSection
                color="#d29922"
                title="🤖 AI EI 분석"
                text={eiData.narrative}
                loading={loadingAi.ei}
                compact
                onGenerate={async () => {
                  setLoadingAi(p => ({ ...p, ei: true }))
                  try {
                    const r = await getEnergyIntensity(Math.max(months, 24), false)
                    if (r?.data) setEiData(r.data)
                  } finally { setLoadingAi(p => ({ ...p, ei: false })) }
                }}
              />
              <ResponsiveContainer width="100%" height={200}>
                <LineChart
                  data={(eiData.items ?? []).filter(it => it.ei_total != null)}
                  margin={{ top: 8, right: 16, left: -10, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                  <XAxis dataKey="period" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                    tickFormatter={v => v?.slice(2)}
                    interval={Math.floor((eiData.items?.filter(it => it.ei_total != null).length ?? 0) / 10)}/>
                  <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v}`}/>
                  <Tooltip
                    contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: 'var(--text)' }}
                    formatter={(v, name) => [
                      `${v} kWh/DD`,
                      name === 'ei_total' ? '정규화 EI' : name,
                    ]}
                  />
                  {eiData.ei_avg != null && (
                    <ReferenceLine y={eiData.ei_avg} stroke="#2563eb44" strokeDasharray="6 3"
                      label={{ value: `평균 ${eiData.ei_avg}`, fill: '#2563eb', fontSize: 10, position: 'right' }}/>
                  )}
                  <Line type="monotone" dataKey="ei_total" name="ei_total"
                    stroke="#d29922" strokeWidth={2} dot={false} connectNulls/>
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 데이터 품질 / 밸런스 검증 */}
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

              <AiSection
                color="#2563eb"
                title="🤖 AI 품질 분석"
                text={balance.narrative}
                loading={loadingAi.balance}
                compact
                onGenerate={async () => {
                  setLoadingAi(p => ({ ...p, balance: true }))
                  try {
                    const r = await getBalanceReport(Math.max(months, 24), false)
                    if (r?.data) setBalance(r.data)
                  } finally { setLoadingAi(p => ({ ...p, balance: false })) }
                }}
              />

              {/* 월별 품질 히트맵 */}
              <div style={s.qualityGrid}>
                {(balance.items ?? []).map(it => {
                  const score = it.quality_score ?? 100
                  const color = score >= 80 ? '#3fb950' : score >= 50 ? '#d29922' : '#f85149'
                  const hasFaults = (it.balance_flags ?? []).length > 0
                  return (
                    <div key={it.period} title={hasFaults ? it.balance_flags.join(', ') : '정상'}
                      style={{ ...s.qualityCell, background: color + '22', border: `1px solid ${color}55`, color }}>
                      <div style={{ fontSize: 9 }}>{it.period.slice(2)}</div>
                      <div style={{ fontSize: 11, fontWeight: 700 }}>{score}</div>
                    </div>
                  )
                })}
              </div>
              <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 6 }}>
                셀에 마우스를 올리면 품질 이슈 상세 확인 · 게이트웨이 장애 구간은 자동 표시
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const sk = { background: 'var(--line)', borderRadius: 4, height: 14, width: '60%' }

const s = {
  wrap:      { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:    { padding: '14px 20px 10px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' },
  title:     { fontWeight: 600, fontSize: 15, color: 'var(--text)', display: 'inline-flex', alignItems: 'center', gap: 8 },
  empty:     { textAlign: 'center', color: 'var(--text3)', paddingTop: 60 },
  body:      { flex: 1, overflowY: 'auto', padding: '16px 20px 24px', display: 'flex', flexDirection: 'column', gap: 14 },
  kpiRow:    { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10 },
  kpiCard:   { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, padding: '12px 16px' },
  charts:    { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  chartBox:  { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, padding: '14px 16px' },
  chartTitle:{ fontSize: 12, fontWeight: 600, color: 'var(--text3)', marginBottom: 10 },
  tableWrap: { overflowX: 'auto' },
  table:     { width: '100%', borderCollapse: 'collapse' },
  th:        { textAlign: 'left', padding: '8px 12px', fontSize: 12, color: 'var(--text3)', borderBottom: '1px solid var(--line)', fontWeight: 500, whiteSpace: 'nowrap' },
  tr:        { borderBottom: '1px solid var(--surface)' },
  td:        { padding: '9px 12px', fontSize: 13, color: 'var(--text3)', verticalAlign: 'middle' },
  segGroup:  { display: 'flex', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' },
  seg:       { padding: '4px 12px', background: 'none', border: 'none', color: 'var(--text3)', fontSize: 12, cursor: 'pointer' },
  segActive: { background: '#2563eb33', color: '#2563eb', fontWeight: 600 },
  csvBtn:       { padding: '5px 12px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', fontWeight: 500 },
  docBtn:       { padding: '5px 12px', background: 'var(--surface)', border: '1px solid var(--brand)', borderRadius: 6, color: 'var(--brand)', fontSize: 12, cursor: 'pointer', fontWeight: 600 },
  loadingBanner:   { display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px', background: 'linear-gradient(135deg, #2563eb15, var(--surface))', border: '1px solid #2563eb44', borderRadius: 10, marginBottom: 12 },
  loadingSpinner:  { width: 22, height: 22, borderRadius: '50%', border: '2px solid var(--border)', borderTopColor: '#2563eb', animation: 'spin 0.8s linear infinite', flexShrink: 0 },

  narrativeBox:    { background: 'linear-gradient(135deg, #a371f710, var(--surface))', border: '1px solid #a371f744', borderRadius: 10, padding: '14px 18px', marginBottom: 4 },
  narrativeTitle:  { fontSize: 13, fontWeight: 700, color: '#a371f7', marginBottom: 8 },
  narrativeText:   { fontSize: 13, color: 'var(--text2)', lineHeight: 1.75, whiteSpace: 'pre-wrap' },

  // AI 섹션 (생성 버튼 또는 결과)
  aiBox:           { background: 'linear-gradient(135deg, rgba(163,113,247,0.05), var(--surface))', border: '1px solid', borderRadius: 10, padding: '12px 16px', marginBottom: 4 },
  aiBoxCompact:    { background: 'rgba(255,255,255,0.02)', border: '1px solid', borderRadius: 8, padding: '10px 12px', marginBottom: 8 },
  aiTitle:         { fontSize: 12, fontWeight: 700, marginBottom: 6 },
  aiText:          { fontSize: 13, color: 'var(--text2)', lineHeight: 1.75, whiteSpace: 'pre-wrap' },
  aiTextCompact:   { fontSize: 12, color: 'var(--text2)', lineHeight: 1.65, whiteSpace: 'pre-wrap' },

  // EI 섹션
  eiBox:       { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px' },
  eiHeader:    { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6, flexWrap: 'wrap', gap: 8 },
  eiTitle:     { fontSize: 13, fontWeight: 700, color: '#d29922' },
  eiAvgBadge:  { fontSize: 11, color: '#2563eb', background: '#2563eb18', borderRadius: 4, padding: '2px 8px' },
  eiDesc:      { fontSize: 11, color: 'var(--text4)', marginBottom: 8 },
  eiNarrative: { fontSize: 12, color: 'var(--text3)', lineHeight: 1.7, marginBottom: 12, whiteSpace: 'pre-wrap',
                 borderLeft: '3px solid #d2992244', paddingLeft: 10 },

  // 데이터 품질 섹션
  balanceBox:      { background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px' },
  balanceHeader:   { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexWrap: 'wrap', gap: 8 },
  balanceTitle:    { fontSize: 13, fontWeight: 700, color: '#2563eb' },
  balanceSummary:  { fontSize: 12 },
  balanceNarrative:{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.7, marginBottom: 12, whiteSpace: 'pre-wrap' },
  qualityGrid:     { display: 'flex', flexWrap: 'wrap', gap: 4 },
  qualityCell:     { borderRadius: 4, padding: '4px 6px', textAlign: 'center', minWidth: 36, cursor: 'default' },
}
