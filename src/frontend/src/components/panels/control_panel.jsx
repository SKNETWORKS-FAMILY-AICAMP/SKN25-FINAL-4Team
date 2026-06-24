import { useEffect, useState, useCallback } from 'react'
import {
  SlidersHorizontal, Wallet, Brain, TrendingDown, Zap, Moon,
  AlertCircle, ClipboardList, BookOpen, Check, Trash2,
  Loader2, CheckCircle2, RefreshCw, Bot, ChevronDown, ChevronUp,
  ListChecks, Info,
} from 'lucide-react'
import {
  getControlRecommendations,
  approveRecommendation,
  rejectRecommendation,
  clearRecommendations,
  getLearningStats,
} from '../../api/client'

const fmtEur = v => '€ ' + Math.round(v || 0).toLocaleString('de-DE')

const PRIORITY_COLOR = {
  HIGH:   { bg: '#f8514922', border: '#f85149', label: '높음' },
  MEDIUM: { bg: '#d2992222', border: '#d29922', label: '보통' },
  LOW:    { bg: '#2563eb22', border: '#2563eb', label: '낮음' },
}

const CATEGORY_META = {
  peak_shift: {
    label: '피크 시프트',
    icon: <Zap size={16} color="#d29922"/>,
    steps: [
      '수요 예측 페이지에서 피크 예상 시각 확인',
      '피크 시작 1시간 전 비필수 설비(HVAC, 보조장비) 출력 감소',
      '피크 구간 동안 CHP 최대 출력 유지',
      '피크 종료 후 정상 운전으로 복귀 및 결과 기록',
    ],
  },
  night_check: {
    label: '대기전력 점검',
    icon: <Moon size={16} color="#a371f7"/>,
    steps: [
      '이상탐지 내역에서 야간 소비 계량기 확인',
      '해당 구역 현장 점검 (조명, 미사용 설비 전원)',
      '불필요한 상시 전원 차단 또는 타이머 설정',
      '익일 야간 소비 데이터로 효과 확인',
    ],
  },
  anomaly_response: {
    label: '이상 대응',
    icon: <AlertCircle size={16} color="#f85149"/>,
    steps: [
      '이상탐지 내역 페이지에서 해당 이벤트 상세 확인',
      '대상 설비 현장 육안 점검 (누유, 소음, 과열 여부)',
      '필요 시 설비 가동 중단 및 정비 작업지시 생성',
      '조치 완료 후 이상탐지 재모니터링 (24시간)',
    ],
  },
  efficiency_review: {
    label: '효율 검토',
    icon: <TrendingDown size={16} color="#3fb950"/>,
    steps: [
      '설비 상태 감시 페이지에서 COP 추이 확인',
      '냉매·필터 상태 점검 (3개월 주기 권장)',
      '인버터 설정값 검토 및 최적 운전점 조정',
      '1주일 후 에너지 분석 페이지에서 효율 변화 비교',
    ],
  },
}

const STATUS_LABEL = {
  pending:  { label: '검토 대기', color: 'var(--text3)', bg: 'var(--line)' },
  approved: { label: '조치 예정', color: '#3fb950',      bg: '#3fb95022' },
  rejected: { label: '보류됨',   color: 'var(--text4)', bg: 'var(--surface)' },
}

