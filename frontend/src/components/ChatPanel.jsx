import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { sendChat } from '../api/client'

const QUICK = [
  { cat: '보고서', color: '#3fb950', questions: ['이번 달 에너지 KPI 요약해줘', '자급률 트렌드를 분석해줘'] },
  { cat: '이상탐지', color: '#f85149', questions: ['최근 이상탐지 원인 분석해줘', 'COP가 갑자기 떨어진 이유는?'] },
  { cat: '도메인', color: '#58a6ff', questions: ['자급률이 낮아진 원인은?', 'PV 야간 NaN은 정상인가요?'] },
  { cat: '예측', color: '#d29922', questions: ['향후 24시간 소비 예측 설명해줘', 'XGBoost 예측이 정확한 이유는?'] },
]

const INTENT_COLOR  = { rag: '#58a6ff', anomaly: '#d29922', report: '#3fb950' }
const INTENT_LABEL  = { rag: 'RAG', anomaly: '이상탐지', report: '보고서' }

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

export default function ChatPanel() {
  const [messages, setMessages] = useState([])
  const [input,    setInput]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const bottomRef  = useRef(null)
  const inputRef   = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const submit = useCallback(async (question) => {
    const q = (question ?? input).trim()
    if (!q || loading) return
    setInput('')
    inputRef.current?.focus()
    const nextMessages = [...messages, { role: 'user', text: q }]
    setMessages(nextMessages)
    setLoading(true)
    try {
      // 이전 대화 히스토리 전달 (user/assistant만, error 제외)
      const history = messages
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role, text: m.text }))
      const { data } = await sendChat(q, history)
      setMessages(prev => [...prev, { role: 'assistant', text: data.answer, intent: data.intent }])
    } catch {
      setMessages(prev => [...prev, { role: 'error', text: '서버 오류가 발생했습니다. API 서버(포트 8000)를 확인하세요.' }])
    } finally {
      setLoading(false)
    }
  }, [input, loading])

  const clear = () => { if (!loading) setMessages([]) }

  return (
    <div style={s.wrap}>
      {/* 헤더 */}
      <div style={s.chatHeader}>
        <span style={s.chatTitle}>에너지 분석 채팅</span>
        {messages.length > 0 && (
          <button style={s.clearBtn} onClick={clear}>대화 초기화</button>
        )}
      </div>

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
                        onMouseLeave={e => e.currentTarget.style.borderColor = '#30363d'}
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
                    <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={MD_COMPONENTS}>
                      {m.text}
                    </ReactMarkdown>
                  </div>
              }
              {m.role === 'assistant' && <CopyBtn text={m.text} />}
            </div>
          </div>
        ))}

        {loading && (
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

      {/* 입력창 */}
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
    </div>
  )
}

const MD_COMPONENTS = {
  h1: ({ children }) => <div style={{ fontSize: 17, fontWeight: 700, color: '#e6edf3', borderBottom: '1px solid #30363d', paddingBottom: 6, marginTop: 16, marginBottom: 10 }}>{children}</div>,
  h2: ({ children }) => <div style={{ fontSize: 15, fontWeight: 700, color: '#e6edf3', borderBottom: '1px solid #21262d', paddingBottom: 4, marginTop: 14, marginBottom: 8 }}>{children}</div>,
  h3: ({ children }) => <div style={{ fontSize: 13, fontWeight: 700, color: '#58a6ff', marginTop: 12, marginBottom: 6 }}>{children}</div>,
  p:  ({ children }) => <p style={{ margin: '0 0 10px', lineHeight: 1.75, color: '#e6edf3' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '4px 0 10px', paddingLeft: 20, listStyleType: 'disc', color: '#e6edf3' }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '4px 0 10px', paddingLeft: 20, listStyleType: 'decimal', color: '#e6edf3' }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 4, lineHeight: 1.7, color: '#e6edf3' }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700, color: '#f0f6fc' }}>{children}</strong>,
  em:     ({ children }) => <em     style={{ fontStyle: 'italic', color: '#d2a8ff' }}>{children}</em>,
  hr: () => <hr style={{ border: 'none', borderTop: '1px solid #30363d', margin: '12px 0' }} />,
  blockquote: ({ children }) => (
    <blockquote style={{ borderLeft: '3px solid #58a6ff', paddingLeft: 12, margin: '8px 0', color: '#8b949e', fontStyle: 'italic' }}>
      {children}
    </blockquote>
  ),
  code: ({ inline, children }) => inline
    ? <code style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 4, padding: '1px 6px', fontSize: 12, color: '#79c0ff', fontFamily: 'monospace' }}>{children}</code>
    : <pre style={{ background: '#0d1117', border: '1px solid #21262d', borderRadius: 8, padding: '12px 14px', overflowX: 'auto', marginBottom: 10 }}>
        <code style={{ fontSize: 12, color: '#e6edf3', fontFamily: 'monospace', whiteSpace: 'pre' }}>{children}</code>
      </pre>,
  table: ({ children }) => <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginBottom: 10 }}>{children}</table>,
  th: ({ children }) => <th style={{ padding: '6px 10px', background: '#21262d', color: '#e6edf3', fontWeight: 600, textAlign: 'left', borderBottom: '1px solid #30363d' }}>{children}</th>,
  td: ({ children }) => <td style={{ padding: '5px 10px', color: '#c9d1d9', borderBottom: '1px solid #21262d' }}>{children}</td>,
}

