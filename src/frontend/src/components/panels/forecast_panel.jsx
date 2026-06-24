import { useState, useEffect, useRef } from 'react'
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid, Legend,
  ReferenceLine, Cell,
} from 'recharts'
import { Clock, TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle2, SlidersHorizontal } from 'lucide-react'
import { predictModel, getTotalDemandForecast } from '../../api/client'

const METER_GROUPS = [
  {
    label: '전기 — 대표 계량기',
    meters: [
      { urn: 'H2.Z66',   desc: 'C1 클러스터 대표' },
      { urn: 'H2.ZE66',  desc: 'C2 클러스터 대표' },
      { urn: 'H1.Z12',   desc: 'C3 클러스터 대표' },
      { urn: 'H4.Z51',   desc: 'C4 클러스터 대표' },
      { urn: 'H2.T.Z31', desc: 'C5 클러스터 대표' },
      { urn: 'H1.Z13',   desc: 'C6 클러스터 대표' },
      { urn: 'H1.Z21',   desc: 'C7 클러스터 대표' },
      { urn: 'H1.Z24',   desc: 'C8 클러스터 대표' },
      { urn: 'H2.Z64',   desc: 'C9 클러스터 대표' },
      { urn: 'H3.Z43',   desc: 'C10 조건부 대표' },
      { urn: 'H3.Z44',   desc: 'C11 조건부 대표' },
      { urn: 'H3.Z48',   desc: 'C12 클러스터 대표' },
      { urn: 'H4.Z50',   desc: 'C13 클러스터 대표' },
      { urn: 'V.Z84',    desc: 'P1 생산 클러스터 대표' },
      { urn: 'H1.Z20',   desc: 'P2 생산 클러스터 대표' },
    ],
  },
  {
    label: '전기 — 독립 계량기',
    meters: [
      { urn: 'H1.Z10' }, { urn: 'H1.Z16' }, { urn: 'H1.Z18' }, { urn: 'H1.Z19' },
      { urn: 'H1.Z23' }, { urn: 'H1.Z26' }, { urn: 'H1.Z27' }, { urn: 'H2.Z61' },
      { urn: 'H2.Z62' }, { urn: 'H2.Z63' }, { urn: 'H2.Z65' }, { urn: 'H2.Z68' },
      { urn: 'H2.Z69' }, { urn: 'H2.ZE65' }, { urn: 'H2.ZE74' }, { urn: 'H3.Z42' },
      { urn: 'H3.Z45' }, { urn: 'H3.Z46' }, { urn: 'H3.Z47' }, { urn: 'H3.Z71' },
      { urn: 'H2.Z311' },
    ],
  },
  {
    label: '열/냉방',
    meters: [
      { urn: 'V.K21',  desc: '냉방 통합 (CM1+2+3)' },
      { urn: 'H1.K11', desc: '실험실 HVAC 3/5' },
      { urn: 'H1.K12', desc: '실험실 HVAC 1/2' },
      { urn: 'H1.K14', desc: '실험실→오피스' },
      { urn: 'H1.K15', desc: '냉방' },
      { urn: 'H1.K16', desc: '서버룸 O1' },
      { urn: 'H2.K21', desc: '오피스 HVAC' },
      { urn: 'H1.W11', desc: '총 열 생산' },
      { urn: 'H1.W12', desc: 'CHP 열 생산' },
    ],
  },
]
const ALL_METERS = METER_GROUPS.flatMap(g => g.meters)

const tt = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: 'var(--text)' },
}

const DOW_KO = ['월', '화', '수', '목', '금', '토', '일']

function StatCard({ label, value, sub, color, icon }) {
  return (
    <div style={{ padding: '12px 16px', background: 'var(--bg)', borderRadius: 10,
      border: '1px solid var(--line)', display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ fontSize: 10, color: 'var(--text4)', textTransform: 'uppercase',
        display: 'flex', alignItems: 'center', gap: 4 }}>
        {icon}{label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: color ?? 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--text4)' }}>{sub}</div>}
    </div>
  )
}

