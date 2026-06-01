import { useEffect, useState, useCallback, useRef } from 'react'
import ChatPanel from './ChatPanel'
import { listChatSessions, getChatSession, deleteChatSession, deleteAllChatSessions } from '../api/client'

const INTENT_COLOR = { rag: '#2563eb', anomaly: '#d29922', report: '#3fb950', forecast: '#a371f7', cms: '#39c5cf' }
const INTENT_LABEL = { rag: 'RAG', anomaly: '이상탐지', report: '보고서', forecast: '예측', cms: '설비' }

function relativeTime(iso) {
  if (!iso) return ''
  const ms  = Date.now() - new Date(iso).getTime()
  const min = Math.floor(ms / 60000)
  if (min < 1)  return '방금'
  if (min < 60) return `${min}분 전`
  const hr = Math.floor(min / 60)
  if (hr < 24)  return `${hr}시간 전`
  const day = Math.floor(hr / 24)
  if (day < 7)  return `${day}일 전`
  return iso.slice(0, 10)
}

export default function ChatWorkspacePanel() {
  const [sessions, setSessions] = useState([])
  const [search,   setSearch]   = useState('')
  const [loading,  setLoading]  = useState(true)

  // 우측 채팅 제어
  const [chatKey,        setChatKey]        = useState('new-0')   // 변경 시 ChatPanel 리마운트
  const [activeId,       setActiveId]       = useState(null)      // 사이드바 하이라이트
  const [loadedSession,  setLoadedSession]  = useState(null)      // 리마운트용 초기 session_id
  const [loadedMessages, setLoadedMessages] = useState(null)      // 리마운트용 초기 메시지
  const newCounter = useRef(0)

  const loadList = useCallback(() => {
    listChatSessions(search)
      .then(r => setSessions(r.data?.items ?? []))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false))
  }, [search])

  useEffect(() => {
    const id = setTimeout(loadList, search ? 300 : 0)
    return () => clearTimeout(id)
  }, [search, loadList])

  // 사이드바에서 기존 세션 선택 → 해당 대화를 불러와 이어가기
  const selectSession = async (id) => {
    if (id === activeId) return
    try {
      const r = await getChatSession(id)
      const msgs = (r.data?.messages ?? []).map(m => ({
        role: m.role, text: m.content, intent: m.intent,
      }))
      setLoadedSession(id)
      setLoadedMessages(msgs)
      setActiveId(id)
      setChatKey(`sess-${id}`)
    } catch {}
  }

  const newChat = () => {
    newCounter.current += 1
    setLoadedSession(null)
    setLoadedMessages([])
    setActiveId(null)
    setChatKey(`new-${newCounter.current}`)
  }

  // ChatPanel이 세션을 만들면(또는 메시지 추가) 목록만 갱신 — 리마운트는 안 함
  const handleSessionCreated = useCallback((id) => {
    setActiveId(id)
    loadList()
  }, [loadList])

  const handleDelete = async (id, e) => {
    e.stopPropagation()
    if (!confirm('이 대화를 삭제하시겠습니까?')) return
    try {
      await deleteChatSession(id)
      if (activeId === id) newChat()
      loadList()
    } catch {}
  }

  const handleClearAll = async () => {
    if (sessions.length === 0) return
    if (!confirm(`전체 대화 기록 ${sessions.length}건을 모두 삭제하시겠습니까? 되돌릴 수 없습니다.`)) return
    try { await deleteAllChatSessions(); newChat(); loadList() } catch {}
  }

  return (
    <div style={s.wrap}>
      {/* 좌측: 세션 목록 */}
      <div style={s.sidebar}>
        <div style={s.sidebarHead}>
          <button onClick={newChat} style={s.newBtn}>＋ 새 대화</button>
          {sessions.length > 0 && (
            <button onClick={handleClearAll} style={s.clearAllBtn} title="전체 기록 삭제">🗑</button>
          )}
        </div>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="🔍 대화 검색"
          style={s.search}
        />
        <div style={s.sessionList}>
          {loading && <div style={s.empty}>불러오는 중...</div>}
          {!loading && sessions.length === 0 && (
            <div style={s.empty}>{search ? '검색 결과 없음' : '대화가 없습니다.\n새 대화를 시작하세요.'}</div>
          )}
          {sessions.map(sess => (
            <div
              key={sess.id}
              onClick={() => selectSession(sess.id)}
              style={{ ...s.sessionItem, ...(activeId === sess.id ? s.sessionItemActive : {}) }}
            >
              <div style={s.sessionTitle}>{sess.title}</div>
              <div style={s.sessionMeta}>
                <span>{relativeTime(sess.updated_at)}</span>
                <span>·</span>
                <span>{sess.message_count}개</span>
                {sess.last_intent && (
                  <span style={{
                    marginLeft: 'auto', fontSize: 9, padding: '1px 5px', borderRadius: 3,
                    background: (INTENT_COLOR[sess.last_intent] ?? 'var(--text4)') + '22',
                    color: INTENT_COLOR[sess.last_intent] ?? 'var(--text4)', fontWeight: 600,
                  }}>{INTENT_LABEL[sess.last_intent] ?? sess.last_intent}</span>
                )}
              </div>
              <button onClick={e => handleDelete(sess.id, e)} style={s.deleteBtn} title="삭제">✕</button>
            </div>
          ))}
        </div>
      </div>

      {/* 우측: 실시간 채팅 */}
      <div style={s.chatArea}>
        <ChatPanel
          key={chatKey}
          initialSessionId={loadedSession}
          initialMessages={loadedMessages}
          onSessionCreated={handleSessionCreated}
        />
      </div>
    </div>
  )
}

const s = {
  wrap:    { display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--bg)' },
  sidebar: { width: 240, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--surface)', flexShrink: 0 },
  sidebarHead: { display: 'flex', gap: 8, padding: '12px 12px 0' },
  newBtn:  { flex: 1, padding: '9px 12px', background: '#2563eb', border: 'none', borderRadius: 8, color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  clearAllBtn: { padding: '9px 11px', background: 'var(--line)', border: '1px solid #f8514944', borderRadius: 8, color: '#f85149', fontSize: 12, cursor: 'pointer' },
  search:  { margin: '10px 12px', padding: '8px 12px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, fontSize: 12, outline: 'none' },
  sessionList: { flex: 1, overflowY: 'auto', padding: '0 8px 8px' },
  empty:   { padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 12, lineHeight: 1.7, whiteSpace: 'pre-line' },
  sessionItem: { padding: '10px 12px', borderRadius: 6, cursor: 'pointer', marginBottom: 4, position: 'relative', border: '1px solid transparent' },
  sessionItemActive: { background: '#2563eb15', border: '1px solid #2563eb55' },
  sessionTitle: { fontSize: 13, color: 'var(--text)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', paddingRight: 20 },
  sessionMeta: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--text4)', marginTop: 4 },
  deleteBtn: { position: 'absolute', top: 8, right: 8, background: 'none', border: 'none', color: 'var(--text4)', fontSize: 12, cursor: 'pointer', padding: '2px 6px', borderRadius: 4 },

  chatArea: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
}
