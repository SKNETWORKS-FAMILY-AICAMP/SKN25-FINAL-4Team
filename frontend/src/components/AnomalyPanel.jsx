import { useState, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ReferenceLine, Legend, PieChart, Pie, Cell,
} from 'recharts'
import { getAnomalies, getAnomalySummary, getAnomalyTimeline, getAnomalyTypes, getAnomalyContext, sendChat } from '../api/client'

const SEV_COLOR  = { HIGH: '#f85149', MEDIUM: '#d29922', LOW: '#58a6ff' }
const TYPE_LABEL = {
  COPDrop: 'COP 급락', CHPOutage: 'CHP 정지', PowerSpike: '전력 급등',
  NightConsumption: '야간 소비', PVNightNonZero: 'PV 야간 비정상', Unknown: '기타',
}
const tooltip = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 11 },
  labelStyle: { color: '#e6edf3' },
}

// 이상 유형별 표시할 지표
const TYPE_METRICS = {
  COPDrop:          [{ key: 'cop',          name: 'COP',          color: '#58a6ff', unit: '' }],
  CHPOutage:        [{ key: 'chp_P',        name: 'CHP (kW)',     color: '#3fb950', unit: 'kW' }],
  PowerSpike:       [{ key: 'grid_P',       name: '계통 전력 (kW)', color: '#f85149', unit: 'kW' }],
  NightConsumption: [{ key: 'grid_P',       name: '계통 전력 (kW)', color: '#f85149', unit: 'kW' }],
  PVNightNonZero:   [{ key: 'pv_P',         name: 'PV (kW)',      color: '#d29922', unit: 'kW' }],
  Unknown:          [
    { key: 'grid_P', name: '계통 전력 (kW)', color: '#f85149', unit: 'kW' },
    { key: 'cop',    name: 'COP',            color: '#58a6ff', unit: '' },
  ],
}