function AccuracyBadge({ mae, mape, exportCount }) {
  if (mae == null) return null
  const good  = mape != null && mape < 15
  const warn  = mape != null && mape >= 15 && mape < 30
  const color = mape == null ? '#d29922' : good ? '#3fb950' : warn ? '#d29922' : '#f85149'
  const Icon  = good ? CheckCircle2 : AlertTriangle
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '4px 10px',
        background: color + '18', border: '1px solid ' + color + '44', borderRadius: 6,
        fontSize: 11, color, fontWeight: 600 }}>
        <Icon size={11}/>
        오차 ±{mae.toFixed(1)} kW{mape != null ? ' (MAPE ' + mape.toFixed(1) + '%)' : ''}
      </div>
      {exportCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px',
          background: '#3fb95018', border: '1px solid #3fb95044', borderRadius: 6,
          fontSize: 11, color: '#3fb950', fontWeight: 600 }}>
          {'↑ 역전류 ' + exportCount + '시간 (PV·CHP 수출 구간, MAPE 제외)'}
        </div>
      )}
    </div>
  )
}

/* ── 부지 전체 수요 예측 뷰 ─────────────────────────── */
function TotalDemandView({ onNavigate }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  useEffect(() => {
    setLoading(true); setError(''); setData(null)
    getTotalDemandForecast(30)
      .then(r => setData(r.data))
      .catch(e => setError(e.response?.data?.error ?? e.message ?? '데이터 로드 실패'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={s.centerMsg}>전체 수요 예측 데이터를 불러오는 중...</div>
  if (error)   return <div style={s.errBox}>{error}</div>
  if (!data)   return null

  // 예측 데이터만 차트에 표시 (실적은 정확도 계산에만 사용)
  const chartData = (data.forecast ?? []).map(r => ({
    ts:  r.ts,
    예측: r.yhat_kw,
  }))

  // 예측 정확도 계산 (actual vs hindcast)
  // 수출 구간(actual_kw <= 0)은 MAPE 계산에서 제외 — 분모가 0에 가까워 수치 폭발
  const allPaired    = (data.actual ?? []).filter(r => r.actual_kw != null && r.yhat_kw != null)
  const paired       = allPaired.filter(r => r.actual_kw > 0)   // 수입 구간만
  const exportCount  = allPaired.filter(r => r.actual_kw <= 0).length
  const mae  = allPaired.length ? allPaired.reduce((s, r) => s + Math.abs(r.actual_kw - r.yhat_kw), 0) / allPaired.length : null
  const mape = paired.length
    ? paired.reduce((s, r) => s + Math.abs(r.actual_kw - r.yhat_kw) / r.actual_kw, 0) / paired.length * 100
    : null

  // 전일 대비 표시
  const delta    = data.day_delta_pct
  const DeltaIcon = delta == null ? Minus : delta > 0 ? TrendingUp : TrendingDown
  const deltaColor = delta == null ? 'var(--text4)' : delta > 5 ? '#f85149' : delta < -5 ? '#3fb950' : '#d29922'

  // 피크 시각
  const peakTime = data.peak_ts ? data.peak_ts.slice(11, 16) : null

  // 요일별 패턴 — 오늘 요일 하이라이트
  const weekly = data.weekly_pattern ?? []
  const anchorDow = data.anchor ? new Date(data.anchor).getDay() : -1  // 0=일 → JS
  // JS: 0=일, Python: 0=월 → 변환
  const todayDow = anchorDow === 0 ? 6 : anchorDow - 1  // JS→Python dow

  return (
    <>
      {/* ── stat 카드 행 ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', marginBottom: 4 }}>
        <button onClick={() => onNavigate?.('control')}
          style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 12px',
            background: 'transparent', border: '1px solid #a371f755', borderRadius: 6,
            color: '#a371f7', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
          <SlidersHorizontal size={11}/>피크 대응 권고 보기 →
        </button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
        <StatCard
          label="예측 피크"
          value={`${data.peak_kw?.toFixed(1)} kW`}
          sub={peakTime ? `예상 시각 ${peakTime}` : undefined}
          color="#f85149"
          icon={<TrendingUp size={10}/>}
        />
        <StatCard
          label="예측 평균"
          value={`${data.fc_avg?.toFixed(1) ?? data.summary?.forecast_avg_kw?.toFixed(1)} kW`}
          sub={`향후 24시간 평균 · 학습기준 ${data.summary?.avg_kw} kW`}
          color="#a371f7"
          icon={<Minus size={10}/>}
        />
        <StatCard
          label="전일 대비"
          value={delta != null ? `${delta > 0 ? '+' : ''}${delta}%` : '—'}
          sub={`전일 평균 ${data.prev_24h_avg ?? '—'} kW`}
          color={deltaColor}
          icon={<DeltaIcon size={10}/>}
        />
        <StatCard
          label="예측 최저"
          value={`${Math.min(...(data.forecast ?? []).map(r => r.yhat_kw)).toFixed(1)} kW`}
          color="#3fb950"
          icon={<TrendingDown size={10}/>}
        />
        <StatCard
          label="학습 기간"
          value={`${data.basis_days}일`}
          sub="요일×시간 패턴 기반"
          icon={<Clock size={10}/>}
        />
      </div>

      {/* 정확도 배지 */}
      {mae != null && (
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <AccuracyBadge mae={mae} mape={mape} exportCount={exportCount} />
        </div>
      )}

      {/* ── 메인 차트 ── */}
      <div style={s.chartBox}>
        <div style={s.chartTitle}>
          부지 전체 수전 전력 — 향후 24시간 예측
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
            <XAxis dataKey="ts" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
              tickFormatter={v => v?.slice(11, 16)} interval={3} />
            <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
              tickFormatter={v => `${v.toFixed(0)}kW`} width={52} />
            <Tooltip {...tt} labelFormatter={v => v}
              formatter={(v, name) => [`${v?.toFixed(1)} kW`, name]} />
            <Line type="monotone" dataKey="예측" stroke="#a371f7" strokeWidth={2.5}
              dot={{ r: 3, fill: '#a371f7', strokeWidth: 0 }} connectNulls />
            {peakTime && data.peak_ts && (
              <ReferenceLine x={data.peak_ts} stroke="#f85149" strokeDasharray="4 2"
                label={{ value: `피크 ${peakTime}`, fill: '#f85149', fontSize: 10, position: 'insideTopRight' }} />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* ── 요일별 평균 소비 패턴 ── */}
      {weekly.length > 0 && (
        <div style={s.chartBox}>
          <div style={s.chartTitle}>
            요일별 평균 수전 전력
            <span style={{ fontWeight: 400, fontSize: 11, color: 'var(--text4)', marginLeft: 8 }}>
              최근 {data.basis_days}일 기준 · 음영 = 오늘 요일
            </span>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={weekly} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#5a6675' }} tickLine={false} />
              <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
                tickFormatter={v => `${v}kW`} width={48} />
              <Tooltip {...tt} formatter={(v, _) => [`${v} kW`, '평균 수전']} />
              <Bar dataKey="avg_kw" name="평균 수전" radius={[4, 4, 0, 0]}>
                {weekly.map((entry, idx) => (
                  <Cell key={idx}
                    fill={idx === todayDow ? '#a371f7' : '#58a6ff'}
                    fillOpacity={idx === todayDow ? 0.9 : 0.55}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </>
  )
}

/* ── 개별 계량기 뷰 ─────────────────────────────────── */
function MeterView({ meterUrn, horizon, meterDesc }) {
  const [forecast, setForecast] = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')
  const abortRef = useRef(null)

  useEffect(() => {
    if (abortRef.current) abortRef.current.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setLoading(true); setError(''); setForecast(null)
    predictModel('v84-ensemble', meterUrn, horizon)
      .then(r => {
        if (ctrl.signal.aborted) return
        setForecast({ data: r.data.forecast ?? [], meter: r.data.meter_urn, horizon: r.data.horizon })
      })
      .catch(e => { if (!ctrl.signal.aborted) setError(e.response?.data?.error ?? e.message ?? '예측 실패') })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
  }, [meterUrn, horizon])

  if (loading) return <div style={s.centerMsg}>예측 데이터를 불러오는 중...</div>
  if (error)   return <div style={s.errBox}>{error}</div>
  if (!forecast?.data?.length) return <div style={s.centerMsg}>예측 결과가 없습니다. 모델 학습이 필요할 수 있습니다.</div>

  const values = forecast.data.map(r => r.yhat_kw).filter(v => v != null)
  const peak   = Math.max(...values)
  const avg    = values.reduce((a, b) => a + b, 0) / values.length
  const min    = Math.min(...values)
  const peakItem = forecast.data.find(r => r.yhat_kw === peak)
  const peakTime = peakItem?.ts?.slice(11, 16)

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        <StatCard label="예측 피크" value={`${peak.toFixed(1)} kW`}
          sub={peakTime ? `예상 시각 ${peakTime}` : undefined}
          color="#f85149" icon={<TrendingUp size={10}/>} />
        <StatCard label="예측 평균" value={`${avg.toFixed(1)} kW`} color="#a371f7" icon={<Minus size={10}/>} />
        <StatCard label="예측 최저" value={`${min.toFixed(1)} kW`} color="#3fb950" icon={<TrendingDown size={10}/>} />
        <StatCard label="예측 구간" value={`${horizon}시간`}
          sub={`${forecast.data[0]?.ts?.slice(11,16)} ~ ${forecast.data.at(-1)?.ts?.slice(11,16)}`}
          icon={<Clock size={10}/>} />
      </div>
      <div style={s.chartBox}>
        <div style={s.chartTitle}>
          {meterUrn}{meterDesc && <span style={{ fontWeight: 400, color: 'var(--text3)', marginLeft: 6 }}>— {meterDesc}</span>}
          <span style={{ fontWeight: 400, color: 'var(--text4)', marginLeft: 8, fontSize: 11 }}>향후 {horizon}시간 예측</span>
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={forecast.data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
            <XAxis dataKey="ts" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
              tickFormatter={v => v?.slice(11, 16)} />
            <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
              tickFormatter={v => `${v.toFixed(0)}kW`} width={52} />
            <Tooltip {...tt} labelFormatter={v => v} formatter={v => [`${v?.toFixed(1)} kW`, '예측값']} />
            {peakItem && <ReferenceLine x={peakItem.ts} stroke="#f85149" strokeDasharray="4 2"
              label={{ value: `피크 ${peakTime}`, fill: '#f85149', fontSize: 10, position: 'insideTopRight' }} />}
            <Line type="monotone" dataKey="yhat_kw" name="예측값"
              stroke="#a371f7" strokeWidth={2.5}
              dot={{ r: 3, fill: '#a371f7', strokeWidth: 0 }}
              activeDot={{ r: 6 }} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}

/* ── 메인 패널 ───────────────────────────────────────── */
export default function ForecastPanel({ onNavigate } = {}) {
  const [mode,     setMode]     = useState('total')
  const [horizon,  setHorizon]  = useState(1)
  const [meterUrn, setMeterUrn] = useState('H2.Z66')

  const meterDesc = ALL_METERS.find(m => m.urn === meterUrn)?.desc ?? ''

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <span style={s.title}>수요 예측</span>
        <div style={s.tabs}>
          {[['total','부지 전체'], ['meter','개별 계량기']].map(([v, l]) => (
            <button key={v} onClick={() => setMode(v)}
              style={{ ...s.tab, ...(mode === v ? s.tabActive : {}) }}>{l}</button>
          ))}
        </div>
        {mode === 'meter' && (
          <>
            <div style={s.tabs}>
              {[1, 3, 24].map(h => (
                <button key={h} onClick={() => setHorizon(h)}
                  style={{ ...s.tab, ...(horizon === h ? s.tabActive : {}) }}>{h === 24 ? '24시간' : `${h}시간 후`}</button>
              ))}
            </div>
            <select value={meterUrn} onChange={e => setMeterUrn(e.target.value)} style={s.select}>
              {METER_GROUPS.map(g => (
                <optgroup key={g.label} label={g.label}>
                  {g.meters.map(m => (
                    <option key={m.urn} value={m.urn}>{m.urn}{m.desc ? ` — ${m.desc}` : ''}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </>
        )}
      </div>

      <div style={s.body}>
        {mode === 'total' && <TotalDemandView onNavigate={onNavigate} />}
        {mode === 'meter' && <MeterView meterUrn={meterUrn} horizon={horizon} meterDesc={meterDesc} />}
      </div>
    </div>
  )
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:     { padding: '12px 20px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' },
  title:      { fontWeight: 700, fontSize: 15, color: 'var(--text)', marginRight: 4 },
  tabs:       { display: 'flex', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, padding: 3, gap: 3 },
  tab:        { padding: '5px 16px', border: 'none', borderRadius: 6, background: 'transparent', color: 'var(--text3)', fontSize: 12, fontWeight: 600, cursor: 'pointer' },
  tabActive:  { background: 'var(--surface)', color: '#a371f7', boxShadow: '0 1px 3px rgba(0,0,0,.1)' },
  select:     { marginLeft: 'auto', padding: '5px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', maxWidth: 300 },
  body:       { flex: 1, overflowY: 'auto', padding: '16px 24px', display: 'flex', flexDirection: 'column', gap: 14 },
  chartBox:   { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 18px' },
  chartTitle: { fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 12 },
  errBox:     { background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#f85149' },
  centerMsg:  { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text4)', fontSize: 13 },
}
