import { useEffect, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { listChatSessions, getChatSession, deleteChatSession, deleteAllChatSessions } from '../../api/client'

const INTENT_COLOR = { rag: '#2563eb', anomaly: '#d29922', report: '#3fb950', forecast: '#a371f7', cms: '#39c5cf' }
const INTENT_LABEL = { rag: 'RAG', anomaly: '이상탐지', report: '보고서', forecast: '예측', cms: '설비' }

// 메시지 버블 안에서만 머무는 markdown 스타일 (들여쓰기 보수적)
const MD_COMPONENTS = {
  h1: ({ children }) => <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginTop: 10, marginBottom: 6 }}>{children}</div>,
  h2: ({ children }) => <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginTop: 10, marginBottom: 6 }}>{children}</div>,
  h3: ({ children }) => <div style={{ fontSize: 13, fontWeight: 700, color: '#2563eb', marginTop: 8, marginBottom: 4 }}>{children}</div>,
  p:  ({ children }) => <p style={{ margin: '0 0 8px', lineHeight: 1.65, color: 'var(--text)' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '4px 0 8px', paddingLeft: 18, listStylePosition: 'outside', color: 'var(--text)' }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '4px 0 8px', paddingLeft: 18, listStylePosition: 'outside', color: 'var(--text)' }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 3, lineHeight: 1.6, color: 'var(--text)' }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700, color: 'var(--text-strong)' }}>{children}</strong>,
  em:     ({ children }) => <em     style={{ fontStyle: 'italic', color: '#8250df' }}>{children}</em>,
  hr: () => <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '10px 0' }} />,
  blockquote: ({ children }) => (
    <blockquote style={{ borderLeft: '3px solid #2563eb', paddingLeft: 10, margin: '6px 0', color: 'var(--text3)', fontStyle: 'italic' }}>
      {children}
    </blockquote>
  ),
  code: ({ inline, children }) => inline
    ? <code style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, padding: '1px 5px', fontSize: 11, color: 'var(--code)', fontFamily: 'monospace' }}>{children}</code>
    : <pre style={{ background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 6, padding: '10px 12px', overflowX: 'auto', marginBottom: 8 }}>
        <code style={{ fontSize: 11, color: 'var(--text)', fontFamily: 'monospace', whiteSpace: 'pre' }}>{children}</code>
      </pre>,
}

function relativeTime(iso) {
  if (!iso) return ''
  const ms   = Date.now() - new Date(iso).getTime()
  const min  = Math.floor(ms / 60000)
  if (min < 1)    return '방금'
  if (min < 60)   return `${min}분 전`
  const hr   = Math.floor(min / 60)
  if (hr  < 24)   return `${hr}시간 전`
  const day  = Math.floor(hr / 24)
  if (day < 7)    return `${day}일 전`
  return iso.slice(0, 10)
}

