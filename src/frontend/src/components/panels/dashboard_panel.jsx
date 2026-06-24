import { useState, useEffect, useRef } from 'react'
import { TYPE_LABEL, SEV_COLOR } from '../../constants/anomaly'
import { Factory, Wrench, TrendingUp, Bot, FileText, FileEdit, CheckCircle2, Clock, Wand2, RefreshCw } from 'lucide-react'
import { EquipmentIcon } from '../common/equipment_icon'
import {
  BarChart, Bar, LineChart, Line, AreaChart, Area, ComposedChart,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, ReferenceLine,
} from 'recharts'
import {
  getReport, getAnomalySummary, getAnomalies, getTotalDemandForecast,
  getDailyReport, getLatestDataDate, dailyDownloadUrl,
  getBilling, getEquipmentStatus, getWorkOrderStats, getSettings,
} from '../../api/client'



const tt = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 11, padding: '6px 10px' },
  labelStyle: { color: 'var(--text)', marginBottom: 4 },
  itemStyle: { color: 'var(--text3)', padding: 0 },
}

function KpiCard({ label, value, unit, sub, color = '#2563eb', trend, gaugePct, sparkline }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 500 }}>{label}</div>
        {trend !== undefined && (
          <div style={{ fontSize: 10, color: trend >= 0 ? '#3fb950' : '#f85149', fontWeight: 700, padding: '1px 6px', background: (trend >= 0 ? '#3fb950' : '#f85149') + '22', borderRadius: 3 }}>
            {trend >= 0 ? '▲' : '▼'} {Math.abs(trend).toFixed(1)}%p
          </div>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 8 }}>
        <span style={{ fontSize: 32, fontWeight: 700, color, lineHeight: 1, letterSpacing: -1 }}>{value ?? '–'}</span>
        {unit && <span style={{ fontSize: 14, color: 'var(--text3)', fontWeight: 500 }}>{unit}</span>}
      </div>

      {gaugePct != null && <MiniGauge pct={gaugePct} color={color} />}
      {sparkline && sparkline.length > 1 && (
        <div style={{ marginTop: 8, height: 28 }}>
          <ResponsiveContainer width="100%" height={28}>
            <LineChart data={sparkline.map((y, x) => ({ x, y }))}>
              <Line type="monotone" dataKey="y" stroke={color} strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: gaugePct != null || sparkline ? 4 : 8 }}>{sub}</div>
    </div>
  )
}

function BillingMiniCard({ billing }) {
  if (!billing) {
    return (
      <div style={s.kpiCard}>
        <div style={{ fontSize: 11, color: 'var(--text3)' }}>금월 전기 비용</div>
        <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text4)', marginTop: 8 }}>–</div>
        <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 8 }}>데이터 로딩 중</div>
      </div>
    )
  }
  const overPct = billing.target_eur > 0 ? (billing.projected_eur - billing.target_eur) / billing.target_eur * 100 : 0
  const usedPct = billing.target_eur > 0 ? Math.min(100, billing.actual_eur / billing.target_eur * 100) : 0
  const statusColor = billing.status === '정상' ? '#3fb950' : billing.status === '주의' ? '#d29922' : '#f85149'

  return (
    <div style={s.kpiCard}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 500 }}>
          {billing.period} 전기 비용
        </div>
        <div style={{ fontSize: 10, color: statusColor, fontWeight: 700, padding: '1px 6px', background: statusColor + '22', borderRadius: 3 }}>
          {billing.status}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 8 }}>
        <span style={{ fontSize: 32, fontWeight: 700, color: 'var(--text)', lineHeight: 1, letterSpacing: -1 }}>
          € {Math.round(billing.actual_eur).toLocaleString()}
        </span>
      </div>

      <div style={{ marginTop: 8, height: 4, background: 'var(--line)', borderRadius: 2, overflow: 'hidden', position: 'relative' }}>
        <div style={{ width: `${usedPct}%`, height: '100%', background: '#3fb950', transition: 'width 0.5s' }} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text4)', marginTop: 4 }}>
        <span>예상 €{Math.round(billing.projected_eur).toLocaleString()}</span>
        <span style={{ color: overPct > 0 ? '#f85149' : '#3fb950', fontWeight: 600 }}>
          {overPct >= 0 ? '+' : ''}{overPct.toFixed(1)}% 목표
        </span>
      </div>
    </div>
  )
}

