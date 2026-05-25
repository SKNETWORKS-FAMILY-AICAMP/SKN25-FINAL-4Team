import { useState, useEffect, useCallback } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend, ReferenceLine,
} from 'recharts'
import {
  getDailyReport, getDailyReportList, getLatestDataDate,
  getSchedulerStatus, runSchedulerNow, dailyDownloadUrl,
} from '../api/client'

const tt = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e6edf3' },
  itemStyle: { color: '#8b949e' },
}

function KpiCard({ label, value, sub, color = '#58a6ff' }) {
  return (
    <div style={s.kpiCard}>
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value ?? '–'}</div>
      <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>{sub}</div>
      <div style={{ fontSize: 13, color: '#e6edf3', marginTop: 6 }}>{label}</div>
    </div>
  )
}

function fmtDateTime(iso) {
  if (!iso) return '–'
  const d = new Date(iso)
  return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function DailyReportPanel() {
  const [date,    setDate]    = useState('')
  const [report,  setReport]  = useState(null)
  const [list,    setList]    = useState([])
  const [sched,   setSched]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  const loadList  = useCallback(() => getDailyReportList(30).then(r => setList(r.data.items ?? [])).catch(() => {}), [])
  const loadSched = useCallback(() => getSchedulerStatus().then(r => setSched(r.data)).catch(() => {}), [])

  const loadReport = useCallback((d, regenerate = false) => {
    if (!d) return
    setLoading(true); setError('')
    getDailyReport(d, regenerate)
      .then(r => {
        if (r.data.error) { setError(r.data.error); setReport(null) }
        else { setReport(r.data); loadList() }
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [loadList])

  // 최초: 최신 데이터 날짜로 초기화
  useEffect(() => {
    getLatestDataDate().then(r => {
      const d = r.data.date
      if (d) { setDate(d); loadReport(d) }
    }).catch(() => {})
    loadList(); loadSched()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const handleRunScheduler = () => {
    setLoading(true)
    runSchedulerNow()
      .then(r => { if (!r.data.error) { setDate(r.data.date); setReport(r.data) } })
      .finally(() => { loadSched(); loadList(); setLoading(false) })
  }

  const handleDownload = (fmt) => {
    if (!report?.date) return
    const a = document.createElement('a')
    a.href = dailyDownloadUrl(report.date, fmt)
    a.target = '_blank'
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  const hourly = (report?.hourly_profile ?? []).map(h => ({
    ...h, hourLabel: `${String(h.hour).padStart(2, '0')}`,
  }))

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.header}>
        <div>
          <div style={s.title}>📅 일일 보고서</div>
          <div style={s.sub}>Honda R&D Europe · 하루 단위 KPI · 시간대별 전력 프로파일</div>
        </div>
        <div style={s.controls}>
          <input
            type="date" value={date}
            onChange={e => setDate(e.target.value)}
            style={s.dateInput}
          />
          <button style={s.btn}        onClick={() => loadReport(date)}        disabled={loading || !date}>조회</button>
          <button style={s.btnGhost}   onClick={() => loadReport(date, true)}  disabled={loading || !date}>재생성</button>
        </div>
      </div>

      <div style={s.body}>
        {/* 스케줄러 상태 바 */}
        {sched && (
          <div style={s.schedBar}>
            <span style={{ ...s.dot, background: sched.enabled ? '#3fb950' : '#f85149' }} />
            <span style={{ color: '#e6edf3', fontWeight: 600 }}>
              자동 생성 스케줄러 {sched.enabled ? '활성' : '비활성'}
            </span>
            <span style={{ color: '#8b949e' }}>다음 실행: {fmtDateTime(sched.next_run)}</span>
            <span style={{ color: '#8b949e' }}>
              마지막: {sched.last_run?.date ?? '–'} ({sched.last_run?.status ?? '대기 중'})
            </span>
            <button style={{ ...s.btnGhost, marginLeft: 'auto' }} onClick={handleRunScheduler} disabled={loading}>
              지금 실행
            </button>
          </div>
        )}

        {loading && <div style={s.placeholder}>생성 중… (AI 요약 포함 최대 수십 초 소요)</div>}
        {error && !loading && <div style={{ ...s.placeholder, color: '#f85149' }}>⚠ {error}</div>}

        {report && !loading && (
          <>
            <div style={s.reportDate}>
              {report.date}
              {report.generated_by === 'scheduler' && <span style={s.badge}>자동 생성</span>}
              <div style={s.dlGroup}>
                <span style={{ fontSize: 12, color: '#6e7681' }}>다운로드:</span>
                <button style={s.dlBtn} onClick={() => handleDownload('pdf')}>PDF</button>
                <button style={s.dlBtn} onClick={() => handleDownload('docx')}>DOCX</button>
                <button style={s.dlBtn} onClick={() => handleDownload('hwpx')}>HWPX</button>
              </div>
            </div>

            {/* KPI 카드 */}
            <div style={s.kpiRow}>
              <KpiCard label="총 소비량"     value={`${report.total_consumption_kwh?.toLocaleString()} kWh`} sub="Daily Consumption" color="#58a6ff" />
              <KpiCard label="자급률"        value={report.self_sufficiency_pct != null ? `${report.self_sufficiency_pct}%` : '–'} sub="Self-Sufficiency" color="#3fb950" />
              <KpiCard label="평균 COP"      value={report.avg_cop ?? '–'} sub="냉방 성능계수" color="#a371f7" />
              <KpiCard label="그리드 의존도" value={report.grid_dependency_pct != null ? `${report.grid_dependency_pct}%` : '–'} sub="Grid Dependency" color="#d29922" />
              <KpiCard label="피크"          value={report.peak_hour != null ? `${report.peak_hour}시` : '–'} sub={report.peak_kw != null ? `${report.peak_kw?.toLocaleString()} kW` : ''} color="#f85149" />
              <KpiCard label="이상탐지"      value={`${report.anomaly_count ?? 0}건`} sub="당일 탐지" color={report.anomaly_count > 0 ? '#f85149' : '#3fb950'} />
            </div>

            {/* AI 요약 */}
            {report.ai_summary && (
              <div style={s.summaryBox}>
                <div style={s.summaryTitle}>🤖 AI 일일 요약</div>
                <div style={s.summaryText}>{report.ai_summary}</div>
              </div>
            )}

            {/* 시간대별 프로파일 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>시간대별 전력 프로파일 (kW) · COP</div>
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={hourly} margin={{ top: 8, right: 16, left: -8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                  <XAxis dataKey="hourLabel" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}
                    label={{ value: '시', position: 'insideBottomRight', fontSize: 10, fill: '#6e7681', offset: -2 }} />
                  <YAxis yAxisId="kw" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v}`} />
                  <YAxis yAxisId="cop" orientation="right" tick={{ fontSize: 10, fill: '#a371f7' }} tickLine={false} axisLine={false}
                    domain={[0, 'auto']} />
                  <Tooltip {...tt} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {report.peak_hour != null && (
                    <ReferenceLine yAxisId="kw" x={String(report.peak_hour).padStart(2, '0')}
                      stroke="#f85149" strokeDasharray="4 2"
                      label={{ value: '피크', position: 'top', fontSize: 10, fill: '#f85149' }} />
                  )}
                  <Bar yAxisId="kw" dataKey="grid_kw" stackId="a" name="계통" fill="#d29922" />
                  <Bar yAxisId="kw" dataKey="pv_kw"   stackId="a" name="태양광" fill="#3fb950" />
                  <Bar yAxisId="kw" dataKey="chp_kw"  stackId="a" name="CHP" fill="#58a6ff" radius={[3, 3, 0, 0]} />
                  <Line yAxisId="cop" type="monotone" dataKey="cop" name="COP" stroke="#a371f7" strokeWidth={2}
                    dot={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </>
        )}

        {/* 최근 보고서 목록 */}
        {list.length > 0 && (
          <div style={s.chartBox}>
            <div style={s.chartTitle}>최근 일일 보고서</div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {list.map(item => (
                <div key={item.date}
                  onClick={() => { setDate(item.date); loadReport(item.date) }}
                  style={{ ...s.listRow, ...(item.date === report?.date ? s.listRowActive : {}) }}>
                  <span style={{ fontWeight: 600, color: '#e6edf3', minWidth: 100 }}>{item.date}</span>
                  <span style={{ color: '#8b949e', fontSize: 12 }}>소비 {item.total_consumption_kwh?.toLocaleString()} kWh</span>
                  <span style={{ color: '#3fb950', fontSize: 12 }}>자급 {item.self_sufficiency_pct ?? '–'}%</span>
                  <span style={{ color: '#a371f7', fontSize: 12 }}>COP {item.avg_cop ?? '–'}</span>
                  <span style={{ color: item.anomaly_count > 0 ? '#f85149' : '#6e7681', fontSize: 12 }}>이상 {item.anomaly_count ?? 0}</span>
                  {item.generated_by === 'scheduler' && <span style={s.badgeSm}>자동</span>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

const s = {
  wrap:        { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:      { padding: '16px 24px 12px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, flexWrap: 'wrap', gap: 12 },
  title:       { fontWeight: 700, fontSize: 16, color: '#e6edf3' },
  sub:         { fontSize: 12, color: '#8b949e', marginTop: 3 },
  controls:    { display: 'flex', gap: 8, alignItems: 'center' },
  dateInput:   { background: '#161b22', border: '1px solid #30363d', borderRadius: 6, color: '#e6edf3', fontSize: 13, padding: '6px 10px', colorScheme: 'dark' },
  btn:         { background: '#1f6feb', border: 'none', borderRadius: 6, color: '#fff', fontSize: 13, fontWeight: 600, padding: '7px 16px', cursor: 'pointer' },
  btnGhost:    { background: 'transparent', border: '1px solid #30363d', borderRadius: 6, color: '#8b949e', fontSize: 13, padding: '7px 14px', cursor: 'pointer' },
  body:        { flex: 1, overflowY: 'auto', padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 16 },
  schedBar:    { display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '10px 16px', fontSize: 12 },
  dot:         { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  placeholder: { padding: '40px', textAlign: 'center', color: '#8b949e', fontSize: 14 },
  reportDate:  { fontSize: 20, fontWeight: 700, color: '#e6edf3', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  dlGroup:     { display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' },
  dlBtn:       { background: 'transparent', border: '1px solid #30363d', borderRadius: 6, color: '#58a6ff', fontSize: 12, fontWeight: 600, padding: '5px 12px', cursor: 'pointer' },
  badge:       { fontSize: 11, fontWeight: 600, color: '#a371f7', background: '#a371f722', borderRadius: 4, padding: '2px 8px' },
  badgeSm:     { fontSize: 10, fontWeight: 600, color: '#a371f7', background: '#a371f722', borderRadius: 3, padding: '1px 6px', marginLeft: 'auto' },
  kpiRow:      { display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 },
  kpiCard:     { background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '14px 16px' },
  summaryBox:  { background: '#161b22', border: '1px solid #a371f733', borderRadius: 10, padding: '14px 18px' },
  summaryTitle:{ fontSize: 13, fontWeight: 600, color: '#a371f7', marginBottom: 8 },
  summaryText: { fontSize: 13, color: '#c9d1d9', lineHeight: 1.7, whiteSpace: 'pre-wrap' },
  chartBox:    { background: '#161b22', border: '1px solid #21262d', borderRadius: 10, padding: '16px 18px' },
  chartTitle:  { fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 12 },
  listRow:     { display: 'flex', alignItems: 'center', gap: 16, padding: '9px 10px', borderBottom: '1px solid #21262d', cursor: 'pointer', borderRadius: 6 },
  listRowActive:{ background: '#1f6feb18' },
}
