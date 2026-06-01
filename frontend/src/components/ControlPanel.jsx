import { useEffect, useState, useCallback } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import {
  getControlRecommendations,
  approveRecommendation,
  rejectRecommendation,
  clearRecommendations,
} from '../api/client'

const PRIORITY_COLOR = {
  HIGH:   { bg: '#f8514922', border: '#f85149', label: '높음', dot: '#f85149' },
  MEDIUM: { bg: '#d2992222', border: '#d29922', label: '보통', dot: '#d29922' },
  LOW:    { bg: '#2563eb22', border: '#2563eb', label: '낮음', dot: '#2563eb' },
}

const CATEGORY_ICON = {
  peak_shift:        '⚡',
  night_check:       '🌙',
  anomaly_response:  '🚨',
  efficiency_review: '📉',
}

const CATEGORY_LABEL = {
  peak_shift:        '피크 시프트',
  night_check:       '대기전력',
  anomaly_response:  '이상 대응',
  efficiency_review: '효율 검토',
}

const STATUS_LABEL = {
  pending:   { label: '검토 대기', color: 'var(--text3)', bg: 'var(--line)' },
  approved:  { label: '✓ 승인 완료', color: '#3fb950', bg: '#3fb95022' },
  rejected:  { label: '✕ 거부됨', color: 'var(--text4)', bg: 'var(--surface)' },
}