function MiniGauge({ pct, color }) {
  const p = Math.max(0, Math.min(100, pct ?? 0))
  return (
    <div style={{ marginTop: 8, height: 4, background: 'var(--line)', borderRadius: 2, overflow: 'hidden' }}>
      <div style={{ width: `${p}%`, height: '100%', background: color, transition: 'width 0.5s ease' }} />
    </div>
  )
}

const skBase = { background: 'var(--line)', borderRadius: 6, animation: 'skPulse 1.5s ease-in-out infinite' }

function Sk({ h = 12, w = '100%', mt = 0 }) {
  return <div style={{ ...skBase, height: h, width: w, marginTop: mt, flexShrink: 0 }} />
}

function DashboardSkeleton() {
  return (
    <div style={s.body}>
      <div style={s.cmsRow}>
        <div style={{ ...s.cmsCard, flex: 3 }}>
          <Sk h={12} w={120} />
          <div style={{ display: 'flex', gap: 18, marginTop: 12 }}>
            {[0,1,2,3].map(i => <Sk key={i} h={44} w={80} />)}
          </div>
        </div>
        <div style={{ ...s.cmsCard, flex: 1 }}>
          <Sk h={12} w={80} />
          <Sk h={36} w={60} mt={10} />
          <Sk h={8} w={120} mt={8} />
        </div>
      </div>
      <div style={s.row1}>
        {[0,1,2].map(i => (
          <div key={i} style={s.kpiCard}>
            <Sk h={10} w={80} />
            <Sk h={36} w={100} mt={10} />
            <Sk h={4} mt={10} />
            <Sk h={8} w="60%" mt={6} />
          </div>
        ))}
      </div>
      <div style={s.briefingCard}>
        <div style={s.briefingHeader}><Sk h={14} w={160} /></div>
        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Sk h={11} /><Sk h={11} w="85%" /><Sk h={11} w="90%" />
        </div>
      </div>
      <div style={s.row2}>
        {[0,1].map(i => (
          <div key={i} style={s.chartBox}>
            <Sk h={12} w={180} />
            <div style={{ flex: 1, marginTop: 8, ...skBase }} />
          </div>
        ))}
      </div>
      <div style={s.row3}>
        {[0,1].map(i => (
          <div key={i} style={s.chartBox}>
            <Sk h={12} w={140} />
            <div style={{ flex: 1, marginTop: 8, ...skBase }} />
          </div>
        ))}
      </div>
    </div>
  )
}

