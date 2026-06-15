import { useState, useEffect, useCallback } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend, ReferenceLine,
} from 'recharts'
import {
  getDailyReport, getDailyReportList, getLatestDataDate,
  getSchedulerStatus, runSchedulerNow, dailyDownloadUrl,
} from '../../api/client'

const tt = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: 'var(--text)' },
  itemStyle: { color: 'var(--text3)' },
}

const SEV_COLOR  = { HIGH: '#f85149', MEDIUM: '#d29922', LOW: '#2563eb' }
const TYPE_LABEL = {
  COPDrop: 'COP 급락', CHPOutage: 'CHP 정지', PowerSpike: '전력 급등',
  NightConsumption: '야간 소비', PVNightNonZero: 'PV 야간 비정상', Unknown: '기타',
}

function Metric({ label, value, color = 'var(--text)' }) {
  return (
    <div style={s.metric}>
      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, color, marginTop: 2 }}>{value ?? '–'}</div>
    </div>
  )
}

function fmtDateTime(iso) {
  if (!iso) return '–'
  const d = new Date(iso)
  return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function daily_report_panel() {
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
            <span style={{ color: 'var(--text)', fontWeight: 600 }}>
              자동 생성 스케줄러 {sched.enabled ? '활성' : '비활성'}
            </span>
            <span style={{ color: 'var(--text3)' }}>다음 실행: {fmtDateTime(sched.next_run)}</span>
            <span style={{ color: 'var(--text3)' }}>
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
                <span style={{ fontSize: 12, color: 'var(--text4)' }}>다운로드:</span>
                <button style={s.dlBtn} onClick={() => handleDownload('pdf')}>PDF</button>
                <button style={s.dlBtn} onClick={() => handleDownload('docx')}>DOCX</button>
                <button style={s.dlBtn} onClick={() => handleDownload('hwpx')}>HWPX</button>
              </div>
            </div>

            {/* AI 일일 브리핑 (전면) */}
            {report.ai_summary && (
              <div style={s.summaryBox}>
                <div style={s.summaryTitle}>🤖 AI 일일 브리핑</div>
                <div style={s.summaryText}>{report.ai_summary}</div>
              </div>
            )}

            {/* 오늘 할 일 (운영 권고) */}
            {report.today_actions && (
              <div style={s.actionsBox}>
                <div style={s.actionsTitle}>✅ 오늘 할 일</div>
                <div style={s.actionsText}>{report.today_actions}</div>
              </div>
            )}

            {/* 컴팩트 KPI 스트립 */}
            <div style={s.kpiStrip}>
              <Metric label="총 소비량"     value={`${report.total_consumption_kwh?.toLocaleString()} kWh`} color="#2563eb" />
              <Metric label="자급률"        value={report.self_sufficiency_pct != null ? `${report.self_sufficiency_pct}%` : '–'} color="#3fb950" />
              <Metric label="평균 COP"      value={report.avg_cop ?? '–'} color="#a371f7" />
              <Metric label="그리드 의존도" value={report.grid_dependency_pct != null ? `${report.grid_dependency_pct}%` : '–'} color="#d29922" />
              <Metric label="피크"          value={report.peak_hour != null ? `${report.peak_hour}시 · ${report.peak_kw?.toLocaleString()}kW` : '–'} color="#f85149" />
            </div>

            {/* 시간대별 프로파일 (핵심) */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>⏱ 시간대별 전력 프로파일 (kW) · COP</div>
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={hourly} margin={{ top: 8, right: 16, left: -8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1" />
                  <XAxis dataKey="hourLabel" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                    label={{ value: '시', position: 'insideBottomRight', fontSize: 10, fill: '#909aa8', offset: -2 }} />
                  <YAxis yAxisId="kw" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
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
                  <Bar yAxisId="kw" dataKey="chp_kw"  stackId="a" name="CHP" fill="#2563eb" radius={[3, 3, 0, 0]} />
                  <Line yAxisId="cop" type="monotone" dataKey="cop" name="COP" stroke="#a371f7" strokeWidth={2}
                    dot={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* 당일 이상 이벤트 */}
            <div style={s.chartBox}>
              <div style={s.chartTitle}>
                🚨 당일 이상 이벤트
                <span style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 400, marginLeft: 8 }}>
                  {report.anomaly_events?.length ?? report.anomaly_count ?? 0}건
                </span>
              </div>
              {(report.anomaly_events?.length ?? 0) === 0
                ? <div style={{ color: 'var(--text4)', fontSize: 13, padding: '16px 0', textAlign: 'center' }}>
                    탐지된 이상 없음
                  </div>
                : (
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    {report.anomaly_events.map(ev => (
                      <div key={ev.id} style={s.eventRow}>
                        <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text3)', minWidth: 46 }}>
                          {ev.timestamp?.slice(11, 16)}
                        </span>
                        <span style={{ ...s.sevPill, background: (SEV_COLOR[ev.severity] ?? 'var(--text4)') + '22', color: SEV_COLOR[ev.severity] ?? 'var(--text3)' }}>
                          {ev.severity}
                        </span>
                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', minWidth: 90 }}>
                          {TYPE_LABEL[ev.anomaly_type] ?? ev.anomaly_type}
                        </span>
                        <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#2563eb', minWidth: 70 }}>
                          {ev.meter_id}
                        </span>
                        <span style={{ fontSize: 12, color: 'var(--text3)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {ev.description}
                        </span>
                      </div>
                    ))}
                  </div>
                )
              }
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
                  <span style={{ fontWeight: 600, color: 'var(--text)', minWidth: 100 }}>{item.date}</span>
                  <span style={{ color: 'var(--text3)', fontSize: 12 }}>소비 {item.total_consumption_kwh?.toLocaleString()} kWh</span>
                  <span style={{ color: '#3fb950', fontSize: 12 }}>자급 {item.self_sufficiency_pct ?? '–'}%</span>
                  <span style={{ color: '#a371f7', fontSize: 12 }}>COP {item.avg_cop ?? '–'}</span>
                  <span style={{ color: item.anomaly_count > 0 ? '#f85149' : 'var(--text4)', fontSize: 12 }}>이상 {item.anomaly_count ?? 0}</span>
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
  header:      { padding: '16px 24px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, flexWrap: 'wrap', gap: 12 },
  title:       { fontWeight: 700, fontSize: 16, color: 'var(--text)' },
  sub:         { fontSize: 12, color: 'var(--text3)', marginTop: 3 },
  controls:    { display: 'flex', gap: 8, alignItems: 'center' },
  dateInput:   { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 13, padding: '6px 10px', colorScheme: 'light' },
  btn:         { background: '#2563eb', border: 'none', borderRadius: 6, color: '#fff', fontSize: 13, fontWeight: 600, padding: '7px 16px', cursor: 'pointer' },
  btnGhost:    { background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text3)', fontSize: 13, padding: '7px 14px', cursor: 'pointer' },
  body:        { flex: 1, overflowY: 'auto', padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 16 },
  schedBar:    { display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '10px 16px', fontSize: 12 },
  dot:         { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  placeholder: { padding: '40px', textAlign: 'center', color: 'var(--text3)', fontSize: 14 },
  reportDate:  { fontSize: 20, fontWeight: 700, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  dlGroup:     { display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' },
  dlBtn:       { background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, color: '#2563eb', fontSize: 12, fontWeight: 600, padding: '5px 12px', cursor: 'pointer' },
  badge:       { fontSize: 11, fontWeight: 600, color: '#a371f7', background: '#a371f722', borderRadius: 4, padding: '2px 8px' },
  badgeSm:     { fontSize: 10, fontWeight: 600, color: '#a371f7', background: '#a371f722', borderRadius: 3, padding: '1px 6px', marginLeft: 'auto' },
  kpiStrip:    { display: 'flex', gap: 8, flexWrap: 'wrap', background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '12px 16px' },
  metric:      { flex: 1, minWidth: 120, paddingRight: 12, borderRight: '1px solid var(--line)' },
  summaryBox:  { background: 'linear-gradient(135deg, #a371f714, var(--surface))', border: '1px solid #a371f744', borderRadius: 10, padding: '16px 20px' },
  summaryTitle:{ fontSize: 14, fontWeight: 700, color: '#a371f7', marginBottom: 8 },
  summaryText: { fontSize: 14, color: 'var(--text)', lineHeight: 1.75, whiteSpace: 'pre-wrap' },
  actionsBox:  { background: 'linear-gradient(135deg, #3fb95014, var(--surface))', border: '1px solid #3fb95044', borderRadius: 10, padding: '16px 20px' },
  actionsTitle:{ fontSize: 14, fontWeight: 700, color: '#3fb950', marginBottom: 8 },
  actionsText: { fontSize: 14, color: 'var(--text)', lineHeight: 1.75, whiteSpace: 'pre-wrap' },
  chartBox:    { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '16px 18px' },
  chartTitle:  { fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 12 },
  eventRow:    { display: 'flex', alignItems: 'center', gap: 12, padding: '8px 4px', borderBottom: '1px solid var(--line)' },
  sevPill:     { fontSize: 10, fontWeight: 700, borderRadius: 4, padding: '2px 8px', minWidth: 54, textAlign: 'center' },
  listRow:     { display: 'flex', alignItems: 'center', gap: 16, padding: '9px 10px', borderBottom: '1px solid var(--line)', cursor: 'pointer', borderRadius: 6 },
  listRowActive:{ background: '#2563eb18' },
}
