import { useEffect, useState, useCallback } from 'react'
import { Wrench } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { listWorkOrders, updateWorkOrderStatus } from '../../api/client'

const STATUS = {
  open:        { label: '열림',   color: '#2563eb', icon: '📋' },
  in_progress: { label: '진행중', color: '#d29922', icon: '🔧' },
  done:        { label: '완료',   color: '#3fb950', icon: '✅' },
}
const PRIORITY = {
  HIGH:   { label: '높음', color: '#f85149' },
  MEDIUM: { label: '보통', color: '#d29922' },
  LOW:    { label: '낮음', color: '#2563eb' },
}
const ORDER = ['open', 'in_progress', 'done']

const MD = {
  h3: ({ children }) => <div style={{ fontSize: 11, fontWeight: 700, color: '#2563eb', marginTop: 8, marginBottom: 3 }}>{children}</div>,
  p:  ({ children }) => <p style={{ margin: '0 0 4px', fontSize: 11, lineHeight: 1.6, color: 'var(--text3)' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '2px 0 4px', paddingLeft: 16, color: 'var(--text3)' }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '2px 0 4px', paddingLeft: 16, color: 'var(--text3)' }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 2, fontSize: 11, lineHeight: 1.5 }}>{children}</li>,
  strong: ({ children }) => <strong style={{ color: 'var(--text2)' }}>{children}</strong>,
  em: ({ children }) => <em style={{ color: 'var(--text4)' }}>{children}</em>,
}

function relTime(iso) {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const d = Math.floor(ms / 86400000); if (d > 0) return `${d}일 전`
  const h = Math.floor(ms / 3600000);  if (h > 0) return `${h}시간 전`
  const m = Math.floor(ms / 60000);    return m > 0 ? `${m}분 전` : '방금'
}

function WorkOrderCard({ wo, busy, onAdvance, onComplete }) {
  const pri = PRIORITY[wo.priority] ?? PRIORITY.MEDIUM
  return (
    <div style={s.card}>
      <div style={s.cardHead}>
        <div style={{ minWidth: 0 }}>
          <div style={s.eq}>{wo.equipment_name || wo.equipment_id}</div>
          <div style={s.title}>{wo.title}</div>
        </div>
        <span style={{ ...s.priBadge, color: pri.color, background: pri.color + '22', border: `1px solid ${pri.color}55` }}>
          ● {pri.label}
        </span>
      </div>

      {wo.cause && <div style={s.cause}>{wo.cause}</div>}

      {wo.action && (
        <div style={s.actionBox}>
          <ReactMarkdown components={MD}>{wo.action}</ReactMarkdown>
        </div>
      )}

      <div style={s.metaRow}>
        <span>생성 {relTime(wo.created_at)}</span>
        {wo.assignee && <span>· 담당 {wo.assignee}</span>}
        {wo.status === 'done' && wo.resolved_at && <span>· 완료 {relTime(wo.resolved_at)}</span>}
      </div>

      {wo.status === 'done' && wo.outcome_note && (
        <div style={s.outcome}>✅ {wo.outcome_note}</div>
      )}

      {wo.status !== 'done' && (
        <div style={s.actions}>
          {wo.status === 'open' && (
            <button disabled={busy} onClick={() => onAdvance(wo, 'in_progress')} style={s.startBtn}>▶ 작업 시작</button>
          )}
          {wo.status === 'in_progress' && (
            <button disabled={busy} onClick={() => onComplete(wo)} style={s.doneBtn}>✓ 완료 처리</button>
          )}
        </div>
      )}
    </div>
  )
}

