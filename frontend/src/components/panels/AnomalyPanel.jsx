import { useState, useEffect, useRef } from 'react'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ReferenceLine, Legend, PieChart, Pie, Cell,
} from 'recharts'
import { getAnomalies, getAnomalySummary, getAnomalyTimeline, getAnomalyTypes,
         getAnomalyContext, getAnomalyEvents, sendChat, runDetection, getDetectionStatus,
         getSimulatorStatus } from '../../api/client'

const SEV_COLOR  = { HIGH: '#f85149', MEDIUM: '#d29922', LOW: '#2563eb' }
const TYPE_LABEL = {
  COPDrop: 'COP 급락', CHPOutage: 'CHP 정지', PowerSpike: '전력 급등',
  NightConsumption: '야간 소비', PVNightNonZero: 'PV 야간 비정상', Unknown: '기타',
}
const tooltip = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 11 },
  labelStyle: { color: 'var(--text)' },
}

// 게이트웨이 장애 구간 (인공 보정 데이터 → 이상 오판 가능)
const GATEWAY_FAILURES = [
  ['2020-02-13', '2020-03-06', 'Workshop Gateway Failure #1'],
  ['2020-08-20', '2020-09-17', 'Emission Lab Gateway Failure'],
  ['2021-11-15', '2021-12-10', 'Distribution Gateway Failure'],
  ['2022-05-06', '2022-07-14', 'Workshop Gateway Failure #2'],
]

function getGatewayFailure(timestamp) {
  if (!timestamp) return null
  const t = timestamp.slice(0, 10)
  return GATEWAY_FAILURES.find(([s, e]) => t >= s && t <= e) ?? null
}

const TYPE_METRICS = {
  COPDrop:          [{ key: 'cop',    name: 'COP',          color: '#2563eb', unit: '' }],
  CHPOutage:        [{ key: 'chp_P',  name: 'CHP (kW)',     color: '#3fb950', unit: 'kW' }],
  PowerSpike:       [{ key: 'grid_P', name: '계통 전력 (kW)', color: '#f85149', unit: 'kW' }],
  NightConsumption: [{ key: 'grid_P', name: '계통 전력 (kW)', color: '#f85149', unit: 'kW' }],
  PVNightNonZero:   [{ key: 'pv_P',  name: 'PV (kW)',      color: '#d29922', unit: 'kW' }],
  Unknown: [
    { key: 'grid_P', name: '계통 전력 (kW)', color: '#f85149', unit: 'kW' },
    { key: 'cop',    name: 'COP',            color: '#2563eb', unit: '' },
  ],
}