function BriefingCard({ briefing, onRegenerate }) {
  const [regenerating, setRegenerating] = useState(false)

  const hourly = (briefing?.hourly_profile ?? []).map(h => ({
    ...h, hourLabel: String(h.hour).padStart(2, '0'),
  }))
  const hasAi = !!(briefing?.ai_summary || briefing?.today_actions)

  const handleDownload = (fmt) => {
    if (!briefing?.date) return
    const a = document.createElement('a')
    a.href = dailyDownloadUrl(briefing.date, fmt)
    a.target = '_blank'
    a.rel = 'noopener'
    document.body.appendChild(a); a.click(); a.remove()
  }

  const handleRegen = async () => {
    if (regenerating) return
    setRegenerating(true)
    try { await onRegenerate?.() } finally { setRegenerating(false) }
  }

  return (
    <div style={s.briefingCard}>
      <div style={s.briefingHeader}>
        <Bot size={14}/>
        <span style={{ fontSize: 13, fontWeight: 700, color: '#2563eb' }}>AI 운영 브리핑</span>
        <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 8 }}>{briefing?.date || '오늘'}</span>
        {briefing?.anomaly_count > 0 && (
          <span style={{ fontSize: 10, color: '#f85149', background: '#f8514922', padding: '2px 8px', borderRadius: 4, marginLeft: 8, fontWeight: 600 }}>
            이상 {briefing.anomaly_count}건
          </span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          {briefing?.date && !hasAi && (
            <button style={{ ...s.dlBtn, color: '#a371f7', borderColor: '#a371f755' }}
              onClick={handleRegen} disabled={regenerating} title="LLM으로 AI 요약·할 일 생성 (1회 호출)">
              {regenerating ? '생성 중...' : <><Wand2 size={11} style={{verticalAlign:'-1px',marginRight:4}}/>AI 요약 생성</>}
            </button>
          )}
          {briefing?.date && (
            <>
              <span style={{ fontSize: 10, color: 'var(--text4)', marginRight: 4 }}>일일 보고서 다운로드:</span>
              <button style={s.dlBtn} onClick={() => handleDownload('pdf')}  title={`${briefing.date} 일일 보고서 PDF로 다운로드`}><FileText size={11}/>PDF</button>
              <button style={s.dlBtn} onClick={() => handleDownload('docx')} title={`${briefing.date} 일일 보고서 Word로 다운로드`}><FileEdit size={11}/>DOCX</button>
              <button style={s.dlBtn} onClick={() => handleDownload('hwpx')} title={`${briefing.date} 일일 보고서 한글(HWPX)로 다운로드`}><FileText size={11}/>HWPX</button>
            </>
          )}
        </div>
      </div>

      {!briefing && (
        <div style={{ padding: '16px 14px', color: 'var(--text3)', fontSize: 12 }}>
          오늘의 브리핑이 아직 생성되지 않았습니다.
        </div>
      )}

      {briefing && (
        <div style={s.briefingGrid}>
          {/* 좌측: 텍스트 (요약 + 할 일) */}
          <div style={s.briefingText}>
            {briefing.ai_summary
              ? <div style={s.briefingSummary}>{briefing.ai_summary}</div>
              : <div style={{ fontSize: 12, color: 'var(--text4)', fontStyle: 'italic', padding: '4px 0' }}>
                  KPI/프로파일 데이터는 자동 수집되었습니다.
                  위 "AI 요약 생성" 버튼을 눌러 AI 요약과 권고사항을 생성할 수 있습니다.
                </div>
            }
            {briefing.today_actions && (
              <div style={s.actionsBlock}>
                <div style={{...s.actionsBlockTitle, display:'flex', alignItems:'center', gap:4}}><CheckCircle2 size={11}/>오늘 할 일</div>
                <div style={s.actionsBlockText}>{briefing.today_actions}</div>
              </div>
            )}
          </div>

          {/* 우측: 시간대별 프로파일 */}
          <div style={s.briefingChart}>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>
              <Clock size={11} style={{verticalAlign:'-1px',marginRight:4}}/>시간대별 전력 프로파일 · COP
            </div>
            {hourly.length === 0 ? (
              <div style={{ color: 'var(--text4)', fontSize: 11, textAlign: 'center', padding: '40px 0' }}>
                프로파일 데이터 없음
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={168}>
                <ComposedChart data={hourly} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false} />
                  <XAxis dataKey="hourLabel" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false}
                    interval={2} />
                  <YAxis yAxisId="kw" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false} />
                  <YAxis yAxisId="cop" orientation="right" tick={{ fontSize: 9, fill: '#a371f7' }} tickLine={false} axisLine={false} domain={[0, 'auto']} />
                  <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 11, padding: '6px 10px' }}
                    labelStyle={{ color: 'var(--text)', marginBottom: 4 }} itemStyle={{ color: 'var(--text3)', padding: 0 }} />
                  {briefing.peak_hour != null && (
                    <ReferenceLine yAxisId="kw" x={String(briefing.peak_hour).padStart(2, '0')}
                      stroke="#f85149" strokeDasharray="4 2" />
                  )}
                  <Bar yAxisId="kw" dataKey="grid_kw" stackId="a" name="계통"   fill="#d29922" />
                  <Bar yAxisId="kw" dataKey="pv_kw"   stackId="a" name="태양광" fill="#3fb950" />
                  <Bar yAxisId="kw" dataKey="chp_kw"  stackId="a" name="CHP"   fill="#2563eb" radius={[2, 2, 0, 0]} />
                  <Line yAxisId="cop" type="monotone" dataKey="cop" name="COP" stroke="#a371f7" strokeWidth={2} dot={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

const EQ_STATUS_COLOR = { 정상: '#3fb950', 주의: '#d29922', 경고: '#f85149' }

function CmsSummary({ equipment, woStats, onNavigate }) {
  const openWO = (woStats?.open ?? 0) + (woStats?.in_progress ?? 0)
  const worst  = [...(equipment ?? [])].sort((a, b) => a.health_score - b.health_score)[0]
  return (
    <div style={s.cmsRow}>
      {/* 설비 헬스 요약 */}
      <div style={{ ...s.cmsCard, flex: 3, cursor: 'pointer' }} onClick={() => onNavigate?.('equipment')}>
        <div style={s.cmsHead}>
          <span style={s.cmsTitle}><Factory size={15} color="#0d9488" /> 설비 상태</span>
          <span style={s.cmsLink}>설비 상태 감시 →</span>
        </div>
        <div style={s.eqChips}>
          {(equipment ?? []).length === 0 && <span style={{ fontSize: 12, color: 'var(--text4)' }}>데이터 로딩 중…</span>}
          {(equipment ?? []).map(eq => {
            const col = EQ_STATUS_COLOR[eq.status] ?? 'var(--text3)'
            return (
              <div key={eq.id} style={s.eqChip}>
                <EquipmentIcon id={eq.id} size={18} />
                <div style={{ minWidth: 0, flex: 1, overflow: 'hidden' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{eq.name}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: col }} />
                    <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>{eq.health_score}</span>
                    <span style={{ fontSize: 10, color: col, fontWeight: 600 }}>{eq.status}</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* 미해결 작업지시 */}
      <div style={{ ...s.cmsCard, flex: 1, cursor: 'pointer' }} onClick={() => onNavigate?.('maintenance')}>
        <div style={s.cmsHead}>
          <span style={s.cmsTitle}><Wrench size={15} color="#0d9488" /> 작업지시</span>
          <span style={s.cmsLink}>정비 →</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 6 }}>
          <span style={{ fontSize: 30, fontWeight: 700, color: openWO > 0 ? '#d29922' : '#3fb950', lineHeight: 1 }}>{openWO}</span>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>미해결</span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 6 }}>
          완료 {woStats?.done ?? 0} · 전체 {woStats?.total ?? 0}
          {worst && worst.status !== '정상' && <> · 건강도 최저: {worst.name} ({worst.health_score}점)</>}
        </div>
      </div>
    </div>
  )
}

export default function DashboardPanel({ onNavigate } = {}) {
  const [report,   setReport]   = useState([])
  const [summary,  setSummary]  = useState([])
  const [recent,   setRecent]   = useState([])
  const [loading,  setLoading]  = useState(true)
  const [forecast,  setForecast]  = useState(null)   // null=미실행, {}=실패, {models}=성공
  const [fcLoading, setFcLoading] = useState(false)
  const [briefing,  setBriefing]  = useState(null)
  const [billing,   setBilling]   = useState(null)
  const [equipment, setEquipment] = useState([])     // CMS 설비 헬스 요약
  const [woStats,   setWoStats]   = useState(null)    // 작업지시 요약
  const [companyName, setCompanyName] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [nowStr,     setNowStr]     = useState('')

  const lastBriefDate = useRef(null)

  // 라이브 시계
  useEffect(() => {
    const upd = () => setNowStr(new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
    upd()
    const tid = setInterval(upd, 1000)
    return () => clearInterval(tid)
  }, [])

  const loadForecast = () => {
    setFcLoading(true)
    getTotalDemandForecast(30)
      .then(r => setForecast(r.data ?? null))
      .catch(() => setForecast(null))
      .finally(() => setFcLoading(false))
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true); setForecast(null); setBriefing(null)
    lastBriefDate.current = null

    getSettings().then(r => setCompanyName(r.data?.profile?.company_name ?? '')).catch(() => {})

    // Heavy monthly report can take 10s+ on the backend. Do not block first paint on it.
    getReport(24)
      .then(r => { if (!cancelled) setReport(r.data.items ?? []) })  // 백엔드가 이미 오래된→최신 순
      .catch(() => {})

    Promise.allSettled([
      getAnomalySummary(),
      getAnomalies(5),
      getEquipmentStatus(30),
      getWorkOrderStats(),
    ]).then(([s, a, eq, wo]) => {
      if (cancelled) return
      if (s.status === 'fulfilled') setSummary(s.value.data.summary ?? [])
      if (a.status === 'fulfilled') setRecent(a.value.data.items ?? [])
      if (eq.status === 'fulfilled') setEquipment(eq.value.data.items ?? [])
      if (wo.status === 'fulfilled' && !wo.value.data.error) setWoStats(wo.value.data)
    }).finally(() => {
      if (cancelled) return
      setLoading(false)
      setTimeout(loadForecast, 0)
    })

    // 브리핑 + 비용 로드 — 최신 완료 데이터 일자 기준으로 자동 추적
    const loadBrief = async () => {
      try {
        const latest = await getLatestDataDate()
        const date   = latest.data?.date
        if (!date || date === lastBriefDate.current) return
        lastBriefDate.current = date
        const [brief, bill] = await Promise.allSettled([
          getDailyReport(date),
          getBilling(),
        ])
        if (cancelled) return
        if (brief.status === 'fulfilled' && brief.value?.data) setBriefing(brief.value.data)
        if (bill.status === 'fulfilled' && bill.value?.data && !bill.value.data.error) setBilling(bill.value.data)
      } catch {}
    }
    loadBrief()

    // 라이브 데이터 적재가 진행되면 최신 완료 일자가 바뀔 수 있으므로 주기적으로 확인한다.
    const id = setInterval(loadBrief, 30000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [refreshKey])

  // 부지 전체 수요: 예측만 (수요예측현황 페이지와 동일)
  const fcData = (forecast?.forecast ?? []).map(r => ({
    h:    r.ts.replace('T', ' ').slice(11, 16),
    yhat: r.yhat_kw,
  }))

  const latest = report[report.length - 1]
  const prev   = report[report.length - 2]
  const totalAnomalies = summary.reduce((a, b) => a + b.count, 0)
  const highCount = summary.find(s => s.severity === 'HIGH')?.count ?? 0
  const medCount  = summary.find(s => s.severity === 'MEDIUM')?.count ?? 0
  const lowCount  = summary.find(s => s.severity === 'LOW')?.count ?? 0

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
      <style>{`@keyframes skPulse{0%,100%{opacity:.35}50%{opacity:.75}}`}</style>
      <div style={s.header}>
        <div>
          <div style={s.title}>대시보드 종합현황</div>
          {companyName && <div style={s.sub}>{companyName}</div>}
        </div>
        {latest && (
          <div style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', gap: 12, alignItems: 'center' }}>
            <span>최근 보고: <strong style={{ color: 'var(--text)' }}>{latest.period}</strong></span>
            {nowStr && <span style={{ fontWeight: 600, color: 'var(--text2)' }}>{nowStr}</span>}
            <button style={s.btnGhost} onClick={() => setRefreshKey(k => k + 1)} title="데이터 다시 불러오기"><RefreshCw size={11} style={{verticalAlign:'-1px',marginRight:4}}/>새로고침</button>
          </div>
        )}
      </div>

      {loading ? (
        <DashboardSkeleton />
      ) : (
        <div style={s.body}>
          {/* ================= ROW 0: CMS 설비 상태 요약 ================= */}
          <CmsSummary equipment={equipment} woStats={woStats} onNavigate={onNavigate} />

          {/* ================= ROW 1: KPI 6개 (3×2) ================= */}
          <div style={s.row1}>
            <KpiCard
              label="이번 달 총 소비량"
              value={latest ? Math.round((latest.total_consumption_kwh ?? 0) / 1000).toLocaleString() : '–'}
              unit="MWh"
              sub={latest ? `기준: ${latest.period}` : "월별 전체 소비량"}
              color="var(--text)"
              trend={prev && latest ? -((latest.total_consumption_kwh - prev.total_consumption_kwh) / Math.max(prev.total_consumption_kwh, 1) * 100) : undefined}
              sparkline={report.slice(-12).map(r => r.total_consumption_kwh).filter(v => v != null)}
            />
            <KpiCard
              label="자급률"
              value={latest?.self_sufficiency_pct?.toFixed(1) ?? '–'}
              unit="%"
              sub="태양광 + CHP 비중"
              color="#3fb950"
              gaugePct={latest?.self_sufficiency_pct}
              trend={prev ? latest?.self_sufficiency_pct - prev.self_sufficiency_pct : undefined}
              sparkline={report.slice(-12).map(r => r.self_sufficiency_pct).filter(v => v != null)}
            />
            <KpiCard
              label="평균 COP"
              value={latest?.avg_cop?.toFixed(2) ?? '–'}
              unit=""
              sub="냉방 성능계수 (기준 2.06)"
              color="#2563eb"
              gaugePct={latest?.avg_cop ? Math.min(100, (latest.avg_cop / 4) * 100) : 0}
              trend={prev ? latest?.avg_cop - prev.avg_cop : undefined}
              sparkline={report.slice(-12).map(r => r.avg_cop).filter(v => v != null)}
            />
            <KpiCard
              label="그리드 의존도"
              value={latest?.grid_dependency_pct?.toFixed(1) ?? '–'}
              unit="%"
              sub="낮을수록 자립 에너지 비중 높음"
              color="#d29922"
              gaugePct={latest?.grid_dependency_pct}
              trend={prev ? -(latest?.grid_dependency_pct - prev.grid_dependency_pct) : undefined}
              sparkline={report.slice(-12).map(r => r.grid_dependency_pct).filter(v => v != null)}
            />
            <BillingMiniCard billing={billing} />
            <KpiCard
              label="이상탐지"
              value={totalAnomalies}
              unit="건"
              sub={`고위험 ${highCount} · 중위험 ${medCount} · 저위험 ${lowCount}`}
              color={highCount > 0 ? '#f85149' : totalAnomalies > 0 ? '#d29922' : '#3fb950'}
            />
          </div>

          {/* ================= ROW 1.5: AI 브리핑 (풀폭) + 시간대 프로파일 ================= */}
          <BriefingCard
            briefing={briefing}
            onRegenerate={async () => {
              if (!briefing?.date) return
              try {
                const r = await getDailyReport(briefing.date, true)   // regenerate=true
                if (r?.data) setBriefing(r.data)
              } catch {}
            }}
          />

          {/* ================= ROW 2 ================= */}
          <div style={s.row2}>
            {/* 전력 수요 예측 (24시간) */}
            <div style={s.chartBox}>
              <div style={s.chartHeader}>
                <div style={s.chartTitle}><TrendingUp size={13} style={{ verticalAlign: '-2px', marginRight: 6 }} />부지 전체 수전 전력 — 예측</div>
              </div>
              <div style={s.chartContent}>
                {fcLoading && <div style={s.emptyMsg}>예측 중...</div>}
                {!fcLoading && fcData.length === 0 && (
                  <div style={s.emptyMsg}>부지 전체 수요 데이터 없음</div>
                )}
                {!fcLoading && fcData.length > 0 && (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={fcData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false} />
                      <XAxis dataKey="h" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} interval={3} />
                      <YAxis tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false}
                        tickFormatter={v => `${Math.round(v)}`} unit=" kW" />
                      <Tooltip {...tt} formatter={v => [`${Number(v).toFixed(1)} kW`, '예측']} />
                      {forecast?.peak_ts && (
                        <ReferenceLine
                          x={forecast.peak_ts.replace('T', ' ').slice(11, 16)}
                          stroke="#f85149" strokeDasharray="4 2"
                          label={{ value: `피크 ${forecast.peak_ts.replace('T',' ').slice(11,16)}`, fill: '#f85149', fontSize: 9, position: 'insideTopRight' }}
                        />
                      )}
                      <Line type="monotone" dataKey="yhat" name="예측"
                        stroke="#a371f7" strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>

            {/* 에너지 소스 믹스 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 에너지 소스 믹스</div>
              <div style={{ fontSize: 10, color: 'var(--text4)', marginBottom: 8 }}>단위: MWh</div>
              <div style={s.chartContent}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={mixData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false} />
                    <XAxis dataKey="period" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false}
                      tickFormatter={v => v?.slice(2)}/>
                    <YAxis tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false}
                      tickFormatter={v => v}/>
                    <Tooltip {...tt} formatter={(v, n) => [`${v} MWh`, n]}/>
                    <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} iconSize={8} />
                    <Bar dataKey="PV"   stackId="a" fill="#3fb950" name="태양광"/>
                    <Bar dataKey="CHP"  stackId="a" fill="#2563eb" name="열병합"/>
                    <Bar dataKey="Grid" stackId="a" fill="#d29922" name="수전력" radius={[2, 2, 0, 0]}/>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* ================= ROW 3 ================= */}
          <div style={s.row3}>
            {/* 최근 이상탐지 테이블 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>최근 이상탐지 이벤트 (5건)</div>
              <div style={{ ...s.chartContent, overflowY: 'auto' }}>
                {recent.length === 0 ? (
                  <div style={s.emptyMsg}>탐지된 이상 없음</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    {recent.map(item => (
                      <div key={item.id} style={{ ...s.anomalyRow, cursor: 'pointer' }}
                        onClick={() => onNavigate?.('anomaly')}
                        title="이상탐지 내역 보기">
                        <span style={{ ...s.sevDot, background: SEV_COLOR[item.severity] }}/>
                        <span style={{ fontSize: 10, color: 'var(--text4)', width: 65, flexShrink: 0 }}>
                          {item.timestamp?.slice(5, 16).replace('T', ' ')}
                        </span>
                        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text)', width: 70, flexShrink: 0 }}>
                          {TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--text3)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.description}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {recent.length > 0 && (
                  <div style={{ textAlign: 'right', paddingTop: 8 }}>
                    <button onClick={() => onNavigate?.('anomaly')}
                      style={{ background: 'none', border: 'none', cursor: 'pointer',
                        fontSize: 11, color: '#a371f7', fontWeight: 600 }}>
                      전체 이상탐지 내역 보기 →
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* 월별 COP 추이 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 평균 COP (설비 효율)</div>
              <div style={s.chartContent}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={report.slice(-12)} margin={{ top: 10, right: 10, left: -24, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" vertical={false} />
                    <XAxis dataKey="period" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} tickFormatter={v => v?.slice(2)}/>
                    <YAxis tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false} axisLine={false} domain={['auto', 'auto']}/>
                    <ReferenceLine y={2.06} stroke="#8b949e" strokeDasharray="4 2" label={{ value: "기준 2.06", position: "insideTopRight", fill: "#8b949e", fontSize: 9 }} />
                    <Tooltip {...tt} formatter={v => [v?.toFixed(2), 'COP']}/>
                    <Line type="monotone" dataKey="avg_cop" name="COP" stroke="#2563eb" strokeWidth={2} dot={{ fill: '#2563eb', r: 2 }} activeDot={{ r: 4 }}/>
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

          </div>
        </div>
      )}
    </div>
  )
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header:     { padding: '12px 20px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)' },
  title:      { fontWeight: 700, fontSize: 15, color: 'var(--text)' },
  sub:        { fontSize: 11, color: 'var(--text3)', marginTop: 2 },
  btnGhost:   { background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text3)', fontSize: 11, padding: '4px 10px', cursor: 'pointer' },
  btnMicro:   { background: '#a371f722', border: '1px solid #a371f744', borderRadius: 4, color: '#a371f7', fontSize: 10, padding: '2px 8px', cursor: 'pointer', fontWeight: 600 },

  loading:    { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 13 },

  body:       {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    padding: '12px 20px',
    overflowY: 'auto',   // 한 화면 강제 채우기 대신 스크롤 — 차트가 항상 실제 높이를 받음
  },

  cmsRow:     { display: 'flex', gap: 12, flexShrink: 0 },
  cmsCard:    { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '12px 16px', minWidth: 0 },
  cmsHead:    { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  cmsTitle:   { fontSize: 12, fontWeight: 700, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 6 },
  cmsLink:    { fontSize: 10, color: '#2563eb', fontWeight: 600 },
  eqChips:    { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginTop: 10 },
  eqChip:     { display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden' },

  row1:       { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, flexShrink: 0 },
  row2:       { display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12, height: 260, flexShrink: 0 },
  row3:       { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, height: 280, flexShrink: 0 },

  kpiCard:    { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '16px 18px', display: 'flex', flexDirection: 'column', minHeight: 130 },

  briefingCard:    { background: 'linear-gradient(135deg, #2563eb0a, #a371f70a)', border: '1px solid #2563eb33', borderRadius: 10, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
  briefingHeader:  { padding: '10px 14px', background: '#2563eb11', borderBottom: '1px solid #2563eb22', display: 'flex', alignItems: 'center', gap: 6 },
  briefingGrid:    { display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 0 },
  briefingText:    { padding: '12px 14px', borderRight: '1px solid #2563eb22', display: 'flex', flexDirection: 'column', gap: 10, overflow: 'hidden' },
  briefingSummary: { fontSize: 12, color: 'var(--text2)', lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'keep-all' },
  briefingChart:   { padding: '10px 12px', display: 'flex', flexDirection: 'column' },
  actionsBlock:    { background: '#3fb95011', border: '1px solid #3fb95033', borderRadius: 6, padding: '8px 10px' },
  actionsBlockTitle:{ fontSize: 11, fontWeight: 700, color: '#3fb950', marginBottom: 4 },
  actionsBlockText: { fontSize: 11, color: 'var(--text2)', lineHeight: 1.7, whiteSpace: 'pre-wrap' },
  dlBtn:           { padding: '3px 9px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text3)', fontSize: 10, cursor: 'pointer', fontWeight: 600 },

  chartBox:   { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column', minHeight: 0 },
  chartHeader:{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  chartTitle: { fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 8 },
  chartContent:{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }, // minHeight 0 is crucial for Recharts in CSS Grid

  emptyMsg:   { margin: 'auto', color: 'var(--text4)', fontSize: 11 },

  anomalyRow: { display: 'flex', alignItems: 'center', gap: 8, padding: '6px 4px', borderBottom: '1px solid var(--line)' },
  sevDot:     { width: 6, height: 6, borderRadius: '50%', flexShrink: 0 },
}