/* ── 승인 메모 모달 ── */
function ApproveModal({ item, onConfirm, onCancel }) {
  const [memo, setMemo] = useState('')
  const meta = CATEGORY_META[item.category] ?? {}

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', zIndex: 9999,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onCancel}>
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
        padding: 24, width: 480, maxWidth: '90vw', boxShadow: '0 12px 40px rgba(0,0,0,.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
          조치 계획 확인
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 16 }}>
          {item.title}
        </div>

        {/* 조치 단계 */}
        <div style={{ background: 'var(--bg)', borderRadius: 8, padding: '10px 14px', marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', marginBottom: 8,
            display: 'flex', alignItems: 'center', gap: 5 }}>
            <ListChecks size={11}/>권장 조치 절차
          </div>
          {(meta.steps ?? []).map((step, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 5, alignItems: 'flex-start' }}>
              <span style={{ fontSize: 10, color: '#a371f7', fontWeight: 700, marginTop: 1,
                background: '#a371f722', borderRadius: '50%', width: 16, height: 16,
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                {i + 1}
              </span>
              <span style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>{step}</span>
            </div>
          ))}
        </div>

        {/* 메모 입력 */}
        <div style={{ marginBottom: 16 }}>
          <label style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600,
            display: 'block', marginBottom: 6 }}>
            조치 메모 (선택)
          </label>
          <textarea
            value={memo}
            onChange={e => setMemo(e.target.value)}
            placeholder="실제 조치 내용, 담당자, 예정 시각 등을 기록해두면 학습에 활용됩니다"
            rows={3}
            style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px',
              background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6,
              color: 'var(--text)', fontSize: 12, resize: 'vertical', fontFamily: 'inherit' }}
          />
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={s.btnGhost}>취소</button>
          <button onClick={() => onConfirm(memo)} style={s.btnApprove}>
            <Check size={12} style={{ marginRight: 4 }}/>조치 예정으로 표시
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── 권고 카드 ── */
function RecCard({ item, busy, onAction, onAskAI }) {
  const [showSteps, setShowSteps] = useState(false)
  const [showApproveModal, setShowApproveModal] = useState(false)

  const pri = PRIORITY_COLOR[item.priority] ?? PRIORITY_COLOR.LOW
  const meta = CATEGORY_META[item.category] ?? {}
  const learned = item.learned

  const handleAskAI = () => {
    const q = `운영 권고: "${item.title}"
카테고리: ${meta.label ?? item.category} | 우선순위: ${item.priority}
대상 설비: ${item.equipment}
예상 효과: ${item.expected_saving}
상세 내용: ${item.description}

이 권고의 원인과 구체적인 조치 방법, 주의사항을 설명해줘.`
    onAskAI?.(q)
  }

  const handleApproveConfirm = (memo) => {
    setShowApproveModal(false)
    onAction(item, 'approve', memo)
  }

  return (
    <>
      {showApproveModal && (
        <ApproveModal item={item} onConfirm={handleApproveConfirm} onCancel={() => setShowApproveModal(false)}/>
      )}
      <div style={{ ...s.card, borderLeft: `4px solid ${pri.border}` }}>
        {/* 헤더 */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ paddingTop: 2 }}>{meta.icon ?? <SlidersHorizontal size={16}/>}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 10, color: 'var(--text3)', fontWeight: 600,
              marginBottom: 2,
              display: 'flex', alignItems: 'center', gap: 6 }}>
              {meta.label ?? item.category}
              {learned?.rate != null && (
                <span style={{ fontSize: 9, color: learned.signal === 'trusted' ? '#3fb950' : '#d29922',
                  background: (learned.signal === 'trusted' ? '#3fb950' : '#d29922') + '22',
                  padding: '1px 5px', borderRadius: 3, fontWeight: 700 }}>
                  <Brain size={8} style={{ marginRight: 2 }}/>학습 성공률 {learned.rate}%
                </span>
              )}
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', lineHeight: 1.4 }}>
              {item.title}
            </div>
          </div>
          <span style={{ fontSize: 10, padding: '3px 8px', borderRadius: 4, fontWeight: 700,
            background: pri.bg, color: pri.border, border: `1px solid ${pri.border}55`, flexShrink: 0 }}>
            {pri.label}
          </span>
        </div>

        {/* 설명 */}
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          {item.description}
        </div>

        {/* 메타 */}
        <div style={{ display: 'flex', gap: 16, padding: '8px 0',
          borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 9, color: 'var(--text4)', fontWeight: 600, marginBottom: 2 }}>대상 설비</div>
            <div style={{ fontSize: 12, color: 'var(--text)', fontWeight: 500 }}>{item.equipment}</div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 9, color: 'var(--text4)', fontWeight: 600, marginBottom: 2 }}>예상 효과</div>
            <div style={{ fontSize: 12, color: '#3fb950', fontWeight: 600 }}>{item.expected_saving}</div>
          </div>
        </div>

        {/* 조치 절차 토글 */}
        {meta.steps?.length > 0 && (
          <div>
            <button onClick={() => setShowSteps(v => !v)}
              style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'none',
                border: 'none', cursor: 'pointer', color: 'var(--text3)', fontSize: 11,
                fontWeight: 600, padding: 0 }}>
              <ListChecks size={11}/>
              조치 절차 {showSteps ? <ChevronUp size={11}/> : <ChevronDown size={11}/>}
            </button>
            {showSteps && (
              <div style={{ marginTop: 8, background: 'var(--bg)', borderRadius: 6,
                padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {meta.steps.map((step, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    <span style={{ fontSize: 10, color: '#a371f7', fontWeight: 700,
                      background: '#a371f722', borderRadius: '50%', width: 16, height: 16,
                      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      {i + 1}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--text2)', lineHeight: 1.5 }}>{step}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 액션 버튼 */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          {onAskAI && (
            <button onClick={handleAskAI} style={s.btnAI} title="AI에게 상세 분석 요청">
              <Bot size={11} style={{ marginRight: 4 }}/>AI에게 물어보기
            </button>
          )}
          <button onClick={() => onAction(item, 'reject')} disabled={busy} style={s.btnGhost}>
            보류
          </button>
          <button onClick={() => setShowApproveModal(true)} disabled={busy} style={s.btnApprove}>
            {busy ? '처리 중...' : <><Check size={11} style={{ marginRight: 4 }}/>조치 예정으로 표시</>}
          </button>
        </div>
      </div>
    </>
  )
}

/* ── 처리 완료 아이템 ── */
function ReviewedItem({ item }) {
  const [open, setOpen] = useState(false)
  const status = STATUS_LABEL[item.status] ?? STATUS_LABEL.pending
  const meta = CATEGORY_META[item.category] ?? {}

  return (
    <div style={{ background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6,
      overflow: 'hidden', cursor: 'pointer' }} onClick={() => setOpen(v => !v)}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px' }}>
        <div style={{ flexShrink: 0, opacity: 0.6 }}>{meta.icon ?? <SlidersHorizontal size={13}/>}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: 'var(--text)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.title}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 2 }}>
            {item.status_updated ? new Date(item.status_updated).toLocaleString('ko-KR') : ''}
          </div>
        </div>
        <span style={{ fontSize: 11, color: status.color, background: status.bg,
          padding: '3px 10px', borderRadius: 4, fontWeight: 600, flexShrink: 0 }}>
          {status.label}
        </span>
        {open ? <ChevronUp size={12} style={{ color: 'var(--text4)', flexShrink: 0 }}/>
               : <ChevronDown size={12} style={{ color: 'var(--text4)', flexShrink: 0 }}/>}
      </div>
      {open && (
        <div style={{ padding: '10px 14px 12px', borderTop: '1px solid var(--line)',
          background: 'var(--surface2)' }} onClick={e => e.stopPropagation()}>
          <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 8 }}>
            {item.description}
          </div>
          {item.outcome_note && (
            <div style={{ fontSize: 11, color: '#3fb950', background: '#3fb95014',
              borderRadius: 5, padding: '5px 10px', marginBottom: 8 }}>
              <CheckCircle2 size={10} style={{ marginRight: 4 }}/>
              {item.outcome_note}
            </div>
          )}
          <div style={{ display: 'flex', gap: 16, paddingTop: 8, borderTop: '1px solid var(--line)' }}>
            <div>
              <div style={{ fontSize: 9, color: 'var(--text4)', fontWeight: 600 }}>대상 설비</div>
              <div style={{ fontSize: 12, color: 'var(--text)', fontWeight: 500, marginTop: 2 }}>{item.equipment}</div>
            </div>
            <div>
              <div style={{ fontSize: 9, color: 'var(--text4)', fontWeight: 600 }}>예상 효과</div>
              <div style={{ fontSize: 12, color: '#3fb950', fontWeight: 500, marginTop: 2 }}>{item.expected_saving}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── 메인 패널 ── */
export default function ControlPanel({ onAskAI }) {
  const [items,    setItems]    = useState([])
  const [meta,     setMeta]     = useState(null)
  const [summary,  setSummary]  = useState(null)
  const [learning, setLearning] = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState('')
  const [busyId,   setBusyId]   = useState(null)

  const loadLearning = useCallback(() => {
    getLearningStats().then(r => setLearning(r.data?.error ? null : r.data)).catch(() => {})
  }, [])

  const load = useCallback(() => {
    setLoading(true); setError('')
    getControlRecommendations(24)
      .then(r => {
        if (r.data.error) throw new Error(r.data.error)
        setItems(r.data.items ?? [])
        setMeta({ generated_at: r.data.generated_at, count: r.data.count })
        setSummary(r.data.summary ?? null)
      })
      .catch(e => setError(e.message ?? '권고 로드 실패'))
      .finally(() => { setLoading(false); loadLearning() })
  }, [loadLearning])

  useEffect(() => { load() }, [load])

  const handle = async (item, action, memo) => {
    setBusyId(item.id)
    try {
      const body = {
        title: item.title, description: item.description,
        category: item.category, priority: item.priority,
        memo: memo ?? '',
      }
      if (action === 'approve') await approveRecommendation(item.id, body)
      else                       await rejectRecommendation(item.id, body)
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
    if (!confirm('처리·학습 이력을 모두 초기화하시겠습니까? 되돌릴 수 없습니다.')) return
    try { await clearRecommendations(); load() }
    catch (e) { alert('초기화 실패: ' + (e.message ?? '')) }
  }

  const pendingItems  = items.filter(i => (i.status ?? 'pending') === 'pending')
  const reviewedItems = items.filter(i => (i.status ?? 'pending') !== 'pending')
  const highCount     = pendingItems.filter(i => i.priority === 'HIGH').length
  const pendingEur    = pendingItems.reduce((s, i) => s + (i.saving_eur || 0), 0)
  const approvedEur   = items.filter(i => i.status === 'approved').reduce((s, i) => s + (i.saving_eur || 0), 0)

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.header}>
        <div>
          <div style={s.title}>
            <SlidersHorizontal size={18} color="#0d9488"/>운영 권고 체크리스트
          </div>
          <div style={s.sub}>
            AI가 분석한 권고를 검토하고 조치를 직접 실행하세요
            {meta && <> · 생성 {meta.generated_at?.slice(11, 16)}</>}
            <span style={{ marginLeft: 8, padding: '1px 6px', background: 'var(--line)',
              borderRadius: 3, fontSize: 10, color: 'var(--text4)' }}>
              실제 설비 자동제어 없음 · 운영자 직접 조치 필요
            </span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {highCount > 0 && (
            <span style={s.alertBadge}>
              <AlertCircle size={10} style={{ marginRight: 3 }}/>긴급 {highCount}건
            </span>
          )}
          <button onClick={load} style={s.refreshBtn} disabled={loading}>
            {loading ? '분석 중...' : <><RefreshCw size={11} style={{ marginRight: 4 }}/>다시 분석</>}
          </button>
          {reviewedItems.length > 0 && (
            <button onClick={handleClearHistory} style={s.clearBtn}>
              <Trash2 size={11} style={{ marginRight: 4 }}/>이력 초기화
            </button>
          )}
        </div>
      </div>

      <div style={s.body}>
        {/* KPI 행 */}
        {!loading && !error && items.length > 0 && (
          <div style={s.kpiRow}>
            <div style={s.kpiCard}>
              <div style={s.kpiLabel}><Wallet size={13} color="#0d9488"/>검토 대기 절감 가능</div>
              <div style={{ ...s.kpiValue, color: '#0d9488' }}>{fmtEur(pendingEur)}<span style={s.kpiUnit}>/월</span></div>
              <div style={s.kpiSub}>{pendingItems.length}건 권고 · 조치 후 반영 예상</div>
            </div>
            <div style={s.kpiCard}>
              <div style={s.kpiLabel}><TrendingDown size={13} color="#3fb950"/>조치 예정 효과</div>
              <div style={{ ...s.kpiValue, color: '#3fb950' }}>{fmtEur(approvedEur)}<span style={s.kpiUnit}>/월</span></div>
              <div style={s.kpiSub}>현재 조치 예정으로 표시된 권고</div>
            </div>
            <div style={s.kpiCard}>
              <div style={s.kpiLabel}><Brain size={13} color="#7c3aed"/>AI 적응형 학습</div>
              {learning && (learning.success + learning.failure) > 0 ? (
                <>
                  <div style={{ ...s.kpiValue, color: '#7c3aed' }}>
                    {Math.round(learning.success / (learning.success + learning.failure) * 100)}
                    <span style={s.kpiUnit}>% 성공률</span>
                  </div>
                  <div style={s.kpiSub}>
                    성공 {learning.success} · 실패 {learning.failure} · 누적 절감 {Math.round(learning.kw_saved_total)}kW
                  </div>
                </>
              ) : (
                <>
                  <div style={{ ...s.kpiValue, color: 'var(--text3)', fontSize: 18 }}>학습 전</div>
                  <div style={s.kpiSub}>조치 후 24h 결과로 학습 시작</div>
                </>
              )}
            </div>
          </div>
        )}

        {/* 사용 안내 배너 */}
        {!loading && !error && pendingItems.length > 0 && (
          <div style={s.guideBanner}>
            <Info size={13} style={{ flexShrink: 0, marginTop: 1 }}/>
            <div>
              <strong>사용 방법:</strong> 각 권고 카드의 조치 절차를 참고해 현장에서 직접 실행하세요.
              조치가 완료되면 "조치 예정으로 표시"를 눌러 기록하면 AI가 효과를 학습합니다.
              <strong style={{ marginLeft: 6 }}>AI에게 물어보기</strong>로 더 자세한 설명을 받을 수 있습니다.
            </div>
          </div>
        )}

        {loading && (
          <div style={s.center}>
            <Loader2 size={28} strokeWidth={1.5}/>
            <div style={{ fontSize: 13, color: 'var(--text3)', marginTop: 12 }}>
              예측·이상·효율 데이터 종합 분석 중...
            </div>
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
            <CheckCircle2 size={32} strokeWidth={1.2}/>
            <div style={{ fontSize: 14, color: 'var(--text)', fontWeight: 600, marginTop: 10 }}>현재 활성 권고 없음</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
              즉시 조치가 필요한 항목이 없습니다.
            </div>
          </div>
        )}

        {/* 검토 대기 */}
        {!loading && !error && pendingItems.length > 0 && (
          <>
            <div style={{ ...s.sectionTitle, display: 'flex', alignItems: 'center', gap: 5 }}>
              <ClipboardList size={12}/>검토 대기 ({pendingItems.length})
            </div>
            <div style={s.cardGrid}>
              {pendingItems.map(item => (
                <RecCard key={item.id} item={item} busy={busyId === item.id}
                  onAction={handle} onAskAI={onAskAI}/>
              ))}
            </div>
          </>
        )}

        {/* 처리 완료 */}
        {!loading && !error && reviewedItems.length > 0 && (
          <>
            <div style={{ ...s.sectionTitle, marginTop: 24, display: 'flex', alignItems: 'center', gap: 5 }}>
              <BookOpen size={12}/>처리 완료 ({reviewedItems.length})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {reviewedItems.map(item => <ReviewedItem key={item.id} item={item}/>)}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

const s = {
  wrap:        { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header:      { padding: '14px 24px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)', gap: 16, flexWrap: 'wrap' },
  title:       { fontWeight: 700, fontSize: 16, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 8 },
  sub:         { fontSize: 11, color: 'var(--text3)', marginTop: 3 },
  alertBadge:  { fontSize: 11, color: '#f85149', background: '#f8514922', padding: '4px 10px', borderRadius: 6, fontWeight: 600, border: '1px solid #f8514944', display: 'flex', alignItems: 'center' },
  refreshBtn:  { padding: '6px 14px', background: 'var(--line)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', fontSize: 12, fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center' },
  clearBtn:    { padding: '6px 14px', background: 'var(--line)', border: '1px solid #f8514944', borderRadius: 6, color: '#f85149', fontSize: 12, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center' },
  body:        { flex: 1, overflowY: 'auto', padding: '20px 24px 32px' },
  kpiRow:      { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 },
  kpiCard:     { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 16px' },
  kpiLabel:    { fontSize: 11, color: 'var(--text3)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 },
  kpiValue:    { fontSize: 24, fontWeight: 700, marginTop: 6 },
  kpiUnit:     { fontSize: 12, fontWeight: 500, color: 'var(--text3)' },
  kpiSub:      { fontSize: 10, color: 'var(--text4)', marginTop: 3 },
  guideBanner: { background: '#a371f714', border: '1px solid #a371f733', borderRadius: 8, padding: '10px 14px', fontSize: 12, color: 'var(--text3)', lineHeight: 1.6, display: 'flex', gap: 8, marginBottom: 4 },
  center:      { padding: '60px 20px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center' },
  errorBox:    { margin: '20px 0', padding: '14px 18px', background: '#fee2e2', border: '1px solid #f85149', borderRadius: 8, color: '#f85149', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 },
  retryBtn:    { padding: '4px 12px', background: 'none', border: '1px solid #f85149', borderRadius: 4, color: '#f85149', cursor: 'pointer', fontSize: 11 },
  sectionTitle:{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', marginBottom: 12 },
  cardGrid:    { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14 },
  card:        { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 },
  btnGhost:    { padding: '7px 14px', background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text3)', fontSize: 12, fontWeight: 500, cursor: 'pointer' },
  btnApprove:  { padding: '7px 16px', background: '#2563eb', border: 'none', borderRadius: 6, color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'pointer', display: 'flex', alignItems: 'center' },
  btnAI:       { padding: '7px 12px', background: 'transparent', border: '1px solid #a371f755', borderRadius: 6, color: '#a371f7', fontSize: 11, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center' },
}