function DetailPanel({ item, onClose }) {
  const [ctx,    setCtx]    = useState(null)
  const [loading,setLoading]= useState(true)
  const [ai,     setAi]     = useState('')
  const [aiLoad, setAiLoad] = useState(false)

  useEffect(() => {
    setCtx(null); setAi(''); setLoading(true)
    getAnomalyContext(item.id, 24)
      .then(r => setCtx(r.data))
      .finally(() => setLoading(false))
  }, [item.id])

  const askAI = async () => {
    setAiLoad(true)
    const q = `${item.timestamp?.slice(0,16).replace('T',' ')} 시점에 발생한 ${TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type} 이상탐지의 원인을 분석해줘. 심각도: ${item.severity}. 설명: ${item.description}`
    try {
      const { data } = await sendChat(q)
      setAi(data.answer)
    } catch { setAi('AI 분석 실패. API 서버를 확인하세요.') }
    finally { setAiLoad(false) }
  }

  const metrics  = TYPE_METRICS[item.anomaly_type] ?? TYPE_METRICS.Unknown
  const anomalyTs = ctx?.anomaly_ts?.slice(0, 16).replace('T', ' ')
  const isNewModel = item.source === 'vmd-lstm-residual'
  const gf = getGatewayFailure(item.timestamp)

  return (
    <div style={s.detail}>
      {/* 헤더 */}
      <div style={s.detailHeader}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ ...s.sevBadge, background: SEV_COLOR[item.severity] ?? '#555' }}>{item.severity}</span>
          <span style={s.detailType}>{TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type}</span>
          {isNewModel && (
            <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: '#ede9fe', color: '#7c3aed', border: '1px solid #a371f744' }}>
              LSTM 잔차
            </span>
          )}
        </div>
        <button style={s.closeBtn} onClick={onClose}>✕</button>
      </div>
      <div style={s.detailTs}>{item.timestamp?.slice(0,16).replace('T',' ')}</div>

      {/* 게이트웨이 장애 경고 */}
      {gf && (
        <div style={s.gwAlert}>
          ⚠️ <b>게이트웨이 장애 구간</b> — {gf[2]} ({gf[0]} ~ {gf[1]})<br/>
          <span style={{ fontSize: 11, opacity: 0.8 }}>이 구간 데이터는 인공 보정값이므로 이상 오판 가능성이 있습니다.</span>
        </div>
      )}

      <div style={s.detailDesc}>{item.description}</div>

      {/* 모델 점수 — 신 모델 vs 구 앙상블 구분 표시 */}
      {isNewModel ? (
        <div style={s.scoreRow}>
          <span style={s.scoreChip}>
            실제 {item.actual_w != null ? (item.actual_w / 1000).toFixed(1) + ' kW' : '–'}
          </span>
          <span style={s.scoreChip}>
            예측 {item.predicted_w != null ? (item.predicted_w / 1000).toFixed(1) + ' kW' : '–'}
          </span>
          <span style={{ ...s.scoreChip, color: '#f85149' }}>
            잔차 {item.residual_w != null ? (item.residual_w / 1000).toFixed(1) + ' kW' : '–'}
          </span>
          <span style={{ ...s.scoreChip, background: 'var(--line)', color: SEV_COLOR[item.severity] }}>
            {item.vote_count}개 모델 동의
          </span>
        </div>
      ) : (
        <div style={s.scoreRow}>
          <span style={s.scoreChip}>통계 {item.score_stat?.toFixed(1) ?? '–'}</span>
          <span style={s.scoreChip}>IsoForest {item.score_iso?.toFixed(3) ?? '–'}</span>
          <span style={s.scoreChip}>LSTM-AE {typeof item.score_lstm === 'number' ? item.score_lstm.toFixed(3) : '–'}</span>
          <span style={{ ...s.scoreChip, background: 'var(--line)', color: SEV_COLOR[item.severity] }}>
            {item.vote_count}개 모델 동의
          </span>
        </div>
      )}

      {/* 시계열 차트 */}
      <div style={s.chartSection}>
        <div style={s.sectionTitle}>전후 24시간 시계열</div>
        {loading && <div style={s.chartEmpty}>로딩 중...</div>}
        {!loading && ctx && metrics.map(m => (
          <div key={m.key} style={s.miniChart}>
            <div style={s.miniChartTitle}>{m.name}</div>
            <ResponsiveContainer width="100%" height={120}>
              <LineChart data={ctx.timeseries} margin={{ top: 4, right: 8, left: -28, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                <XAxis dataKey="ts" tick={false} tickLine={false}/>
                <YAxis tick={{ fontSize: 9, fill: '#909aa8' }} tickLine={false} axisLine={false}/>
                <Tooltip {...tooltip} formatter={v => [v != null ? `${v}${m.unit}` : '–', m.name]}
                  labelFormatter={l => l?.slice(11,16)}/>
                {anomalyTs && (
                  <ReferenceLine
                    x={ctx.timeseries.find(d => d.ts?.slice(0,16).replace('T',' ') === anomalyTs)?.ts}
                    stroke={SEV_COLOR[item.severity]} strokeWidth={2} strokeDasharray="4 2"
                    label={{ value: '이상', fill: SEV_COLOR[item.severity], fontSize: 10, position: 'top' }}
                  />
                )}
                <Line type="monotone" dataKey={m.key} stroke={m.color} strokeWidth={1.5}
                  dot={false} connectNulls={false}/>
              </LineChart>
            </ResponsiveContainer>
          </div>
        ))}
      </div>

      {/* AI 분석 */}
      <div style={s.aiSection}>
        <div style={s.sectionTitle}>AI 원인 분석</div>
        {!ai && (
          <button style={s.aiBtn} onClick={askAI} disabled={aiLoad}>
            {aiLoad ? '분석 중...' : '🤖 AI 원인 분석 요청'}
          </button>
        )}
        {ai && <div style={s.aiText}>{ai}</div>}
      </div>
    </div>
  )
}

