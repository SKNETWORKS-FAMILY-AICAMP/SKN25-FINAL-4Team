import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine,
} from 'recharts'
import { getForecastModels, getForecastStatus, trainModel, predictModel } from '../../api/client'

// 계량기 목록 (v84 파이프라인 대상 45개 — 선택 UI용 그룹화)
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

const tooltip = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: 'var(--text)' },
}

function StatusBadge({ status }) {
  const isRunning = status === 'running' || status?.startsWith('running')
  const color = status === 'done' ? '#3fb950'
    : isRunning ? '#d29922'
    : status?.startsWith('error') ? '#f85149'
    : 'var(--text4)'
  const label = status === 'done' ? '학습 완료'
    : isRunning ? '학습 중...'
    : status?.startsWith('error') ? '오류'
    : '미학습'
  return (
    <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 4, background: color + '22', color, border: `1px solid ${color}44` }}>
      {label}
    </span>
  )
}

export default function ForecastPanel() {
  const [modelDef,   setModelDef]   = useState(null)
  const [status,     setStatus]     = useState({})   // { 'v84-1h': 'idle'|'running'|'done', 'v84-3h': ... }
  const [horizon,    setHorizon]    = useState(1)
  const [meterUrn,   setMeterUrn]   = useState('H2.Z66')
  const [forecast,   setForecast]   = useState(null)
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState('')
  const [training,   setTraining]   = useState(false)
  const [trainMsg,   setTrainMsg]   = useState('')

  // 모델 메타데이터 1회 로드
  useEffect(() => {
    getForecastModels()
      .then(r => {
        const m = (r.data.models ?? []).find(m => m.name === 'v84-ensemble')
        if (m) setModelDef(m)
      })
      .catch(() => {})
  }, [])

  // 학습 상태 폴링 (10s)
  useEffect(() => {
    const poll = () =>
      getForecastStatus()
        .then(r => setStatus(r.data.status ?? {}))
        .catch(() => {})
    poll()
    const id = setInterval(poll, 10000)
    return () => clearInterval(id)
  }, [])

  const currentStatus = status[`v84-${horizon}h`] ?? 'idle'
  const isAvailable   = currentStatus === 'done' || modelDef?.available

  const handleTrain = async () => {
    setTraining(true); setTrainMsg('')
    try {
      await trainModel('v84-ensemble', horizon)
      const isRetrain = currentStatus === 'done'
      setTrainMsg(
        isRetrain
          ? `${horizon}h 재학습이 시작되었습니다. pass1(LSTM) 스킵으로 수 분 내 완료 예정입니다.`
          : `${horizon}h 학습이 시작되었습니다. 완료까지 20~40분 소요됩니다 (LSTM 6종 학습 포함).`
      )
      setStatus(s => ({ ...s, [`v84-${horizon}h`]: 'running' }))
    } catch (e) {
      setTrainMsg('학습 요청 실패: ' + (e.message ?? ''))
    } finally {
      setTraining(false)
    }
  }

  const handlePredict = async () => {
    setLoading(true); setError(''); setForecast(null)
    try {
      const r = await predictModel('v84-ensemble', meterUrn, horizon)
      const rows = r.data.forecast ?? []
      setForecast({ data: rows, meter: r.data.meter_urn, horizon: r.data.horizon })
    } catch (e) {
      const msg = e.response?.data?.error ?? e.message ?? '예측 실패'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const meterDesc = ALL_METERS.find(m => m.urn === meterUrn)?.desc ?? ''

  return (
    <div style={s.wrap}>
      {/* ── 헤더 ── */}
      <div style={s.header}>
        <span style={s.title}>계량기별 전력 예측</span>
        <span style={s.sub}>v84 앙상블 — LSTM × 6 + CatBoost + LightGBM + Ridge + Naive</span>
      </div>

      <div style={s.body}>

        {/* ── 모델 상태 카드 ── */}
        <div style={s.cards}>
          {[1, 3].map(h => {
            const st = status[`v84-${h}h`] ?? 'idle'
            return (
              <div key={h} style={{ ...s.card, ...(horizon === h ? s.cardActive : {}) }}
                onClick={() => setHorizon(h)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#a371f7' }}>{h}시간 예측</span>
                  <StatusBadge status={st} />
                </div>
                <div style={{ fontSize: 11, color: 'var(--text4)' }}>
                  t+1{h > 1 ? ` ~ t+${h}` : ''} (1h 간격) · 45개 계량기
                </div>
                {modelDef?.badges?.map(b => (
                  <span key={b.text} style={{ display: 'inline-block', marginTop: 6, fontSize: 10, color: b.color, background: b.bg, borderRadius: 4, padding: '2px 6px' }}>
                    {b.text}
                  </span>
                ))}
              </div>
            )
          })}
        </div>

        {/* ── 컨트롤 ── */}
        <div style={s.controls}>
          {/* 계량기 선택 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text3)', whiteSpace: 'nowrap' }}>계량기:</span>
            <select value={meterUrn} onChange={e => setMeterUrn(e.target.value)} style={s.select}>
              {METER_GROUPS.map(g => (
                <optgroup key={g.label} label={g.label}>
                  {g.meters.map(m => (
                    <option key={m.urn} value={m.urn}>
                      {m.urn}{m.desc ? ` — ${m.desc}` : ''}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {/* 버튼 */}
          <div style={{ display: 'flex', gap: 8 }}>
            <button style={{ ...s.trainBtn, opacity: training ? 0.6 : 1 }}
              onClick={handleTrain} disabled={training}>
              {training ? '요청 중...' : currentStatus === 'done' ? `${horizon}h 재학습` : `${horizon}h 학습 시작`}
            </button>
            <button style={{ ...s.predictBtn, opacity: loading || !isAvailable ? 0.5 : 1 }}
              onClick={handlePredict} disabled={loading || !isAvailable}>
              {loading ? '예측 중...' : !isAvailable ? '학습 필요' : '예측 실행'}
            </button>
          </div>
        </div>

        {/* 안내 메시지 */}
        {trainMsg && (
          <div style={{ fontSize: 12, padding: '8px 12px', borderRadius: 8,
            ...(trainMsg.includes('실패') || trainMsg.includes('오류')
              ? { background: '#fee2e2', border: '1px solid #f85149', color: '#f85149' }
              : { background: '#dcfce7', border: '1px solid #16a34a', color: '#16a34a' }) }}>
            {trainMsg}
          </div>
        )}

        {error && <div style={s.errBox}>{error}</div>}

        {/* ── 예측 차트 ── */}
        {forecast?.data?.length > 0 && (
          <div style={s.chartBox}>
            <div style={s.chartTitle}>
              {forecast.meter} — 향후 {forecast.horizon}시간 예측 (W)
              {meterDesc && <span style={{ fontWeight: 400, color: 'var(--text3)', marginLeft: 8 }}>{meterDesc}</span>}
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={forecast.data} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                <XAxis dataKey="ts" tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false}
                  tickFormatter={v => v?.slice(11, 16)} />
                <YAxis tick={{ fontSize: 10, fill: '#5a6675' }} tickLine={false} axisLine={false}
                  tickFormatter={v => `${v.toFixed(1)}kW`} />
                <Tooltip {...tooltip}
                  labelFormatter={v => v}
                  formatter={(v) => [`${v.toFixed(3)} kW`, '예측값']} />
                <Line type="monotone" dataKey="yhat_kw" name="yhat_kw"
                  stroke="#a371f7" strokeWidth={2} dot={{ r: 4, fill: '#a371f7' }} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* 미학습 안내 */}
        {!isAvailable && !forecast && (
          <div style={s.guide}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>🤖</div>
            <div style={{ fontSize: 14, color: 'var(--text)', marginBottom: 6 }}>
              {horizon}h 모델 학습이 필요합니다
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.7 }}>
              45개 계량기 × LSTM 6종 + CatBoost + LightGBM + Ridge + Naive<br />
              학습 완료 후 계량기별 1~3시간 예측이 가능합니다.<br />
              <code style={{ background: 'var(--line)', padding: '2px 6px', borderRadius: 4, fontSize: 11 }}>
                .venv-train/bin/python -m ml.pipeline.train --horizon {horizon}
              </code>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  header:     { padding: '16px 24px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 },
  title:      { fontWeight: 700, fontSize: 15, color: 'var(--text)' },
  sub:        { fontSize: 12, color: 'var(--text3)' },
  body:       { flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 16 },
  cards:      { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  card:       { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px', cursor: 'pointer', transition: 'border-color .15s' },
  cardActive: { borderColor: '#a371f7', boxShadow: '0 0 0 2px #a371f722' },
  controls:   { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 },
  select:     { padding: '5px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', maxWidth: 280 },
  trainBtn:   { padding: '7px 16px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontWeight: 600, cursor: 'pointer', fontSize: 13 },
  predictBtn: { padding: '7px 20px', background: '#a371f7', border: 'none', borderRadius: 8, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 13 },
  chartBox:   { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: '16px 18px' },
  chartTitle: { fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 12 },
  errBox:     { background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: '#f85149' },
  guide:      { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', paddingTop: 40, textAlign: 'center' },
}