function DetailPanel({ item, onClose }) {
  const [ctx,     setCtx]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [ai,      setAi]      = useState('')
  const [aiLoad,  setAiLoad]  = useState(false)

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

  const metrics = TYPE_METRICS[item.anomaly_type] ?? TYPE_METRICS.Unknown
  const anomalyTs = ctx?.anomaly_ts?.slice(0, 16).replace('T', ' ')

  return (
    <div style={s.detail}>
      {/* 헤더 */}
      <div style={s.detailHeader}>
        <div>
          <span style={{ ...s.sevBadge, background: SEV_COLOR[item.severity] ?? '#555' }}>{item.severity}</span>
          <span style={s.detailType}>{TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type}</span>
        </div>
        <button style={s.closeBtn} onClick={onClose}>✕</button>
      </div>
      <div style={s.detailTs}>{item.timestamp?.slice(0,16).replace('T',' ')}</div>
      <div style={s.detailDesc}>{item.description}</div>

      {/* 모델 점수 */}
      <div style={s.scoreRow}>
        <span style={s.scoreChip}>통계 {item.score_stat?.toFixed(1) ?? '–'}</span>
        <span style={s.scoreChip}>IsoForest {item.score_iso?.toFixed(3) ?? '–'}</span>
        <span style={s.scoreChip}>LSTM {typeof item.score_lstm === 'number' ? item.score_lstm.toFixed(3) : '–'}</span>
        <span style={{ ...s.scoreChip, background: '#21262d', color: SEV_COLOR[item.severity] }}>
          {item.vote_count}개 모델 동의
        </span>
      </div>

      {/* 시계열 차트 */}
      <div style={s.chartSection}>
        <div style={s.sectionTitle}>전후 24시간 시계열</div>
        {loading && <div style={s.chartEmpty}>로딩 중...</div>}
        {!loading && ctx && metrics.map(m => (
          <div key={m.key} style={s.miniChart}>
            <div style={s.miniChartTitle}>{m.name}</div>
            <ResponsiveContainer width="100%" height={120}>
              <LineChart data={ctx.timeseries} margin={{ top: 4, right: 8, left: -28, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                <XAxis dataKey="ts" tick={false} tickLine={false}/>
                <YAxis tick={{ fontSize: 9, fill: '#6e7681' }} tickLine={false} axisLine={false}/>
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

const TYPE_COLORS = {
  COPDrop: '#58a6ff', CHPOutage: '#3fb950', PowerSpike: '#f85149',
  NightConsumption: '#d29922', PVNightNonZero: '#a371f7', Unknown: '#6e7681',
}

export default function AnomalyPanel() {
  const [items,    setItems]    = useState([])
  const [summary,  setSummary]  = useState([])
  const [timeline, setTimeline] = useState([])
  const [types,    setTypes]    = useState([])
  const [severity, setSeverity] = useState('MEDIUM+')
  const [year,     setYear]     = useState('')
  const [month,    setMonth]    = useState('')
  const [offset,   setOffset]   = useState(0)
  const [total,    setTotal]    = useState(0)
  const [loading,  setLoading]  = useState(true)
  const [selected, setSelected] = useState(null)
  const PAGE = 50

  useEffect(() => {
    // 타입 요약은 필터와 무관하게 1회만 로드
    getAnomalyTypes().then(r => setTypes(r.data.types ?? [])).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([
      getAnomalySummary(),
      getAnomalyTimeline(),
      getAnomalies(PAGE, severity || undefined, year || undefined, month || undefined, offset),
    ]).then(([s, t, a]) => {
      setSummary(s.data.summary ?? [])
      setTimeline(t.data.timeline ?? [])
      setItems(a.data.items ?? [])
      setTotal(a.data.total ?? 0)
    }).finally(() => setLoading(false))
  }, [severity, year, month, offset])

  const resetFilters = () => { setSeverity('MEDIUM+'); setYear(''); setMonth(''); setOffset(0); setSelected(null) }

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <span style={s.title}>이상탐지 결과</span>
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
        </div>
      </div>

      <div style={s.body}>
        {/* 좌측: 요약 + 타임라인 + 목록 */}
        <div style={{ ...s.left, width: selected ? '38%' : '100%' }}>
          {/* 요약 카드 */}
          <div style={s.cards}>
            {summary.map(sv => (
              <div key={sv.severity} style={{ ...s.card, borderColor: SEV_COLOR[sv.severity] ?? '#30363d' }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: SEV_COLOR[sv.severity] }}>{sv.count}</div>
                <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>{sv.severity}</div>
              </div>
            ))}
            <div style={{ ...s.card, borderColor: '#30363d', flex: 2 }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#e6edf3' }}>
                {summary.reduce((a, b) => a + b.count, 0)}
              </div>
              <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>전체</div>
            </div>
          </div>

          {/* 이상 유형 파이차트 */}
          {!selected && types.length > 0 && (
            <div style={s.chartBox}>
              <div style={s.chartTitle}>이상 유형 분포</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <PieChart width={130} height={130}>
                  <Pie data={types} dataKey="count" nameKey="type" cx="50%" cy="50%" outerRadius={55} innerRadius={28}>
                    {types.map(t => <Cell key={t.type} fill={TYPE_COLORS[t.type] ?? '#6e7681'}/>)}
                  </Pie>
                  <Tooltip {...tooltip} formatter={(v, n) => [v + '건', TYPE_LABEL[n] ?? n]}/>
                </PieChart>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5, flex: 1 }}>
                  {types.map(t => {
                    const total_count = types.reduce((a, b) => a + b.count, 0)
                    const pct = total_count ? ((t.count / total_count) * 100).toFixed(1) : 0
                    return (
                      <div key={t.type} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: TYPE_COLORS[t.type] ?? '#6e7681', flexShrink: 0 }}/>
                        <span style={{ color: '#e6edf3', flex: 1 }}>{TYPE_LABEL[t.type] ?? t.type}</span>
                        <span style={{ color: '#8b949e' }}>{t.count}건</span>
                        <span style={{ color: '#6e7681' }}>({pct}%)</span>
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
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="month" tick={{ fontSize: 9, fill: '#8b949e' }} tickLine={false}/>
                  <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}/>
                  <Tooltip {...tooltip}/>
                  <Legend wrapperStyle={{ fontSize: 11, color: '#8b949e' }}/>
                  <Bar dataKey="HIGH"   name="HIGH"   stackId="a" fill={SEV_COLOR.HIGH}/>
                  <Bar dataKey="MEDIUM" name="MEDIUM" stackId="a" fill={SEV_COLOR.MEDIUM}/>
                  <Bar dataKey="LOW"    name="LOW"    stackId="a" fill={SEV_COLOR.LOW} fillOpacity={0.35} radius={[3,3,0,0]}/>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 목록 헤더 */}
          {!loading && total > 0 && (
            <div style={{ fontSize: 12, color: '#8b949e', display: 'flex', justifyContent: 'space-between' }}>
              <span>총 {total.toLocaleString()}건</span>
              <span>{offset + 1}–{Math.min(offset + PAGE, total)} 표시</span>
            </div>
          )}

          {/* 목록 */}
          <div style={s.list}>
            {loading && [1,2,3,4,5].map(i => (
              <div key={i} style={{ ...s.item, cursor: 'default' }}>
                <div className="skeleton" style={{ height: 18, width: '40%', marginBottom: 8 }}/>
                <div className="skeleton" style={{ height: 13, width: '80%' }}/>
              </div>
            ))}
            {!loading && items.length === 0 && <div style={s.empty}>탐지된 이상 없음</div>}
            {items.map(item => (
              <div key={item.id}
                style={{ ...s.item, ...(selected?.id === item.id ? s.itemActive : {}) }}
                onClick={() => setSelected(selected?.id === item.id ? null : item)}
              >
                <div style={s.itemTop}>
                  <span style={{ ...s.sevBadge, background: SEV_COLOR[item.severity] ?? '#555' }}>
                    {item.severity}
                  </span>
                  <span style={s.typeLabel}>{TYPE_LABEL[item.anomaly_type] ?? item.anomaly_type}</span>
                  <span style={s.ts}>{item.timestamp?.slice(0,16).replace('T',' ')}</span>
                </div>
                <div style={s.desc}>{item.description}</div>
                {!selected && (
                  <div style={s.scores}>
                    통계 {item.score_stat?.toFixed(1) ?? '–'} &nbsp;·&nbsp;
                    IsoForest {item.score_iso?.toFixed(3) ?? '–'} &nbsp;·&nbsp;
                    LSTM {typeof item.score_lstm === 'number' ? item.score_lstm.toFixed(3) : '–'}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* 페이지네이션 */}
          {total > PAGE && (
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', paddingTop: 4 }}>
              <button style={s.pageBtn} disabled={offset === 0}
                onClick={() => { setOffset(Math.max(0, offset - PAGE)); setSelected(null) }}>← 이전</button>
              <span style={{ fontSize: 12, color: '#8b949e', alignSelf: 'center' }}>
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
  header:     { padding: '16px 20px 12px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 },
  title:      { fontWeight: 600, fontSize: 15, color: '#e6edf3' },
  select:     { background: '#161b22', border: '1px solid #30363d', borderRadius: 6, color: '#e6edf3', padding: '4px 8px', fontSize: 13, cursor: 'pointer' },
  body:       { flex: 1, overflow: 'hidden', display: 'flex', gap: 0 },
  left:       { overflowY: 'auto', padding: '12px 20px 20px', display: 'flex', flexDirection: 'column', gap: 12, transition: 'width .2s', borderRight: '1px solid #21262d' },
  right:      { flex: 1, overflowY: 'auto', padding: '16px 20px' },
  cards:      { display: 'flex', gap: 10, flexShrink: 0 },
  card:       { flex: 1, background: '#161b22', border: '1px solid', borderRadius: 8, padding: '10px 14px', textAlign: 'center' },
  chartBox:   { background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '14px 16px', flexShrink: 0 },
  chartTitle: { fontSize: 12, fontWeight: 600, color: '#8b949e', marginBottom: 10 },
  list:       { display: 'flex', flexDirection: 'column', gap: 8 },
  empty:      { textAlign: 'center', color: '#8b949e', paddingTop: 20 },
  item:       { background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '10px 14px', cursor: 'pointer', transition: 'border-color .15s' },
  itemActive: { borderColor: '#58a6ff', background: '#1f2937' },
  itemTop:    { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 },
  sevBadge:   { fontSize: 11, padding: '1px 6px', borderRadius: 4, color: '#0d1117', fontWeight: 700, flexShrink: 0 },
  typeLabel:  { fontSize: 13, fontWeight: 600, color: '#e6edf3' },
  ts:         { marginLeft: 'auto', fontSize: 11, color: '#8b949e', fontVariantNumeric: 'tabular-nums' },
  desc:       { fontSize: 12, color: '#8b949e', marginBottom: 4 },
  scores:     { fontSize: 11, color: '#6e7681' },

  // 상세 패널
  detail:       { display: 'flex', flexDirection: 'column', gap: 12 },
  detailHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  detailType:   { fontSize: 15, fontWeight: 600, color: '#e6edf3', marginLeft: 8 },
  detailTs:     { fontSize: 12, color: '#8b949e' },
  detailDesc:   { fontSize: 13, color: '#e6edf3', lineHeight: 1.6, background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '10px 14px' },
  scoreRow:     { display: 'flex', flexWrap: 'wrap', gap: 6 },
  scoreChip:    { fontSize: 11, padding: '3px 8px', borderRadius: 4, background: '#161b22', border: '1px solid #30363d', color: '#8b949e' },
  chartSection: { display: 'flex', flexDirection: 'column', gap: 10 },
  sectionTitle: { fontSize: 12, fontWeight: 600, color: '#8b949e', marginBottom: 4 },
  chartEmpty:   { color: '#8b949e', fontSize: 13, paddingTop: 20, textAlign: 'center' },
  miniChart:    { background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '10px 14px' },
  miniChartTitle: { fontSize: 11, color: '#8b949e', marginBottom: 6 },
  closeBtn:     { background: 'none', border: 'none', color: '#6e7681', cursor: 'pointer', fontSize: 16, padding: '2px 6px' },
  pageBtn:      { padding: '5px 12px', background: '#161b22', border: '1px solid #30363d', borderRadius: 6, color: '#e6edf3', fontSize: 12, cursor: 'pointer' },
  aiSection:    { display: 'flex', flexDirection: 'column', gap: 8 },
  aiBtn:        { padding: '9px 16px', background: '#1f6feb', border: 'none', borderRadius: 8, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 13, width: '100%' },
  aiText:       { fontSize: 13, color: '#e6edf3', lineHeight: 1.7, background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: '12px 14px', whiteSpace: 'pre-wrap' },
}
