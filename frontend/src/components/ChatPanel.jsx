import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { BASE } from '../api/client'
import ForecastPanel from './ForecastPanel'

const QUICK = [
  { cat: '보고서', color: '#3fb950', questions: ['이번 달 에너지 KPI 요약해줘', '자급률 트렌드를 분석해줘'] },
  { cat: '이상탐지', color: '#f85149', questions: ['최근 이상탐지 원인 분석해줘', 'COP가 갑자기 떨어진 이유는?'] },
  { cat: '도메인', color: '#2563eb', questions: ['자급률이 낮아진 원인은?', 'PV 야간 NaN은 정상인가요?'] },
  { cat: '예측', color: '#d29922', questions: ['향후 24시간 소비 예측 설명해줘', 'XGBoost 예측이 정확한 이유는?'] },
]

const INTENT_COLOR  = { rag: '#2563eb', anomaly: '#d29922', report: '#3fb950', forecast: '#a371f7', cms: '#39c5cf' }
const INTENT_LABEL  = { rag: 'RAG', anomaly: '이상탐지', report: '보고서', forecast: '예측', cms: '설비' }

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button onClick={copy} style={s.copyBtn} title="복사">
      {copied ? '✓' : '⎘'}
    </button>
  )
}

export default function ChatPanel({ initialSessionId = null, initialMessages = null, readOnly = false, onSessionCreated = null, context = null, onClearContext = null, seedQuestion = null, onSeedConsumed = null } = {}) {
  const [messages,  setMessages]  = useState(initialMessages || [])
  const [input,     setInput]     = useState('')
  const [loading,   setLoading]   = useState(false)
  const [sessionId, setSessionId] = useState(initialSessionId)
  const bottomRef  = useRef(null)
  const inputRef   = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // 알림 SSE 구독은 App.jsx의 토스트가 담당 — 채팅 메시지 흐름과 분리

  const newSession = () => {
    if (loading) return
    setMessages([])
    setSessionId(null)
  }

  const submit = useCallback(async (question) => {
    if (readOnly) return
    const q = (question ?? input).trim()
    if (!q || loading) return
    setInput('')
    inputRef.current?.focus()

    const history = messages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, text: m.text }))

    setMessages(prev => [...prev, { role: 'user', text: q }])
    setLoading(true)

    // 스트리밍 placeholder 추가
    setMessages(prev => [...prev, { role: 'assistant', text: '', intent: '', streaming: true }])

    const isFirst = !sessionId

    try {
      const res = await fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: q, history, session_id: sessionId, is_first: isFirst,
          context: context?.equipmentId ? { equipment_id: context.equipmentId } : null,
        }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buf    = ''
      let accText = ''
      let intent  = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()  // 마지막 미완성 줄 보존

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          let ev
          try { ev = JSON.parse(line.slice(6)) } catch { continue }

          if (ev.type === 'session') {
            if (ev.session_id) { setSessionId(ev.session_id); onSessionCreated?.(ev.session_id) }
          } else if (ev.type === 'status') {
            setMessages(prev => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.streaming) next[next.length - 1] = { ...last, statusText: ev.content }
              return next
            })
          } else if (ev.type === 'intent') {
            intent = ev.content
          } else if (ev.type === 'token') {
            accText += ev.content
            setMessages(prev => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.streaming) next[next.length - 1] = { ...last, text: accText, intent }
              return next
            })
          } else if (ev.type === 'done') {
            setMessages(prev => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.streaming) next[next.length - 1] = { ...last, text: accText, intent, streaming: false }
              return next
            })
          } else if (ev.type === 'error') {
            setMessages(prev => {
              const next = prev.filter(m => !m.streaming)
              return [...next, { role: 'error', text: ev.content ?? '서버 오류' }]
            })
          }
        }
      }
    } catch {
      setMessages(prev => {
        const next = prev.filter(m => !m.streaming)
        return [...next, { role: 'error', text: '서버 오류가 발생했습니다. API 서버(포트 8000)를 확인하세요.' }]
      })
    } finally {
      setLoading(false)
    }
  }, [input, loading, messages, sessionId, readOnly, onSessionCreated, context])

  // 알림 "AI 분석" 클릭 등 외부에서 들어온 질문을 자동 전송 (1회)
  const submitRef = useRef(submit)
  submitRef.current = submit
  useEffect(() => {
    if (seedQuestion && !readOnly) {
      submitRef.current(seedQuestion)
      onSeedConsumed?.()
    }
  }, [seedQuestion, readOnly, onSeedConsumed])

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.chatHeader}>
        <span style={s.chatTitle}>
          {readOnly ? '🗂 과거 대화 보기' : '🤖 에너지 분석 채팅'}
        </span>
        {!readOnly && messages.length > 0 && (
          <button style={s.clearBtn} onClick={newSession} title="새 대화 시작">
            + 새 대화
          </button>
        )}
      </div>

      {/* 현재 설비 컨텍스트 칩 — "이 설비 진단해줘" 가능 */}
      {!readOnly && context?.equipmentName && (
        <div style={s.ctxBar}>
          📍 현재 설비: <b style={{ color: 'var(--text)' }}>{context.equipmentName}</b>
          <span style={{ color: 'var(--text4)' }}> — "진단해줘 / 작업지시 만들어줘"에 자동 적용</span>
          {onClearContext && (
            <button onClick={onClearContext} style={s.ctxClear} title="컨텍스트 해제">✕</button>
          )}
        </div>
      )}

      {/* 메시지 영역 */}
      <div style={s.messages}>
        {messages.length === 0 && (
          <div style={s.empty}>
            <div style={s.emptyIcon}>⚡</div>
            <div style={s.emptyTitle}>Honda R&D Europe 에너지 분석 AI</div>
            <div style={s.emptyDesc}>독일 오펜바흐 · 2017~2024 데이터 기반</div>
            <div style={s.quickGroups}>
              {QUICK.map(group => (
                <div key={group.cat} style={s.quickGroup}>
                  <div style={{ ...s.quickCat, color: group.color, borderColor: group.color + '44', background: group.color + '11' }}>
                    {group.cat}
                  </div>
                  <div style={s.quickBtns}>
                    {group.questions.map(q => (
                      <button key={q} style={s.exBtn}
                        onMouseEnter={e => e.currentTarget.style.borderColor = group.color}
                        onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
                        onClick={() => submit(q)}>{q}</button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} style={{ ...s.row, justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            {m.role !== 'user' && (
              <div style={s.avatar}>{m.role === 'error' ? '⚠' : '🤖'}</div>
            )}
            <div style={{
              ...s.bubble,
              ...(m.role === 'user' ? s.userBubble : s.aiBubble),
              ...(m.role === 'error' ? s.errorBubble : {}),
            }}>
              {m.intent && (
                <span style={{ ...s.badge, background: INTENT_COLOR[m.intent] }}>
                  {INTENT_LABEL[m.intent] ?? m.intent}
                </span>
              )}
              {m.role === 'user'
                ? <span>{m.text}</span>
                : <div style={s.mdWrap}>
                    {m.streaming && !m.text
                      ? <div style={s.statusBox}>
                          <div style={s.typing}><span/><span/><span/></div>
                          {m.statusText && (
                            <div style={s.statusText}>{m.statusText}</div>
                          )}
                        </div>
                      : <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={MD_COMPONENTS}>
                          {m.text + (m.streaming ? '▌' : '')}
                        </ReactMarkdown>
                    }
                  </div>
              }
              {m.role === 'assistant' && <CopyBtn text={m.text} />}
            </div>
          </div>
        ))}

        {/* 스트리밍 시작 전 타이핑 닷 (placeholder가 텍스트 없을 때만) */}
        {loading && !messages.some(m => m.streaming) && (
          <div style={{ ...s.row, justifyContent: 'flex-start' }}>
            <div style={s.avatar}>🤖</div>
            <div style={{ ...s.bubble, ...s.aiBubble }}>
              <div style={s.typing}>
                <span/><span/><span/>
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 입력창 (history 모드에서는 숨김) */}
      {!readOnly && (
        <div style={s.inputWrap}>
          <input
            ref={inputRef}
            style={s.input}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && submit()}
            placeholder="에너지 데이터에 대해 질문하세요... (Enter로 전송)"
            disabled={loading}
          />
          <button
            style={{ ...s.sendBtn, opacity: loading || !input.trim() ? 0.4 : 1 }}
            onClick={() => submit()}
            disabled={loading || !input.trim()}
          >
            {loading ? '...' : '전송'}
          </button>
        </div>
      )}
    </div>
  )
}

const MD_COMPONENTS = {
  h1: ({ children }) => <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--text)', borderBottom: '1px solid var(--border)', paddingBottom: 6, marginTop: 16, marginBottom: 10 }}>{children}</div>,
  h2: ({ children }) => <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', borderBottom: '1px solid var(--line)', paddingBottom: 4, marginTop: 14, marginBottom: 8 }}>{children}</div>,
  h3: ({ children }) => <div style={{ fontSize: 13, fontWeight: 700, color: '#2563eb', marginTop: 12, marginBottom: 6 }}>{children}</div>,
  p:  ({ children }) => {
    const textContent = Array.isArray(children) ? children.join('') : String(children);
    if (textContent.includes('[CHART:FORECAST]')) {
       return (
         <>
           <p style={{ margin: '0 0 10px', lineHeight: 1.75, color: 'var(--text)' }}>
             {textContent.replace('[CHART:FORECAST]', '')}
           </p>
           <div style={{ marginTop: 10, marginBottom: 10, border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg)', height: 450, overflow: 'hidden' }}>
             <ForecastPanel />
           </div>
         </>
       )
    }
    return <p style={{ margin: '0 0 10px', lineHeight: 1.75, color: 'var(--text)' }}>{children}</p>
  },
  ul: ({ children }) => <ul style={{ margin: '4px 0 10px', paddingLeft: 20, listStyleType: 'disc', color: 'var(--text)' }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '4px 0 10px', paddingLeft: 20, listStyleType: 'decimal', color: 'var(--text)' }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 4, lineHeight: 1.7, color: 'var(--text)' }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700, color: 'var(--text-strong)' }}>{children}</strong>,
  em:     ({ children }) => <em     style={{ fontStyle: 'italic', color: '#8250df' }}>{children}</em>,
  hr: () => <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '12px 0' }} />,
  blockquote: ({ children }) => (
    <blockquote style={{ borderLeft: '3px solid #2563eb', paddingLeft: 12, margin: '8px 0', color: 'var(--text3)', fontStyle: 'italic' }}>
      {children}
    </blockquote>
  ),
  code: ({ inline, children }) => inline
    ? <code style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '1px 6px', fontSize: 12, color: 'var(--code)', fontFamily: 'monospace' }}>{children}</code>
    : <pre style={{ background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 8, padding: '12px 14px', overflowX: 'auto', marginBottom: 10 }}>
        <code style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'monospace', whiteSpace: 'pre' }}>{children}</code>
      </pre>,
  table: ({ children }) => <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginBottom: 10 }}>{children}</table>,
  th: ({ children }) => <th style={{ padding: '6px 10px', background: 'var(--line)', color: 'var(--text)', fontWeight: 600, textAlign: 'left', borderBottom: '1px solid var(--border)' }}>{children}</th>,
  td: ({ children }) => <td style={{ padding: '5px 10px', color: 'var(--text2)', borderBottom: '1px solid var(--line)' }}>{children}</td>,
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  chatHeader: { padding: '14px 20px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 },
  chatTitle:  { fontWeight: 600, fontSize: 15, color: 'var(--text)' },
  clearBtn:   { background: 'none', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text3)', fontSize: 12, padding: '4px 10px', cursor: 'pointer' },
  ctxBar:     { display: 'flex', alignItems: 'center', gap: 4, padding: '7px 16px', background: '#39c5cf12', borderBottom: '1px solid #39c5cf33', fontSize: 11, color: '#39c5cf', flexShrink: 0 },
  ctxClear:   { marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text3)', cursor: 'pointer', fontSize: 12, flexShrink: 0 },
  messages:   { flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 },
  empty:      { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, paddingTop: 40 },
  emptyIcon:  { fontSize: 44 },
  emptyTitle: { fontSize: 18, fontWeight: 600, color: 'var(--text)' },
  emptyDesc:  { fontSize: 13, color: 'var(--text3)' },
  quickGroups:{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14, width: '100%', maxWidth: 640 },
  quickGroup: { display: 'flex', flexDirection: 'column', gap: 6 },
  quickCat:   { fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4, border: '1px solid', alignSelf: 'flex-start' },
  quickBtns:  { display: 'flex', flexWrap: 'wrap', gap: 6 },
  exBtn:      { padding: '7px 13px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text3)', cursor: 'pointer', fontSize: 12, transition: 'border-color .15s' },
  row:        { display: 'flex', gap: 10, alignItems: 'flex-start' },
  avatar:     { width: 30, height: 30, borderRadius: '50%', background: 'var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, flexShrink: 0, marginTop: 2 },
  bubble:     { maxWidth: '78%', padding: '12px 16px', borderRadius: 12, fontSize: 14, lineHeight: 1.7, position: 'relative' },
  userBubble: { background: '#2563eb', color: '#fff', borderBottomRightRadius: 4 },
  aiBubble:   { background: 'var(--surface)', border: '1px solid var(--border)', borderBottomLeftRadius: 4 },
  errorBubble:{ background: '#fee2e2', border: '1px solid #f85149', color: '#f85149' },
  badge:      { display: 'inline-block', fontSize: 11, padding: '1px 7px', borderRadius: 4, color: 'var(--bg)', fontWeight: 700, marginBottom: 8 },
  mdWrap:     { color: 'var(--text)', lineHeight: 1.75, fontSize: 13 },
  copyBtn:    { position: 'absolute', top: 8, right: 8, background: 'none', border: 'none', color: 'var(--text4)', cursor: 'pointer', fontSize: 14, padding: '2px 4px', borderRadius: 4 },
  typing:     { display: 'flex', gap: 5, alignItems: 'center', height: 20, padding: '2px 0' },
  statusBox:  { display: 'flex', alignItems: 'center', gap: 10, minHeight: 24 },
  statusText: { fontSize: 12, color: 'var(--text3)', fontStyle: 'italic', animation: 'fadeIn .3s ease' },
  inputWrap:  { padding: '12px 20px 20px', borderTop: '1px solid var(--line)', display: 'flex', gap: 8, flexShrink: 0 },
  input:      { flex: 1, padding: '10px 16px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 14, outline: 'none' },
  sendBtn:    { padding: '10px 20px', background: '#2563eb', border: 'none', borderRadius: 8, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 14, minWidth: 64 },
}