export default function maintenance_panel({ refreshSignal } = {}) {
  const [items,   setItems]   = useState([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')
  const [busyId,  setBusyId]  = useState(null)

  const load = useCallback(() => {
    setLoading(true); setError('')
    listWorkOrders()
      .then(r => setItems(r.data?.items ?? []))
      .catch(e => setError(e.message ?? '작업지시 로드 실패'))
      .finally(() => setLoading(false))
  }, [])

  // 최초 마운트 + 작업지시 생성 신호(refreshSignal) 변경 시 자동 갱신
  useEffect(() => { load() }, [load, refreshSignal])

  const advance = async (wo, status, outcome_note) => {
    setBusyId(wo.id)
    try {
      const r = await updateWorkOrderStatus(wo.id, { status, ...(outcome_note && { outcome_note }) })
      if (r.data?.error) throw new Error(r.data.error)
      setItems(prev => prev.map(x => x.id === wo.id ? r.data : x))
    } catch (e) {
      alert('처리 실패: ' + (e.message ?? ''))
    } finally {
      setBusyId(null)
    }
  }

  const complete = (wo) => {
    const note = window.prompt('완료 메모 (조치 결과를 기록하세요):', '')
    if (note === null) return
    advance(wo, 'done', note || '완료 처리됨')
  }

  const groups = ORDER.map(st => ({ st, list: items.filter(i => i.status === st) }))
  const counts = ORDER.reduce((a, st) => { a[st] = items.filter(i => i.status === st).length; return a }, {})

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <div>
          <div style={s.h1}><Wrench size={18} color="#0d9488" /> 정비 작업지시</div>
          <div style={s.sub}>AI 진단에서 생성된 작업지시를 추적하고 조치 결과를 기록합니다</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={s.summary}>
            <span style={{ color: '#2563eb' }}>열림 {counts.open}</span>
            <span style={{ color: '#d29922' }}>진행 {counts.in_progress}</span>
            <span style={{ color: '#3fb950' }}>완료 {counts.done}</span>
          </div>
          <button onClick={load} style={s.refreshBtn} disabled={loading}>🔄 새로고침</button>
        </div>
      </div>

      <div style={s.body}>
        {loading && <div style={s.center}>불러오는 중...</div>}
        {!loading && error && <div style={s.errorBox}>{error}</div>}
        {!loading && !error && items.length === 0 && (
          <div style={s.center}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>📭</div>
            <div style={{ fontSize: 14, color: 'var(--text)', fontWeight: 600 }}>작업지시가 없습니다</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
              설비 상태 감시 → 카드 클릭 → AI 진단에서 "작업지시 생성"으로 추가하세요.
            </div>
          </div>
        )}
        {!loading && !error && items.length > 0 && (
          <div style={s.columns}>
            {groups.map(({ st, list }) => (
              <div key={st} style={s.column}>
                <div style={{ ...s.colHead, color: STATUS[st].color }}>
                  {STATUS[st].icon} {STATUS[st].label} <span style={{ color: 'var(--text4)' }}>({list.length})</span>
                </div>
                <div style={s.colBody}>
                  {list.length === 0
                    ? <div style={s.colEmpty}>없음</div>
                    : list.map(wo => (
                        <WorkOrderCard key={wo.id} wo={wo} busy={busyId === wo.id} onAdvance={advance} onComplete={complete} />
                      ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const s = {
  wrap:   { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header: { padding: '14px 24px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)' },
  h1:     { fontWeight: 700, fontSize: 16, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8 },
  sub:    { fontSize: 11, color: 'var(--text3)', marginTop: 3 },
  summary:{ display: 'flex', gap: 12, fontSize: 12, fontWeight: 700 },
  refreshBtn: { padding: '7px 14px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, cursor: 'pointer', fontWeight: 600 },

  body:   { flex: 1, overflow: 'hidden', padding: 20 },
  center: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 60, color: 'var(--text3)', fontSize: 13, height: '100%' },
  errorBox: { padding: 20, background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, color: '#f85149' },

  columns: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, height: '100%' },
  column:  { display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 10, overflow: 'hidden' },
  colHead: { padding: '10px 14px', fontSize: 13, fontWeight: 700, borderBottom: '1px solid var(--line)', background: 'var(--surface)', flexShrink: 0 },
  colBody: { flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 },
  colEmpty:{ color: 'var(--text4)', fontSize: 11, textAlign: 'center', padding: 16 },

  card:    { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14 },
  cardHead:{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 },
  eq:      { fontSize: 11, color: 'var(--text3)', marginBottom: 2 },
  title:   { fontSize: 13, fontWeight: 700, color: 'var(--text)' },
  priBadge:{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, flexShrink: 0, whiteSpace: 'nowrap' },
  cause:   { fontSize: 11, color: 'var(--text2)', marginTop: 8, lineHeight: 1.5 },
  actionBox: { marginTop: 8, maxHeight: 150, overflowY: 'auto', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, padding: '6px 10px' },
  metaRow: { display: 'flex', gap: 6, flexWrap: 'wrap', fontSize: 10, color: 'var(--text4)', marginTop: 8 },
  outcome: { marginTop: 8, fontSize: 11, color: '#3fb950', background: '#3fb95011', border: '1px solid #3fb95033', borderRadius: 6, padding: '6px 10px' },
  actions: { marginTop: 10, display: 'flex', gap: 8 },
  startBtn:{ flex: 1, padding: '7px 10px', background: '#2563eb', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer' },
  doneBtn: { flex: 1, padding: '7px 10px', background: '#238636', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer' },
}