// 새 탐지 실행 패널
function RunDetectionPanel({ onDone }) {
  const [start,   setStart]   = useState('2023-01-01')
  const [end,     setEnd]     = useState('2023-06-01')

  useEffect(() => {
    getSimulatorStatus().then(r => {
      const now = r.data?.current_time ?? r.data?.now
      if (!now) return
      const simDate = new Date(now)
      const endStr   = simDate.toISOString().slice(0, 10)
      const sixBefore = new Date(simDate)
      sixBefore.setMonth(sixBefore.getMonth() - 6)
      setEnd(endStr)
      setStart(sixBefore.toISOString().slice(0, 10))
    }).catch(() => {})
  }, [])
  const [jobId,   setJobId]   = useState(null)
  const [status,  setStatus]  = useState(null)
  const [open,    setOpen]    = useState(false)
  const pollRef = useRef(null)

  const startJob = async () => {
    try {
      const r = await runDetection(start, end)
      const id = r.data.job_id
      setJobId(id)
      setStatus({ status: 'queued' })
      pollRef.current = setInterval(async () => {
        const s = await getDetectionStatus(id)
        setStatus(s.data)
        if (s.data.status === 'done' || s.data.status === 'error') {
          clearInterval(pollRef.current)
          if (s.data.status === 'done') onDone()
        }
      }, 3000)
    } catch (e) {
      setStatus({ status: 'error', error: String(e) })
    }
  }

  useEffect(() => () => clearInterval(pollRef.current), [])

  const statusColor = status?.status === 'done' ? '#3fb950'
    : status?.status === 'error' ? '#f85149'
    : status?.status === 'running' ? '#d29922' : 'var(--text3)'

  return (
    <div style={s.runPanel}>
      <button style={s.runToggle} onClick={() => setOpen(o => !o)}>
        {open ? '▲' : '▼'} LSTM 잔차 새 탐지 실행
      </button>
      {open && (
        <div style={s.runForm}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>기간:</span>
            <input type="date" style={s.dateInput} value={start} onChange={e => setStart(e.target.value)}/>
            <span style={{ fontSize: 12, color: 'var(--text4)' }}>~</span>
            <input type="date" style={s.dateInput} value={end} onChange={e => setEnd(e.target.value)}/>
            <button style={{ ...s.runBtn, opacity: status?.status === 'running' || status?.status === 'queued' ? 0.5 : 1 }}
              onClick={startJob}
              disabled={status?.status === 'running' || status?.status === 'queued'}>
              탐지 실행
            </button>
          </div>
          {status && (
            <div style={{ fontSize: 12, color: statusColor, marginTop: 6 }}>
              {status.status === 'queued'  && '대기 중...'}
              {status.status === 'running' && '탐지 중... (수 분 소요)'}
              {status.status === 'done'    && `완료 — ${status.total ?? 0}건 저장 (${JSON.stringify(status.counts ?? {})})`}
              {status.status === 'error'   && `오류: ${status.error}`}
              {status.model && <span style={{ color: '#a371f7', marginLeft: 8 }}>[{status.model}]</span>}
            </div>
          )}
          <div style={{ fontSize: 11, color: 'var(--text4)', marginTop: 4 }}>
            계량기별 LSTM 예측 잔차를 학습 임계값과 비교해 이상 구간을 재탐지 후 DB에 저장합니다.
          </div>
        </div>
      )}
    </div>
  )
}