export default function ChatHistoryPanel() {
  const [sessions,  setSessions]  = useState([])
  const [selectedId,setSelectedId]= useState(null)
  const [detail,    setDetail]    = useState(null)
  const [search,    setSearch]    = useState('')
  const [loading,   setLoading]   = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)

  const loadList = useCallback(() => {
    setLoading(true)
    listChatSessions(search)
      .then(r => setSessions(r.data?.items ?? []))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false))
  }, [search])

  useEffect(() => { loadList() }, [loadList])

  // 검색어 변경 시 디바운스
  useEffect(() => {
    const id = setTimeout(loadList, 300)
    return () => clearTimeout(id)
  }, [search, loadList])

  // 세션 선택 시 상세 로드
  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    setDetailLoading(true)
    getChatSession(selectedId)
      .then(r => setDetail(r.data))
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false))
  }, [selectedId])

  const handleDelete = async (id, e) => {
    e.stopPropagation()
    if (!confirm('이 대화를 삭제하시겠습니까?')) return
    try {
      await deleteChatSession(id)
      if (selectedId === id) setSelectedId(null)
      loadList()
    } catch {}
  }

  const handleClearAll = async () => {
    if (sessions.length === 0) return
    if (!confirm(`전체 대화 기록 ${sessions.length}건을 모두 삭제하시겠습니까? 되돌릴 수 없습니다.`)) return
    try {
      await deleteAllChatSessions()
      setSelectedId(null)
      loadList()
    } catch {}
  }

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <div>
          <div style={s.title}>💬 AI 대화 내역</div>
          <div style={s.sub}>지난 대화 검색 · 재확인 · 삭제</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11, color: 'var(--text4)' }}>{sessions.length}개 세션</span>
          <button
            onClick={handleClearAll}
            disabled={sessions.length === 0}
            style={s.clearAllBtn}
            title="전체 대화 기록 삭제"
          >🗑 전체 삭제</button>
        </div>
      </div>

      <div style={s.body}>
        {/* 좌측 세션 리스트 */}
        <div style={s.sidebar}>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="🔍 제목 또는 내용 검색"
            style={s.search}
          />

          <div style={s.sessionList}>
            {loading && <div style={s.empty}>불러오는 중...</div>}
            {!loading && sessions.length === 0 && (
              <div style={s.empty}>
                {search ? '검색 결과 없음' : '대화 내역이 없습니다.'}<br/>
                <span style={{ fontSize: 11, color: 'var(--text4)' }}>
                  우하단 💬 챗봇으로 대화를 시작하세요.
                </span>
              </div>
            )}
            {sessions.map(sess => (
              <div
                key={sess.id}
                onClick={() => setSelectedId(sess.id)}
                style={{
                  ...s.sessionItem,
                  ...(selectedId === sess.id ? s.sessionItemActive : {}),
                }}
              >
                <div style={s.sessionTitle}>{sess.title}</div>
                <div style={s.sessionMeta}>
                  <span>{relativeTime(sess.updated_at)}</span>
                  <span>·</span>
                  <span>{sess.message_count}개 메시지</span>
                  {sess.last_intent && (
                    <span style={{
                      marginLeft: 'auto', fontSize: 9, padding: '1px 5px', borderRadius: 3,
                      background: (INTENT_COLOR[sess.last_intent] ?? 'var(--text4)') + '22',
                      color:      INTENT_COLOR[sess.last_intent] ?? 'var(--text4)',
                      fontWeight: 600,
                    }}>
                      {INTENT_LABEL[sess.last_intent] ?? sess.last_intent}
                    </span>
                  )}
                </div>
                <button
                  onClick={e => handleDelete(sess.id, e)}
                  style={s.deleteBtn}
                  title="삭제"
                >✕</button>
              </div>
            ))}
          </div>
        </div>

        {/* 우측 메시지 뷰어 */}
        <div style={s.viewer}>
          {!selectedId && (
            <div style={s.viewerEmpty}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>💬</div>
              <div style={{ fontSize: 14, color: 'var(--text)', marginBottom: 4 }}>
                좌측 목록에서 대화를 선택하세요
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                플로팅 챗봇으로 새 대화를 시작하면 여기에 자동 저장됩니다.
              </div>
            </div>
          )}

          {selectedId && detailLoading && (
            <div style={s.viewerEmpty}>대화를 불러오는 중...</div>
          )}

          {selectedId && !detailLoading && detail && (
            <>
              <div style={s.viewerHeader}>
                <div>
                  <div style={s.viewerTitle}>{detail.session?.title}</div>
                  <div style={s.viewerMeta}>
                    {detail.session?.created_at?.slice(0, 16).replace('T', ' ')} 시작 ·
                    총 {detail.session?.message_count}개 메시지
                  </div>
                </div>
              </div>
              <div style={s.messages}>
                {(detail.messages ?? []).map((m, i) => (
                  <div key={i} style={{ ...s.row, justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
                    {m.role !== 'user' && <div style={s.avatar}>🤖</div>}
                    <div style={{ ...s.bubble, ...(m.role === 'user' ? s.userBubble : s.aiBubble) }}>
                      {m.intent && (
                        <span style={{ ...s.badge, background: INTENT_COLOR[m.intent] ?? 'var(--text4)' }}>
                          {INTENT_LABEL[m.intent] ?? m.intent}
                        </span>
                      )}
                      {m.role === 'user'
                        ? <span>{m.content}</span>
                        : <div style={s.mdWrap}>
                            <ReactMarkdown
                              remarkPlugins={[remarkMath]}
                              rehypePlugins={[rehypeKatex]}
                              components={MD_COMPONENTS}
                            >
                              {m.content}
                            </ReactMarkdown>
                          </div>
                      }
                      <div style={s.ts}>{m.created_at?.slice(11, 16)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const s = {
  wrap:    { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  header:  { padding: '14px 24px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, background: 'var(--surface)' },
  title:   { fontWeight: 700, fontSize: 16, color: 'var(--text)' },
  sub:     { fontSize: 11, color: 'var(--text3)', marginTop: 3 },
  clearAllBtn: { padding: '5px 10px', background: 'var(--line)', border: '1px solid #f8514944', borderRadius: 6, color: '#f85149', fontSize: 11, cursor: 'pointer', fontWeight: 600 },

  body:    { flex: 1, display: 'grid', gridTemplateColumns: '240px 1fr', overflow: 'hidden' },

  // 좌측 세션 리스트
  sidebar:     { borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  search:      { margin: 12, padding: '8px 12px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, fontSize: 12, outline: 'none' },
  sessionList: { flex: 1, overflowY: 'auto', padding: '0 8px 8px' },
  empty:       { padding: 24, textAlign: 'center', color: 'var(--text3)', fontSize: 12, lineHeight: 1.7 },
  sessionItem: { padding: '10px 12px', borderRadius: 6, cursor: 'pointer', marginBottom: 4, position: 'relative', border: '1px solid transparent' },
  sessionItemActive: { background: '#2563eb15', border: '1px solid #2563eb55' },
  sessionTitle:{ fontSize: 13, color: 'var(--text)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', paddingRight: 20 },
  sessionMeta: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--text4)', marginTop: 4 },
  deleteBtn:   { position: 'absolute', top: 8, right: 8, background: 'none', border: 'none', color: 'var(--text4)', fontSize: 12, cursor: 'pointer', padding: '2px 6px', borderRadius: 4 },

  // 우측 메시지 뷰어
  viewer:       { display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  viewerEmpty:  { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 13, padding: 40, textAlign: 'center' },
  viewerHeader: { padding: '12px 20px', borderBottom: '1px solid var(--line)', background: 'var(--surface)' },
  viewerTitle:  { fontSize: 14, fontWeight: 600, color: 'var(--text)' },
  viewerMeta:   { fontSize: 11, color: 'var(--text3)', marginTop: 3 },

  messages:    { flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 },
  row:         { display: 'flex', gap: 10, alignItems: 'flex-start' },
  avatar:      { width: 28, height: 28, borderRadius: '50%', background: 'var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flexShrink: 0, marginTop: 2 },
  bubble:      { maxWidth: '78%', padding: '10px 16px', borderRadius: 12, fontSize: 13, lineHeight: 1.65, position: 'relative', overflow: 'hidden', wordBreak: 'break-word' },
  userBubble:  { background: '#2563eb', color: '#fff', borderBottomRightRadius: 4 },
  aiBubble:    { background: 'var(--surface)', border: '1px solid var(--border)', borderBottomLeftRadius: 4, color: 'var(--text)' },
  badge:       { display: 'inline-block', fontSize: 10, padding: '1px 7px', borderRadius: 4, color: 'var(--bg)', fontWeight: 700, marginBottom: 6 },
  mdWrap:      { lineHeight: 1.65, fontSize: 13, overflowWrap: 'anywhere' },
  ts:          { fontSize: 9, color: 'var(--text4)', marginTop: 6, textAlign: 'right' },
}