export default function ControlPanel() {
  const [items,   setItems]   = useState([])
  const [meta,    setMeta]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')
  const [busyId,  setBusyId]  = useState(null)

  const load = useCallback(() => {
    setLoading(true); setError('')
    getControlRecommendations(24)
      .then(r => {
        if (r.data.error) throw new Error(r.data.error)
        setItems(r.data.items ?? [])
        setMeta({ generated_at: r.data.generated_at, count: r.data.count })
      })
      .catch(e => setError(e.message ?? '권고 로드 실패'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const handle = async (item, action) => {
    setBusyId(item.id)
    try {
      const body = {
        title:       item.title,
        description: item.description,
        category:    item.category,
        priority:    item.priority,
      }
      if (action === 'approve') await approveRecommendation(item.id, body)
      else                       await rejectRecommendation(item.id, body)
      // 낙관적 업데이트
      setItems(prev => prev.map(x => x.id === item.id
        ? { ...x, status: action === 'approve' ? 'approved' : 'rejected', status_updated: new Date().toISOString() }
        : x))
    } catch (e) {
      alert('처리 실패: ' + (e.message ?? ''))
    } finally {
      setBusyId(null)
    }
  }

  const handleClearHistory = async () => {
    if (!confirm('제어 권고의 처리·학습 이력을 모두 초기화하시겠습니까? 되돌릴 수 없습니다.')) return
    try {
      await clearRecommendations()
      load()
    } catch (e) {
      alert('초기화 실패: ' + (e.message ?? ''))
    }
  }

  const pendingItems  = items.filter(i => (i.status ?? 'pending') === 'pending')
  const reviewedItems = items.filter(i => (i.status ?? 'pending') !== 'pending')
  const highCount     = pendingItems.filter(i => i.priority === 'HIGH').length

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <div>
          <div style={s.title}><SlidersHorizontal size={18} color="#0d9488" /> 제어 및 최적화</div>
          <div style={s.sub}>
            AI 에이전트가 분석한 운영 권고를 검토하고 적용을 승인할 수 있습니다
            {meta && <> · 생성 {meta.generated_at?.slice(11, 16)}</>}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {highCount > 0 && (
            <span style={s.alertBadge}>🚨 긴급 {highCount}건</span>
          )}
          <button onClick={load} style={s.refreshBtn} disabled={loading}>
            {loading ? '분석 중...' : '🔄 다시 분석'}
          </button>
          {reviewedItems.length > 0 && (
            <button onClick={handleClearHistory} style={s.clearBtn} title="처리·학습 이력 초기화">
              🗑 이력 초기화
            </button>
          )}
        </div>
      </div>

      <div style={s.body}>
        {loading && (
          <div style={s.center}>
            <div style={{ fontSize: 28, marginBottom: 12 }}>⏳</div>
            <div style={{ fontSize: 13, color: 'var(--text3)' }}>예측·이상·효율 데이터 종합 분석 중...</div>
          </div>
        )}

        {!loading && error && (
          <div style={s.errorBox}>
            {error}
            <button onClick={load} style={s.retryBtn}>다시 시도</button>
          </div>
        )}

        {!loading && !error && items.length === 0 && (
          <div style={s.center}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>✅</div>
            <div style={{ fontSize: 14, color: 'var(--text)', fontWeight: 600 }}>현재 활성 권고 없음</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
              AI가 분석한 결과, 즉시 조치가 필요한 항목이 없습니다.
            </div>
          </div>
        )}

        {!loading && !error && pendingItems.length > 0 && (
          <>
            <div style={s.sectionTitle}>📋 검토 대기 ({pendingItems.length})</div>
            <div style={s.cardGrid}>
              {pendingItems.map(item => (
                <RecCard key={item.id} item={item} busy={busyId === item.id} onAction={handle}/>
              ))}
            </div>
          </>
        )}

        {!loading && !error && reviewedItems.length > 0 && (
          <>
            <div style={{ ...s.sectionTitle, marginTop: 24 }}>📚 처리 완료 ({reviewedItems.length})</div>
            <div style={s.reviewedList}>
              {reviewedItems.map(item => (
                <ReviewedItem key={item.id} item={item}/>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function RecCard({ item, busy, onAction }) {
  const pri = PRIORITY_COLOR[item.priority] ?? PRIORITY_COLOR.LOW
  const icon = CATEGORY_ICON[item.category] ?? '💡'
  const catLabel = CATEGORY_LABEL[item.category] ?? item.category
  const mem = item.memory   // {label, note, status} or undefined
  const memColor = mem?.label === '성공' ? '#3fb950' : mem?.label === '실패' ? '#f85149' : 'var(--text3)'
  const learned = item.learned   // {success, failure, rate, signal} or undefined
  const lrColor = learned?.signal === 'trusted' ? '#3fb950' : learned?.signal === 'caution' ? '#f85149' : 'var(--text3)'

  return (
    <div style={{ ...s.card, borderLeft: `4px solid ${pri.border}` }}>
      <div style={s.cardHeader}>
        <span style={{ fontSize: 22 }}>{icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={s.cardCat}>
            {catLabel}
            {learned?.rate != null && (
              <span style={{ fontSize: 9, color: lrColor, background: lrColor + '22', padding: '1px 6px', borderRadius: 3, marginLeft: 6, fontWeight: 700 }}>
                🧠 성공률 {learned.rate}%{learned.signal === 'trusted' ? ' ↑신뢰' : learned.signal === 'caution' ? ' ⚠주의' : ''}
              </span>
            )}
            {!learned && mem && (
              <span style={{ fontSize: 9, color: memColor, background: memColor + '22', padding: '1px 6px', borderRadius: 3, marginLeft: 6, fontWeight: 700 }}>
                🧠 AI 학습 ({mem.label})
              </span>
            )}
          </div>
          <div style={s.cardTitle}>{item.title}</div>
        </div>
        <span style={{ ...s.priBadge, background: pri.bg, color: pri.border, border: `1px solid ${pri.border}66` }}>
          ● {pri.label}
        </span>
      </div>

      <div style={s.cardDesc}>{item.description}</div>

      <div style={s.cardMeta}>
        <div style={s.metaItem}>
          <span style={s.metaLabel}>대상 설비</span>
          <span style={s.metaValue}>{item.equipment}</span>
        </div>
        <div style={s.metaItem}>
          <span style={s.metaLabel}>예상 효과</span>
          <span style={{ ...s.metaValue, color: '#3fb950' }}>{item.expected_saving}</span>
        </div>
      </div>

      <div style={s.cardActions}>
        <button
          onClick={() => onAction(item, 'reject')}
          disabled={busy}
          style={{ ...s.btnGhost, opacity: busy ? 0.5 : 1 }}
        >
          거부
        </button>
        <button
          onClick={() => onAction(item, 'approve')}
          disabled={busy}
          style={{ ...s.btnApprove, opacity: busy ? 0.5 : 1 }}
        >
          {busy ? '처리 중...' : '✓ 승인 및 적용'}
        </button>
      </div>
    </div>
  )
}

function ReviewedItem({ item }) {
  const [open, setOpen] = useState(false)
  const status = STATUS_LABEL[item.status] ?? STATUS_LABEL.pending
  const pri    = item.priority === 'HIGH' ? '#f85149' : item.priority === 'MEDIUM' ? '#d29922' : '#2563eb'

  return (
    <div style={{ ...s.reviewedItem, display: 'block', cursor: 'pointer', padding: 0 }}
         onClick={() => setOpen(o => !o)}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px' }}>
        <span style={{ fontSize: 14 }}>{CATEGORY_ICON[item.category] ?? '·'}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.title}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 2 }}>
            {item.status_updated ? new Date(item.status_updated).toLocaleString('ko-KR') : ''}
          </div>
        </div>
        <span style={{ fontSize: 11, color: status.color, background: status.bg, padding: '3px 10px', borderRadius: 4, fontWeight: 600, flexShrink: 0 }}>
          {status.label}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text4)', width: 12, textAlign: 'center', flexShrink: 0 }}>
          {open ? '▾' : '▸'}
        </span>
      </div>
      {open && (
        <div style={s.reviewedDetail} onClick={e => e.stopPropagation()}>
          <div style={{ display: 'flex', gap: 10, marginBottom: 8 }}>
            <span style={{ fontSize: 10, color: pri, background: pri + '22', borderRadius: 4, padding: '2px 8px', fontWeight: 700 }}>● {item.priority}</span>
            <span style={{ fontSize: 10, color: 'var(--text3)' }}>카테고리: {CATEGORY_LABEL[item.category] ?? item.category}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 8 }}>
            {item.description}
          </div>
          <div style={{ display: 'flex', gap: 16, paddingTop: 8, borderTop: '1px solid var(--line)' }}>
            <div>
              <div style={{ fontSize: 9, color: 'var(--text4)', textTransform: 'uppercase' }}>대상 설비</div>
              <div style={{ fontSize: 12, color: 'var(--text)', fontWeight: 500, marginTop: 2 }}>{item.equipment}</div>
            </div>
            <div>
              <div style={{ fontSize: 9, color: 'var(--text4)', textTransform: 'uppercase' }}>예상 효과</div>
              <div style={{ fontSize: 12, color: '#3fb950', fontWeight: 500, marginTop: 2 }}>{item.expected_saving}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const s = {
  wrap:        { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header:      { padding: '14px 24px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)', gap: 16, flexWrap: 'wrap' },
  title:       { fontWeight: 700, fontSize: 16, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8 },
  sub:         { fontSize: 11, color: 'var(--text3)', marginTop: 3 },
  alertBadge:  { fontSize: 11, color: '#f85149', background: '#f8514922', padding: '4px 10px', borderRadius: 6, fontWeight: 600, border: '1px solid #f8514944' },
  refreshBtn:  { padding: '6px 14px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, fontWeight: 500, cursor: 'pointer' },
  clearBtn:    { padding: '6px 14px', background: 'var(--line)', border: '1px solid #f8514944', borderRadius: 6, color: '#f85149', fontSize: 12, fontWeight: 600, cursor: 'pointer' },

  body:        { flex: 1, overflowY: 'auto', padding: '20px 24px 32px' },
  center:      { padding: '60px 20px', textAlign: 'center' },
  errorBox:    { margin: '20px 0', padding: '14px 18px', background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, color: '#f85149', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 },
  retryBtn:    { padding: '4px 12px', background: 'none', border: '1px solid #f85149', borderRadius: 4, color: '#f85149', cursor: 'pointer', fontSize: 11 },

  sectionTitle:{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 0.5 },
  cardGrid:    { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14 },

  card:        { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 },
  cardHeader:  { display: 'flex', alignItems: 'flex-start', gap: 10 },
  cardCat:     { fontSize: 10, color: 'var(--text3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2 },
  cardTitle:   { fontSize: 13, fontWeight: 600, color: 'var(--text)', lineHeight: 1.4 },
  priBadge:    { fontSize: 10, padding: '3px 8px', borderRadius: 4, fontWeight: 700, flexShrink: 0 },
  cardDesc:    { fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 },
  cardMeta:    { display: 'flex', gap: 16, padding: '8px 0', borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)' },
  metaItem:    { display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 0 },
  metaLabel:   { fontSize: 9, color: 'var(--text4)', textTransform: 'uppercase', letterSpacing: 0.5 },
  metaValue:   { fontSize: 12, color: 'var(--text)', fontWeight: 500 },
  cardActions: { display: 'flex', gap: 8, justifyContent: 'flex-end' },
  btnGhost:    { padding: '7px 14px', background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text3)', fontSize: 12, fontWeight: 500, cursor: 'pointer' },
  btnApprove:  { padding: '7px 16px', background: 'linear-gradient(135deg, #2563eb, #2563eb)', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'pointer', boxShadow: '0 2px 8px rgba(31, 111, 235, 0.3)' },

  reviewedList:  { display: 'flex', flexDirection: 'column', gap: 4 },
  reviewedItem:  { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6 },
  reviewedDetail:{ padding: '10px 14px 12px', borderTop: '1px solid var(--line)', background: 'var(--surface2)', borderRadius: '0 0 6px 6px' },
}