const TYPE_COLORS = {
  COPDrop: '#2563eb', CHPOutage: '#3fb950', PowerSpike: '#f85149',
  NightConsumption: '#d29922', PVNightNonZero: '#a371f7', Unknown: 'var(--text4)',
}

const CAUSE_COLORS = {
  '효율 저하':     { bg: '#2563eb14', border: '#2563eb', text: '#2563eb' },
  'CHP 정지':      { bg: '#3fb95014', border: '#3fb950', text: '#16a34a' },
  '전력 급등':     { bg: '#f8514914', border: '#f85149', text: '#dc2626' },
  '야간 과소비':   { bg: '#d2992214', border: '#d29922', text: '#b45309' },
  'PV 야간 비정상':{ bg: '#a371f714', border: '#a371f7', text: '#7c3aed' },
  '미분류':        { bg: '#909aa814', border: 'var(--text4)', text: 'var(--text3)' },
}

function CauseBadge({ label }) {
  const c = CAUSE_COLORS[label] ?? CAUSE_COLORS['미분류']
  return (
    <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 4,
      background: c.bg, border: `1px solid ${c.border}`, color: c.text, fontWeight: 600 }}>
      {label}
    </span>
  )
}

function EventsView({ severity, year, month, excludeGf }) {
  const [events,  setEvents]  = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getAnomalyEvents(severity === 'MEDIUM+' ? undefined : severity || undefined,
      year || undefined, month || undefined, 2, excludeGf)
      .then(r => setEvents(r.data.events ?? []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false))
  }, [severity, year, month, excludeGf])

  if (loading) return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '12px 0' }}>
      {[1,2,3,4].map(i => (
        <div key={i} style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '12px 16px' }}>
          <div style={{ height: 14, width: '50%', background: 'var(--line)', borderRadius: 4, marginBottom: 8 }}/>
          <div style={{ height: 12, width: '80%', background: 'var(--line)', borderRadius: 4 }}/>
        </div>
      ))}
    </div>
  )

  if (events.length === 0) return (
    <div style={{ textAlign: 'center', color: 'var(--text3)', paddingTop: 40 }}>이벤트 없음</div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 4 }}>
        총 <b style={{ color: 'var(--text)' }}>{events.length}</b>개 이벤트
        <span style={{ marginLeft: 8, color: '#484f58' }}>(연속 이상 포인트를 하나의 이벤트로 통합)</span>
      </div>
      {events.map((ev, i) => (
        <div key={i} style={{
          background: 'var(--surface)', border: `1px solid ${SEV_COLOR[ev.severity] ?? 'var(--border)'}22`,
          borderLeft: `3px solid ${SEV_COLOR[ev.severity] ?? 'var(--border)'}`,
          borderRadius: 8, padding: '10px 14px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
            <span style={{ ...s.sevBadge, background: SEV_COLOR[ev.severity] }}>{ev.severity}</span>
            <CauseBadge label={ev.cause_label} />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{ev.meter_id}</span>
            <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text4)' }}>
              {ev.point_count}포인트 · {ev.duration_h}시간
            </span>
          </div>
          <div style={{ display: 'flex', gap: 16, fontSize: 11, color: 'var(--text3)', flexWrap: 'wrap' }}>
            <span>시작 <b style={{ color: 'var(--text)' }}>{ev.start?.slice(0,16).replace('T',' ')}</b></span>
            <span>종료 <b style={{ color: 'var(--text)' }}>{ev.end?.slice(0,16).replace('T',' ')}</b></span>
            {ev.peak_residual_w != null && (
              <span>최대 잔차 <b style={{ color: '#f85149' }}>{(ev.peak_residual_w/1000).toFixed(1)} kW</b></span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function AnomalyPanel({ equipmentFilter } = {}) {
  const [items,   setItems]   = useState([])
  const [summary, setSummary] = useState([])
  const [timeline,setTimeline]= useState([])
  const [types,   setTypes]   = useState([])
  const [severity,setSeverity]= useState('MEDIUM+')
  const [year,    setYear]    = useState('')
  const [month,   setMonth]   = useState('')
  const [offset,  setOffset]  = useState(0)
  const [total,   setTotal]   = useState(0)
  const [loading, setLoading] = useState(true)
  const [selected,setSelected]= useState(null)
  const [excludeGf, setExcludeGf] = useState(true)
  const [viewMode, setViewMode] = useState('points')  // 'points' | 'events'
  const [eqFilter, setEqFilter] = useState(null)       // { name, types } — 설비 드릴다운
  const PAGE = 50

  const loadList = () => {
    setLoading(true)
    const typeParam = eqFilter?.types?.length ? eqFilter.types.join(',') : undefined
    Promise.all([
      getAnomalySummary(),
      getAnomalyTimeline(),
      getAnomalies(PAGE, severity || undefined, year || undefined, month || undefined, offset, excludeGf, typeParam),
    ]).then(([sv, t, a]) => {
      setSummary(sv.data.summary ?? [])
      setTimeline(t.data.timeline ?? [])
      setItems(a.data.items ?? [])
      setTotal(a.data.total ?? 0)
    }).finally(() => setLoading(false))
  }

  useEffect(() => {
    getAnomalyTypes().then(r => setTypes(r.data.types ?? [])).catch(() => {})
  }, [])

  // 설비 상태 감시 → "이상 내역" 드릴다운: 해당 설비 유형으로 필터
  useEffect(() => {
    if (equipmentFilter?.types?.length) {
      setEqFilter(equipmentFilter)
      setViewMode('points')
      setOffset(0)
      setSelected(null)
    }
  }, [equipmentFilter])

  useEffect(() => { loadList() }, [severity, year, month, offset, excludeGf, eqFilter]) // eslint-disable-line

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={s.title}>이상탐지 결과</span>
          <div style={{ display: 'flex', background: 'var(--line)', borderRadius: 6, padding: 2, gap: 2 }}>
            {[['points','포인트 목록'],['events','이벤트 통합']].map(([mode, label]) => (
              <button key={mode} onClick={() => setViewMode(mode)} style={{
                padding: '3px 10px', borderRadius: 4, border: 'none', cursor: 'pointer', fontSize: 12,
                background: viewMode === mode ? '#2563eb' : 'transparent',
                color: viewMode === mode ? '#fff' : 'var(--text3)', fontWeight: viewMode === mode ? 600 : 400,
              }}>{label}</button>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <select style={s.select} value={year} onChange={e => { setYear(e.target.value); setMonth(''); setOffset(0); setSelected(null) }}>
            <option value="">전체 연도</option>
            {[2018,2019,2020,2021,2022,2023].map(y => <option key={y} value={y}>{y}년</option>)}
          </select>
          <select style={s.select} value={month} onChange={e => { setMonth(e.target.value); setOffset(0); setSelected(null) }} disabled={!year}>
            <option value="">전체 월</option>
            {Array.from({length:12},(_,i)=>i+1).map(m => <option key={m} value={m}>{m}월</option>)}
          </select>
          <select style={s.select} value={severity} onChange={e => { setSeverity(e.target.value); setOffset(0); setSelected(null) }}>
            <option value="MEDIUM+">MEDIUM 이상 (기본)</option>
            <option value="HIGH">HIGH만</option>
            <option value="MEDIUM">MEDIUM만</option>
            <option value="LOW">LOW만</option>
            <option value="">전체 (LOW 포함)</option>
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text3)', cursor: 'pointer', userSelect: 'none' }}>
            <input type="checkbox" checked={excludeGf}
              onChange={e => { setExcludeGf(e.target.checked); setOffset(0); setSelected(null) }}/>
            장애구간 제외
          </label>
          <button
            onClick={loadList}
            disabled={loading}
            style={{ padding: '4px 12px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text)', fontSize: 11, cursor: 'pointer', fontWeight: 500 }}
            title="이상탐지 데이터 새로고침"
          >
            {loading ? '...' : '🔄 새로고침'}
          </button>
        </div>
      </div>

      {/* 새 탐지 실행 패널 */}
      <RunDetectionPanel onDone={() => { setOffset(0); loadList() }} />

      {/* 설비 드릴다운 필터 배너 */}
      {eqFilter && (
        <div style={s.filterBanner}>
          <span>🏭 <strong>{eqFilter.name}</strong> 관련 이상만 표시 중
            <span style={{ color: 'var(--text3)', marginLeft: 6 }}>
              ({(eqFilter.types ?? []).map(t => TYPE_LABEL[t] ?? t).join(', ')})
            </span>
          </span>
          <button onClick={() => { setEqFilter(null); setOffset(0) }} style={s.filterClear}>✕ 필터 해제</button>
        </div>
      )}

      {viewMode === 'events' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 20px 20px' }}>
          <EventsView severity={severity} year={year} month={month} excludeGf={excludeGf} />
        </div>
      )}

      <div style={{ ...s.body, display: viewMode === 'points' ? 'flex' : 'none' }}>
        {/* 좌측 */}
        <div style={{ ...s.left, width: selected ? '38%' : '100%' }}>
          {/* 요약 카드 */}
          <div style={s.cards}>
            {summary.map(sv => (
              <div key={sv.severity} style={{ ...s.card, borderColor: SEV_COLOR[sv.severity] ?? 'var(--border)' }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: SEV_COLOR[sv.severity] }}>{sv.count}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{sv.severity}</div>
              </div>
            ))}
            <div style={{ ...s.card, borderColor: 'var(--border)', flex: 2 }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)' }}>
                {summary.reduce((a, b) => a + b.count, 0)}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>전체</div>
            </div>
          </div>

          {/* 이상 유형 파이차트 */}
          {!selected && types.length > 0 && (
            <div style={s.chartBox}>
              <div style={s.chartTitle}>이상 유형 분포</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <PieChart width={130} height={130}>
                  <Pie data={types} dataKey="count" nameKey="type" cx="50%" cy="50%" outerRadius={55} innerRadius={28}>
                    {types.map(t => <Cell key={t.type} fill={TYPE_COLORS[t.type] ?? '#909aa8'}/>)}
                  </Pie>
                  <Tooltip {...tooltip} formatter={(v, n) => [v + '건', TYPE_LABEL[n] ?? n]}/>
                </PieChart>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5, flex: 1 }}>
                  {types.map(t => {
                    const tc = types.reduce((a, b) => a + b.count, 0)
                    const pct = tc ? ((t.count / tc) * 100).toFixed(1) : 0
                    return (
                      <div key={t.type} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: TYPE_COLORS[t.type] ?? 'var(--text4)', flexShrink: 0 }}/>
                        <span style={{ color: 'var(--text)', flex: 1 }}>{TYPE_LABEL[t.type] ?? t.type}</span>
                        <span style={{ color: 'var(--text3)' }}>{t.count}건</span>
                        <span style={{ color: 'var(--text4)' }}>({pct}%)</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}

          {/* 타임라인 */}
          {!selected && timeline.length > 0 && (
            <div style={s.chartBox}>
              <div style={s.chartTitle}>월별 이상탐지 현황</div>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={timeline} margin={{ top: 4, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7ebf1"/>
                  <XAxis dataKey="month" tick={{ fontSize: 9, fill: '#5a6675' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltip}/>
                  <Legend wrapperStyle={{ fontSize: 11, color: 'var(--text3)' }}/>
                  <Bar dataKey="HIGH"   name="HIGH"   stackId="a" fill={SEV_COLOR.HIGH}/>
                  <Bar dataKey="MEDIUM" name="MEDIUM" stackId="a" fill={SEV_COLOR.MEDIUM}/>
                  <Bar dataKey="LOW"    name="LOW"    stackId="a" fill={SEV_COLOR.LOW} fillOpacity={0.35} radius={[3,3,0,0]}/>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 목록 헤더 */}
          {!loading && total > 0 && (
            <div style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', justifyContent: 'space-between' }}>
              <span>총 {total.toLocaleString()}건</span>
              <span>{offset + 1}–{Math.min(offset + PAGE, total)} 표시</span>
            </div>
          )}

          {/* 목록 */}
          <div style={s.list}>
            {loading && [1,2,3,4,5].map(i => (
              <div key={i} style={{ ...s.item, cursor: 'default' }}>
                <div style={{ height: 18, width: '40%', marginBottom: 8, background: 'var(--line)', borderRadius: 4 }}/>
                <div style={{ height: 13, width: '80%', background: 'var(--line)', borderRadius: 4 }}/>
              </div>
            ))}
            {!loading && items.length === 0 && <div style={s.empty}>탐지된 이상 없음</div>}
            {items.map(item => {
              const gf = getGatewayFailure(item.timestamp)
              const isNew = item.source === 'vmd-lstm-residual'
              return (
                <div key={item.id}
                  style={{ ...s.item, ...(selected?.id === item.id ? s.itemActive : {}) }}
                  onClick={() => setSelected(selected?.id === item.id ? null : item)}
                >
                  <div style={s.itemTop}>
                    <span style={{ ...s.sevBadge, background: SEV_COLOR[item.severity] ?? '#555' }}>
                      {item.severity}
                    </span>
                    <span style={s.typeLabel}>{TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type}</span>
                    {isNew && (
                      <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: '#ede9fe', color: '#7c3aed', border: '1px solid #a371f733' }}>
                        잔차
                      </span>
                    )}
                    {gf && (
                      <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: '#fef3c7', color: '#d29922', border: '1px solid #d2992233' }}>
                        장애구간
                      </span>
                    )}
                    <span style={s.ts}>{item.timestamp?.slice(0,16).replace('T',' ')}</span>
                  </div>
                  <div style={s.desc}>{item.description}</div>
                  {!selected && (
                    <div style={s.scores}>
                      {isNew
                        ? <>실제 {item.actual_w != null ? (item.actual_w/1000).toFixed(1)+'kW' : '–'} &nbsp;·&nbsp;
                           잔차 {item.residual_w != null ? (item.residual_w/1000).toFixed(1)+'kW' : '–'}</>
                        : <>통계 {item.score_stat?.toFixed(1) ?? '–'} &nbsp;·&nbsp;
                           IsoForest {item.score_iso?.toFixed(3) ?? '–'} &nbsp;·&nbsp;
                           LSTM-AE {typeof item.score_lstm === 'number' ? item.score_lstm.toFixed(3) : '–'}</>
                      }
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* 페이지네이션 */}
          {total > PAGE && (
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', paddingTop: 4 }}>
              <button style={s.pageBtn} disabled={offset === 0}
                onClick={() => { setOffset(Math.max(0, offset - PAGE)); setSelected(null) }}>← 이전</button>
              <span style={{ fontSize: 12, color: 'var(--text3)', alignSelf: 'center' }}>
                {Math.floor(offset/PAGE)+1} / {Math.ceil(total/PAGE)}
              </span>
              <button style={s.pageBtn} disabled={offset + PAGE >= total}
                onClick={() => { setOffset(offset + PAGE); setSelected(null) }}>다음 →</button>
            </div>
          )}
        </div>

        {/* 우측: 상세 패널 */}
        {selected && (
          <div style={s.right}>
            <DetailPanel item={selected} onClose={() => setSelected(null)} />
          </div>
        )}
      </div>
    </div>
  )
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  filterBanner: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '8px 20px', background: '#2563eb18', borderBottom: '1px solid #2563eb44', fontSize: 12, color: 'var(--text2)', flexShrink: 0 },
  filterClear:  { padding: '3px 10px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text3)', fontSize: 11, cursor: 'pointer', fontWeight: 600, flexShrink: 0 },
  header:     { padding: '14px 20px 10px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 },
  title:      { fontWeight: 600, fontSize: 15, color: 'var(--text)' },
  select:     { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', padding: '4px 8px', fontSize: 13, cursor: 'pointer' },
  body:       { flex: 1, overflow: 'hidden', display: 'flex', gap: 0 },
  left:       { overflowY: 'auto', padding: '12px 20px 20px', display: 'flex', flexDirection: 'column', gap: 12, transition: 'width .2s', borderRight: '1px solid var(--line)' },
  right:      { flex: 1, overflowY: 'auto', padding: '16px 20px' },
  cards:      { display: 'flex', gap: 10, flexShrink: 0 },
  card:       { flex: 1, background: 'var(--surface)', border: '1px solid', borderRadius: 8, padding: '10px 14px', textAlign: 'center' },
  chartBox:   { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '14px 16px', flexShrink: 0 },
  chartTitle: { fontSize: 12, fontWeight: 600, color: 'var(--text3)', marginBottom: 10 },
  list:       { display: 'flex', flexDirection: 'column', gap: 8 },
  empty:      { textAlign: 'center', color: 'var(--text3)', paddingTop: 20 },
  item:       { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '10px 14px', cursor: 'pointer', transition: 'border-color .15s' },
  itemActive: { borderColor: '#2563eb', background: '#e8f1ff' },
  itemTop:    { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 },
  sevBadge:   { fontSize: 11, padding: '1px 6px', borderRadius: 4, color: 'var(--bg)', fontWeight: 700, flexShrink: 0 },
  typeLabel:  { fontSize: 13, fontWeight: 600, color: 'var(--text)' },
  ts:         { marginLeft: 'auto', fontSize: 11, color: 'var(--text3)', fontVariantNumeric: 'tabular-nums' },
  desc:       { fontSize: 12, color: 'var(--text3)', marginBottom: 4 },
  scores:     { fontSize: 11, color: 'var(--text4)' },
  pageBtn:    { padding: '5px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer' },

  // 새 탐지 실행 패널
  runPanel:   { borderBottom: '1px solid var(--line)', flexShrink: 0, background: 'var(--bg)' },
  runToggle:  { width: '100%', padding: '8px 20px', background: 'none', border: 'none', color: 'var(--text3)', fontSize: 12, textAlign: 'left', cursor: 'pointer' },
  runForm:    { padding: '8px 20px 12px', display: 'flex', flexDirection: 'column', gap: 4 },
  dateInput:  { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', padding: '4px 8px', fontSize: 13 },
  runBtn:     { padding: '5px 14px', background: '#2563eb', border: 'none', borderRadius: 6, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 13 },
  gwAlert:    { fontSize: 12, color: '#d29922', background: '#fef3c7', border: '1px solid #d2992244', borderRadius: 8, padding: '8px 12px', lineHeight: 1.7 },

  // 상세 패널
  detail:       { display: 'flex', flexDirection: 'column', gap: 12 },
  detailHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  detailType:   { fontSize: 15, fontWeight: 600, color: 'var(--text)' },
  detailTs:     { fontSize: 12, color: 'var(--text3)' },
  detailDesc:   { fontSize: 13, color: 'var(--text)', lineHeight: 1.6, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '10px 14px' },
  scoreRow:     { display: 'flex', flexWrap: 'wrap', gap: 6 },
  scoreChip:    { fontSize: 11, padding: '3px 8px', borderRadius: 4, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text3)' },
  chartSection: { display: 'flex', flexDirection: 'column', gap: 10 },
  sectionTitle: { fontSize: 12, fontWeight: 600, color: 'var(--text3)', marginBottom: 4 },
  chartEmpty:   { color: 'var(--text3)', fontSize: 13, paddingTop: 20, textAlign: 'center' },
  miniChart:    { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: '10px 14px' },
  miniChartTitle: { fontSize: 11, color: 'var(--text3)', marginBottom: 6 },
  closeBtn:     { background: 'none', border: 'none', color: 'var(--text4)', cursor: 'pointer', fontSize: 16, padding: '2px 6px' },
  aiSection:    { display: 'flex', flexDirection: 'column', gap: 8 },
  aiBtn:        { padding: '9px 16px', background: '#2563eb', border: 'none', borderRadius: 8, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 13, width: '100%' },
  aiText:       { fontSize: 13, color: 'var(--text)', lineHeight: 1.7, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px', whiteSpace: 'pre-wrap' },
}
