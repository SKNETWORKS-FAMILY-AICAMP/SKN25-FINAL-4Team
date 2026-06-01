import { useEffect, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { Factory, RefreshCw } from 'lucide-react'
import { EquipIcon } from '../EquipIcon'
import { getEquipmentStatus, diagnoseEquipment, createWorkOrder, getPredictiveMaintenance } from '../api/client'

const PRIORITY_BY_STATUS = { 경고: 'HIGH', 주의: 'MEDIUM', 정상: 'LOW' }
const RISK_STYLE = {
  높음: { color: '#f85149', bg: '#f8514918' },
  보통: { color: '#d29922', bg: '#d2992218' },
  낮음: { color: '#3fb950', bg: '#3fb95018' },
}
const DIR_MARK = { 악화: '▲', 개선: '▼', 안정: '▬', '데이터 부족': '–' }

// 미니 스파크라인 (SVG)
function Sparkline({ series, color }) {
  const vals = (series ?? []).map(p => p.value)
  if (vals.length < 2) return <div style={{ width: 80, height: 24 }} />
  const min = Math.min(...vals), max = Math.max(...vals)
  const rng = max - min || 1
  const W = 80, H = 24
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W
    const y = H - ((v - min) / rng) * (H - 4) - 2
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg width={W} height={H} style={{ flexShrink: 0 }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

function PredictiveSection({ items }) {
  if (!items || items.length === 0) return null
  return (
    <div style={s.predWrap}>
      <div style={s.predTitle}>🔮 예지보전 — 추세 기반 위험 예측 <span style={{ fontSize: 10, color: 'var(--text4)', fontWeight: 400 }}>(현 추세 외삽 · 참고용)</span></div>
      <div style={s.predList}>
        {items.map(it => {
          const rk = RISK_STYLE[it.risk] ?? RISK_STYLE.낮음
          return (
            <div key={it.id} style={s.predRow}>
              <span style={{ width: 22, display: 'inline-flex', justifyContent: 'center' }}><EquipIcon id={it.id} size={18} /></span>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', width: 88 }}>{it.name}</span>
              <span style={{ ...s.riskBadge, color: rk.color, background: rk.bg, border: `1px solid ${rk.color}55` }}>
                {DIR_MARK[it.direction] ?? ''} 위험 {it.risk}
              </span>
              <Sparkline series={it.series} color={rk.color} />
              <span style={{ fontSize: 11, color: it.projection ? '#f0b429' : 'var(--text3)', flex: 1, minWidth: 0 }}>
                {it.note}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// 진단 텍스트(마크다운) 렌더 스타일 — 모달 내부용
const MD = {
  h3: ({ children }) => <div style={{ fontSize: 13, fontWeight: 700, color: '#2563eb', marginTop: 14, marginBottom: 6 }}>{children}</div>,
  p:  ({ children }) => <p style={{ margin: '0 0 8px', fontSize: 13, lineHeight: 1.7, color: 'var(--text2)' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '4px 0 8px', paddingLeft: 18, color: 'var(--text2)' }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '4px 0 8px', paddingLeft: 18, color: 'var(--text2)' }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 4, fontSize: 13, lineHeight: 1.6 }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700, color: 'var(--text-strong)' }}>{children}</strong>,
  em: ({ children }) => <em style={{ color: 'var(--text3)' }}>{children}</em>,
}

const STATUS_STYLE = {
  정상: { color: '#3fb950', bg: '#3fb95018', label: '정상' },
  주의: { color: '#d29922', bg: '#d2992218', label: '주의' },
  경고: { color: '#f85149', bg: '#f8514918', label: '경고' },
}

function relTime(iso) {
  if (!iso) return '이상 없음'
  const ms  = Date.now() - new Date(iso).getTime()
  const day = Math.floor(ms / 86400000)
  if (day > 0)  return `${day}일 전`
  const hr  = Math.floor(ms / 3600000)
  if (hr > 0)   return `${hr}시간 전`
  const min = Math.floor(ms / 60000)
  return min > 0 ? `${min}분 전` : '방금'
}

// SVG 링 게이지
function Ring({ score, color }) {
  const r = 34, c = 2 * Math.PI * r
  const off = c * (1 - Math.max(0, Math.min(100, score)) / 100)
  return (
    <svg width="84" height="84" style={{ flexShrink: 0 }}>
      <circle cx="42" cy="42" r={r} fill="none" strokeWidth="7" style={{ stroke: 'var(--line)' }} />
      <circle
        cx="42" cy="42" r={r} fill="none" stroke={color} strokeWidth="7"
        strokeLinecap="round" strokeDasharray={c} strokeDashoffset={off}
        transform="rotate(-90 42 42)"
        style={{ transition: 'stroke-dashoffset .6s cubic-bezier(0.4,0,0.2,1)' }}
      />
      <text x="42" y="40" textAnchor="middle" fontSize="22" fontWeight="700" style={{ fill: 'var(--text)' }}>{score}</text>
      <text x="42" y="55" textAnchor="middle" fontSize="9" style={{ fill: 'var(--text3)' }}>/ 100</text>
    </svg>
  )
}

function EquipmentCard({ item, onClick }) {
  const st = STATUS_STYLE[item.status] ?? STATUS_STYLE.정상
  const c  = item.counts ?? {}
  return (
    <div
      className="hover-lift"
      style={{ ...s.card, borderTop: `3px solid ${st.color}` }}
      onClick={() => onClick?.(item)}
    >
      <div style={s.cardTop}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <EquipIcon id={item.id} size={26} />
          <div style={{ minWidth: 0 }}>
            <div style={s.name}>{item.name}</div>
            <span style={{ ...s.statusBadge, color: st.color, background: st.bg, border: `1px solid ${st.color}55` }}>
              ● {st.label}
            </span>
          </div>
        </div>
        <Ring score={item.health_score} color={st.color} />
      </div>

      <div style={s.metricRow}>
        <span style={s.metricLabel}>{item.metric_label}</span>
        <span style={s.metricValue}>
          {item.metric_value != null ? `${item.metric_value}${item.unit}` : '–'}
        </span>
      </div>

      <div style={s.divider} />

      <div style={s.anomalyRow}>
        <div style={s.anomalyLeft}>
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>최근 30일 이상</span>
          <span style={{ fontSize: 11, color: 'var(--text4)' }}>· 마지막 {relTime(item.last_anomaly_at)}</span>
        </div>
        <div style={s.chips}>
          {c.HIGH   > 0 && <span style={{ ...s.chip, color: '#f85149', background: '#f8514918' }}>심각 {c.HIGH}</span>}
          {c.MEDIUM > 0 && <span style={{ ...s.chip, color: '#d29922', background: '#d2992218' }}>주의 {c.MEDIUM}</span>}
          {c.LOW    > 0 && <span style={{ ...s.chip, color: '#2563eb', background: '#2563eb18' }}>경미 {c.LOW}</span>}
          {(item.anomaly_total ?? 0) === 0 && <span style={{ ...s.chip, color: '#3fb950', background: '#3fb95018' }}>이상 없음</span>}
        </div>
      </div>
    </div>
  )
}

export default function EquipmentPanel({ onEquipmentClick, onWorkOrderCreated, onFocusEquipment } = {}) {
  const [items,   setItems]   = useState([])
  const [anchor,  setAnchor]  = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')
  const [predictive, setPredictive] = useState([])

  const load = useCallback(() => {
    setLoading(true); setError('')
    getEquipmentStatus(30)
      .then(r => {
        if (r.data.error) throw new Error(r.data.error)
        setItems(r.data.items ?? [])
        setAnchor(r.data.anchor)
      })
      .catch(e => setError(e.message ?? '설비 상태 로드 실패'))
      .finally(() => setLoading(false))
    getPredictiveMaintenance(8)
      .then(r => setPredictive(r.data?.items ?? []))
      .catch(() => setPredictive([]))
  }, [])

  useEffect(() => { load() }, [load])

  const [selected,    setSelected]    = useState(null)
  const [diag,        setDiag]        = useState(null)
  const [diagLoading, setDiagLoading] = useState(false)

  const openDiagnosis = useCallback((item) => {
    setSelected(item); setDiag(null); setDiagLoading(true)
    onFocusEquipment?.(item)   // 플로팅 챗봇이 이 설비를 인지하도록
    diagnoseEquipment(item.id)
      .then(r => setDiag(r.data))
      .catch(() => setDiag({ diagnosis: '진단을 불러오지 못했습니다.', llm_used: false }))
      .finally(() => setDiagLoading(false))
  }, [onFocusEquipment])

  const regenDiagnosis = useCallback(() => {
    if (!selected) return
    setDiagLoading(true)
    diagnoseEquipment(selected.id, true)
      .then(r => setDiag(r.data))
      .catch(() => {})
      .finally(() => setDiagLoading(false))
  }, [selected])

  const closeDiagnosis = useCallback(() => { setSelected(null); setDiag(null) }, [])

  const [woState, setWoState] = useState('idle')   // idle | saving | done
  useEffect(() => { setWoState('idle') }, [selected])

  const createWO = useCallback(async () => {
    if (!selected) return
    setWoState('saving')
    try {
      await createWorkOrder({
        equipment_id:   selected.id,
        equipment_name: selected.name,
        title:          `${selected.name} 정비 작업`,
        cause:          `헬스 ${selected.health_score} · 최근 이상 ${selected.anomaly_total ?? 0}건`,
        action:         diag?.diagnosis ?? '',
        priority:       PRIORITY_BY_STATUS[selected.status] ?? 'MEDIUM',
      })
      setWoState('done')
      onWorkOrderCreated?.()
    } catch {
      setWoState('idle')
      alert('작업지시 생성 실패')
    }
  }, [selected, diag, onWorkOrderCreated])

  const counts = items.reduce((a, it) => { a[it.status] = (a[it.status] ?? 0) + 1; return a }, {})

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <div>
          <div style={s.title}><Factory size={18} color="#0d9488" /> 설비 상태 감시</div>
          <div style={s.sub}>
            전기·성능 데이터 기반 설비별 건강 지표
            {anchor && <> · 기준 {anchor.slice(0, 10)}</>}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={s.summary}>
            {counts.경고 > 0 && <span style={{ color: '#f85149' }}>경고 {counts.경고}</span>}
            {counts.주의 > 0 && <span style={{ color: '#d29922' }}>주의 {counts.주의}</span>}
            {counts.정상 > 0 && <span style={{ color: '#3fb950' }}>정상 {counts.정상}</span>}
          </div>
          <button onClick={load} style={s.refreshBtn} disabled={loading}>
            {loading ? '분석 중...' : <><RefreshCw size={13} /> 새로고침</>}
          </button>
        </div>
      </div>

      <div style={s.body}>
        {loading && (
          <div style={s.center}>
            <div style={{ fontSize: 28, marginBottom: 12 }}>⏳</div>
            <div style={{ fontSize: 13, color: 'var(--text3)' }}>설비 상태 분석 중...</div>
          </div>
        )}
        {!loading && error && (
          <div style={s.errorBox}>{error}<button onClick={load} style={s.retryBtn}>다시 시도</button></div>
        )}
        {!loading && !error && (
          <div style={s.grid}>
            {items.map(it => (
              <EquipmentCard key={it.id} item={it} onClick={openDiagnosis} />
            ))}
          </div>
        )}
        {!loading && !error && <PredictiveSection items={predictive} />}

        {!loading && !error && (
          <div style={s.hint}>
            💡 설비 카드를 클릭하면 AI가 고장 원인과 권장 조치를 진단합니다.
          </div>
        )}
      </div>

      {selected && (
        <DiagnosisModal
          item={selected}
          diag={diag}
          loading={diagLoading}
          onRegen={regenDiagnosis}
          onClose={closeDiagnosis}
          onViewAnomalies={onEquipmentClick}
          onCreateWO={createWO}
          woState={woState}
        />
      )}
    </div>
  )
}

function DiagnosisModal({ item, diag, loading, onRegen, onClose, onViewAnomalies, onCreateWO, woState }) {
  const st = STATUS_STYLE[item.status] ?? STATUS_STYLE.정상
  const c  = item.counts ?? {}
  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={e => e.stopPropagation()}>
        <div style={s.modalHeader}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <EquipIcon id={item.id} size={24} />
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>{item.name} · AI 진단</div>
              <span style={{ ...s.statusBadge, color: st.color, background: st.bg, border: `1px solid ${st.color}55` }}>
                ● {st.label} · 헬스 {item.health_score}
              </span>
            </div>
          </div>
          <button onClick={onClose} style={s.closeBtn}>✕</button>
        </div>

        <div style={s.modalSummary}>
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>최근 30일 이상</span>
          {c.HIGH   > 0 && <span style={{ ...s.chip, color: '#f85149', background: '#f8514918' }}>심각 {c.HIGH}</span>}
          {c.MEDIUM > 0 && <span style={{ ...s.chip, color: '#d29922', background: '#d2992218' }}>주의 {c.MEDIUM}</span>}
          {c.LOW    > 0 && <span style={{ ...s.chip, color: '#2563eb', background: '#2563eb18' }}>경미 {c.LOW}</span>}
          {(item.anomaly_total ?? 0) === 0 && <span style={{ ...s.chip, color: '#3fb950', background: '#3fb95018' }}>이상 없음</span>}
        </div>

        {!loading && diag?.electrical_str && (
          <div style={s.elecBar}>⚡ 측정 근거 · {diag.electrical_str}</div>
        )}

        <div style={s.modalBody}>
          {loading && (
            <div style={{ textAlign: 'center', padding: 30, color: 'var(--text3)', fontSize: 13 }}>
              🤖 AI가 데이터를 분석하고 있습니다...
            </div>
          )}
          {!loading && diag && (
            <>
              <ReactMarkdown components={MD}>{diag.diagnosis || ''}</ReactMarkdown>
              <div style={s.llmTag}>
                {diag.llm_used ? '🤖 AI 진단' : '⚙️ 규칙 기반 요약 (LLM 미사용)'}
              </div>
            </>
          )}
        </div>

        <div style={s.modalFooter}>
          <button onClick={onRegen} disabled={loading} style={s.ghostBtn}>🔄 다시 진단</button>
          <div style={{ display: 'flex', gap: 8 }}>
            {onViewAnomalies && (
              <button onClick={() => { onViewAnomalies(item); onClose() }} style={s.ghostBtn}>
                이상 내역 →
              </button>
            )}
            <button
              onClick={onCreateWO}
              disabled={loading || woState !== 'idle'}
              style={{ ...s.primaryBtn, ...(woState === 'done' ? { background: '#238636' } : {}) }}
            >
              {woState === 'saving' ? '생성 중...' : woState === 'done' ? '✓ 작업지시 생성됨' : '🔧 작업지시 생성'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

const s = {
  wrap:   { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header: { padding: '14px 24px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)' },
  title:  { fontWeight: 700, fontSize: 16, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8 },
  sub:    { fontSize: 11, color: 'var(--text3)', marginTop: 3 },
  summary:{ display: 'flex', gap: 12, fontSize: 12, fontWeight: 700 },
  refreshBtn: { padding: '7px 14px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', fontWeight: 600 },

  body:   { flex: 1, overflowY: 'auto', padding: 24 },
  grid:   { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 },
  center: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 60 },
  errorBox: { padding: 20, background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, color: '#f85149', fontSize: 13 },
  retryBtn: { marginLeft: 12, padding: '4px 12px', background: '#f85149', border: 'none', borderRadius: 4, color: '#fff', cursor: 'pointer', fontSize: 12 },
  hint:   { marginTop: 20, fontSize: 11, color: 'var(--text4)', textAlign: 'center' },

  card:    { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 18, cursor: 'pointer' },
  cardTop: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  name:    { fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  statusBadge: { fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4 },

  metricRow:  { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 14 },
  metricLabel:{ fontSize: 12, color: 'var(--text3)' },
  metricValue:{ fontSize: 20, fontWeight: 700, color: '#2563eb', fontVariantNumeric: 'tabular-nums' },

  divider: { height: 1, background: 'var(--line)', margin: '14px 0' },

  anomalyRow:  { display: 'flex', flexDirection: 'column', gap: 8 },
  anomalyLeft: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  chips:   { display: 'flex', gap: 6, flexWrap: 'wrap' },
  chip:    { fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4 },

  // 예지보전 섹션
  predWrap:  { marginTop: 24, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 18 },
  predTitle: { fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 12 },
  predList:  { display: 'flex', flexDirection: 'column', gap: 10 },
  predRow:   { display: 'flex', alignItems: 'center', gap: 12 },
  riskBadge: { fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4, width: 78, textAlign: 'center', flexShrink: 0 },

  // 진단 모달
  overlay:  { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000, padding: 20 },
  modal:    { width: 'min(560px, 92vw)', maxHeight: '85vh', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 14, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 12px 48px rgba(0,0,0,0.6)' },
  modalHeader: { padding: '16px 20px', borderBottom: '1px solid var(--line)', background: 'var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  closeBtn: { background: 'none', border: 'none', color: 'var(--text3)', fontSize: 16, cursor: 'pointer' },
  modalSummary: { padding: '10px 20px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  elecBar:  { margin: '0 20px', padding: '6px 10px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 11, color: 'var(--code)', fontFamily: 'monospace' },
  modalBody: { padding: '12px 20px 16px', overflowY: 'auto', flex: 1 },
  llmTag:   { marginTop: 12, fontSize: 10, color: 'var(--text4)', textAlign: 'right' },
  modalFooter: { padding: '12px 20px', borderTop: '1px solid var(--line)', background: 'var(--surface)', display: 'flex', justifyContent: 'space-between', gap: 10 },
  ghostBtn: { padding: '8px 14px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', fontWeight: 600 },
  primaryBtn: { padding: '8px 14px', background: '#2563eb', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, cursor: 'pointer', fontWeight: 600 },
}