const s = {
  wrap:       { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  chatHeader: { padding: '14px 20px 12px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 },
  chatTitle:  { fontWeight: 600, fontSize: 15, color: '#e6edf3' },
  clearBtn:   { background: 'none', border: '1px solid #30363d', borderRadius: 6, color: '#8b949e', fontSize: 12, padding: '4px 10px', cursor: 'pointer' },
  messages:   { flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 },
  empty:      { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, paddingTop: 40 },
  emptyIcon:  { fontSize: 44 },
  emptyTitle: { fontSize: 18, fontWeight: 600, color: '#e6edf3' },
  emptyDesc:  { fontSize: 13, color: '#8b949e' },
  quickGroups:{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14, width: '100%', maxWidth: 640 },
  quickGroup: { display: 'flex', flexDirection: 'column', gap: 6 },
  quickCat:   { fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4, border: '1px solid', alignSelf: 'flex-start' },
  quickBtns:  { display: 'flex', flexWrap: 'wrap', gap: 6 },
  exBtn:      { padding: '7px 13px', background: '#161b22', border: '1px solid #30363d', borderRadius: 8, color: '#8b949e', cursor: 'pointer', fontSize: 12, transition: 'border-color .15s' },
  row:        { display: 'flex', gap: 10, alignItems: 'flex-start' },
  avatar:     { width: 30, height: 30, borderRadius: '50%', background: '#21262d', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, flexShrink: 0, marginTop: 2 },
  bubble:     { maxWidth: '78%', padding: '12px 16px', borderRadius: 12, fontSize: 14, lineHeight: 1.7, position: 'relative' },
  userBubble: { background: '#1f6feb', color: '#fff', borderBottomRightRadius: 4 },
  aiBubble:   { background: '#161b22', border: '1px solid #30363d', borderBottomLeftRadius: 4 },
  errorBubble:{ background: '#2d1517', border: '1px solid #f85149', color: '#f85149' },
  badge:      { display: 'inline-block', fontSize: 11, padding: '1px 7px', borderRadius: 4, color: '#0d1117', fontWeight: 700, marginBottom: 8 },
  mdWrap:     { color: '#e6edf3', lineHeight: 1.75, fontSize: 13 },
  copyBtn:    { position: 'absolute', top: 8, right: 8, background: 'none', border: 'none', color: '#6e7681', cursor: 'pointer', fontSize: 14, padding: '2px 4px', borderRadius: 4 },
  typing:     { display: 'flex', gap: 5, alignItems: 'center', height: 20, padding: '2px 0' },
  inputWrap:  { padding: '12px 20px 20px', borderTop: '1px solid #21262d', display: 'flex', gap: 8, flexShrink: 0 },
  input:      { flex: 1, padding: '10px 16px', background: '#161b22', border: '1px solid #30363d', borderRadius: 8, color: '#e6edf3', fontSize: 14, outline: 'none' },
  sendBtn:    { padding: '10px 20px', background: '#1f6feb', border: 'none', borderRadius: 8, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 14, minWidth: 64 },
}
