import { useState } from 'react'
import { Zap, LogIn, AlertCircle } from 'lucide-react'
import { login, setAuthToken } from '../api/client'
import { T } from '../theme'

export default function login_screen({ onLogin }) {
  // 데모 편의: admin 자격 미리 채움
  const [email,    setEmail]    = useState('admin@honda-rd.eu')
  const [password, setPassword] = useState('admin123')
  const [error,    setError]    = useState('')
  const [busy,     setBusy]     = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!email || !password) { setError('이메일과 비밀번호를 입력하세요.'); return }
    setBusy(true); setError('')
    try {
      const r = await login(email.trim(), password)
      setAuthToken(r.data.token)
      onLogin(r.data.user)
    } catch (err) {
      const msg = err?.response?.data?.detail ?? '로그인에 실패했습니다. 잠시 후 다시 시도하세요.'
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={s.wrap}>
      <form style={s.card} onSubmit={submit}>
        <div style={s.brand}>
          <div style={s.logoIcon}><Zap size={22} color="#fff" strokeWidth={2.5} /></div>
          <div>
            <div style={s.title}>TTF-FMS</div>
            <div style={s.sub}>Facility Management System</div>
          </div>
        </div>

        <div style={s.welcome}>설비 관리 AI 코파일럿에 로그인하세요</div>

        <label style={s.label}>이메일</label>
        <input
          style={s.input}
          type="email"
          autoFocus
          value={email}
          placeholder="admin@honda-rd.eu"
          onChange={e => setEmail(e.target.value)}
        />

        <label style={s.label}>비밀번호</label>
        <input
          style={s.input}
          type="password"
          value={password}
          placeholder="••••••••"
          onChange={e => setPassword(e.target.value)}
        />

        {error && (
          <div style={s.error}><AlertCircle size={15} /> {error}</div>
        )}

        <button style={{ ...s.btn, opacity: busy ? 0.6 : 1 }} type="submit" disabled={busy}>
          <LogIn size={16} /> {busy ? '로그인 중...' : '로그인'}
        </button>

        <div style={s.hint}>
          데모 계정 — <b>admin@honda-rd.eu</b> / <b>admin123</b>
        </div>
      </form>
      <div style={s.footer}>Honda R&amp;D Europe GmbH · SKN25 4팀</div>
    </div>
  )
}

const s = {
  wrap:    { minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: T.bg, gap: 16 },
  card:    { width: 360, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 16, padding: '32px 30px', boxShadow: T.shadowMd, display: 'flex', flexDirection: 'column' },
  brand:   { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 22 },
  logoIcon:{ width: 42, height: 42, background: `linear-gradient(135deg, ${T.brand}, ${T.brandStrong})`, borderRadius: 11, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 8px rgba(13,148,136,.3)' },
  title:   { fontSize: 19, fontWeight: 800, color: T.text, letterSpacing: -0.3 },
  sub:     { fontSize: 11, color: T.textFaint, fontWeight: 600, marginTop: 1 },
  welcome: { fontSize: 13, color: T.textMuted, marginBottom: 20 },
  label:   { fontSize: 12, fontWeight: 600, color: T.textMuted, marginBottom: 6 },
  input:   { padding: '10px 12px', background: T.bg, border: `1px solid ${T.border}`, borderRadius: 8, color: T.text, fontSize: 13, marginBottom: 14, outline: 'none' },
  error:   { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: T.danger, background: T.dangerSoft, border: `1px solid ${T.danger}33`, borderRadius: 8, padding: '8px 10px', marginBottom: 14 },
  btn:     { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '11px 0', background: `linear-gradient(135deg, ${T.brand}, ${T.brandStrong})`, border: 'none', borderRadius: 8, color: '#fff', fontSize: 14, fontWeight: 700, cursor: 'pointer', marginTop: 4 },
  hint:    { fontSize: 11, color: T.textFaint, textAlign: 'center', marginTop: 16, lineHeight: 1.6 },
  footer:  { fontSize: 11, color: T.textFaint },
}
