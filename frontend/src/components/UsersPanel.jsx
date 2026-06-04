import { useState, useEffect } from 'react'
import { Users, UserPlus, Trash2, Search, ShieldCheck, Eye, Wrench, X, Loader2 } from 'lucide-react'
import { listUsers, createUser, updateUser, deleteUser } from '../api/client'

const ROLES = {
  admin:    { label: '최고 관리자', color: '#2563eb', bg: '#dbeafe' },
  operator: { label: '운영자',      color: '#7c3aed', bg: '#ede9fe' },
  viewer:   { label: '뷰어',        color: '#0891b2', bg: '#cffafe' },
}
const ROLE_ICON = { admin: ShieldCheck, operator: Wrench, viewer: Eye }

export default function UsersPanel() {
  const [users,   setUsers]   = useState([])
  const [loading, setLoading] = useState(true)
  const [search,  setSearch]  = useState('')
  const [modal,   setModal]   = useState(null)   // null | 'add' | {type:'delete', user}
  const [form,    setForm]    = useState({ name: '', email: '', role: 'viewer' })
  const [formErr, setFormErr] = useState('')
  const [saving,  setSaving]  = useState(false)

  const load = () => {
    setLoading(true)
    listUsers()
      .then(r => setUsers(r.data))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const filtered = users.filter(u =>
    u.name.includes(search) || u.email.includes(search)
  )

  const handleAdd = async () => {
    if (!form.name.trim()) return setFormErr('이름을 입력하세요.')
    if (!form.email.trim() || !form.email.includes('@')) return setFormErr('유효한 이메일을 입력하세요.')
    setSaving(true); setFormErr('')
    try {
      await createUser(form)
      load()
      setModal(null)
      setForm({ name: '', email: '', role: 'viewer' })
    } catch (e) {
      setFormErr(e.response?.data?.detail || '추가 실패')
    } finally {
      setSaving(false)
    }
  }

  const handleRoleChange = async (id, role) => {
    await updateUser(id, { role })
    setUsers(prev => prev.map(u => u.id === id ? { ...u, role } : u))
  }

  const handleToggleStatus = async (user) => {
    const next = user.status === 'active' ? 'inactive' : 'active'
    await updateUser(user.id, { status: next })
    setUsers(prev => prev.map(u => u.id === user.id ? { ...u, status: next } : u))
  }

  const handleDelete = async (user) => {
    setSaving(true)
    try {
      await deleteUser(user.id)
      load()
      setModal(null)
    } finally {
      setSaving(false)
    }
  }

  const activeCount   = users.filter(u => u.status === 'active').length
  const inactiveCount = users.filter(u => u.status === 'inactive').length

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: 'var(--bg)' }}>
      <div style={{ maxWidth: 900, margin: '0 auto', padding: '28px 32px 48px' }}>

        {/* 헤더 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Users size={20} color="var(--brand)" />
            <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: 0 }}>사용자 관리</h2>
          </div>
          <button onClick={() => { setModal('add'); setFormErr(''); setForm({ name: '', email: '', role: 'viewer' }) }}
            style={addBtnStyle}>
            <UserPlus size={14} /> 사용자 추가
          </button>
        </div>

        {/* 요약 카드 */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>
          {[
            { label: '전체 사용자', value: users.length, color: 'var(--brand)' },
            { label: '활성',        value: activeCount,   color: '#16a34a' },
            { label: '비활성',      value: inactiveCount, color: 'var(--text3)' },
          ].map(s => (
            <div key={s.label} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 18px' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>{s.value}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* 검색 + 테이블 */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>

          {/* 검색 바 */}
          <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Search size={14} color="var(--text3)" />
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="이름 또는 이메일로 검색..."
              style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'var(--text)' }} />
            {search && <button onClick={() => setSearch('')} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)', display: 'flex' }}><X size={14} /></button>}
          </div>

          {/* 테이블 */}
          {loading
            ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>
                <Loader2 size={20} style={{ animation: 'spin 1s linear infinite' }} />
              </div>
            : <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--bg)', borderBottom: '1px solid var(--border)' }}>
                    {['이름 / 이메일', '역할', '상태', '등록일', '마지막 로그인', ''].map(h => (
                      <th key={h} style={{ padding: '10px 16px', fontWeight: 600, color: 'var(--text3)', fontSize: 11, textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0
                    ? <tr><td colSpan={6} style={{ padding: 32, textAlign: 'center', color: 'var(--text3)' }}>검색 결과 없음</td></tr>
                    : filtered.map((user, i) => {
                        const role = ROLES[user.role] ?? ROLES.viewer
                        const RoleIcon = ROLE_ICON[user.role] ?? Eye
                        const isActive = user.status === 'active'
                        return (
                          <tr key={user.id} style={{ borderBottom: i < filtered.length - 1 ? '1px solid var(--line)' : 'none', transition: 'background .1s' }}
                            onMouseEnter={e => e.currentTarget.style.background = 'var(--bg)'}
                            onMouseLeave={e => e.currentTarget.style.background = ''}>

                            {/* 이름/이메일 */}
                            <td style={{ padding: '12px 16px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                <div style={{ width: 32, height: 32, borderRadius: '50%', background: role.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                                  <span style={{ fontSize: 13, fontWeight: 700, color: role.color }}>{user.name[0]}</span>
                                </div>
                                <div>
                                  <div style={{ fontWeight: 600, color: 'var(--text)' }}>{user.name}</div>
                                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>{user.email}</div>
                                </div>
                              </div>
                            </td>

                            {/* 역할 드롭다운 */}
                            <td style={{ padding: '12px 16px' }}>
                              <select value={user.role} onChange={e => handleRoleChange(user.id, e.target.value)}
                                style={{ padding: '4px 8px', border: `1px solid ${role.color}44`, borderRadius: 6, background: role.bg, color: role.color, fontSize: 12, fontWeight: 600, cursor: 'pointer', outline: 'none' }}>
                                {Object.entries(ROLES).map(([v, r]) => (
                                  <option key={v} value={v}>{r.label}</option>
                                ))}
                              </select>
                            </td>

                            {/* 상태 토글 */}
                            <td style={{ padding: '12px 16px' }}>
                              <button onClick={() => handleToggleStatus(user)}
                                style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600, background: isActive ? '#dcfce7' : 'var(--surface2)', color: isActive ? '#16a34a' : 'var(--text3)', transition: 'all .15s' }}>
                                <div style={{ width: 6, height: 6, borderRadius: '50%', background: isActive ? '#16a34a' : 'var(--text4)' }} />
                                {isActive ? '활성' : '비활성'}
                              </button>
                            </td>

                            {/* 등록일 */}
                            <td style={{ padding: '12px 16px', color: 'var(--text3)', fontSize: 12 }}>
                              {user.created_at}
                            </td>

                            {/* 마지막 로그인 */}
                            <td style={{ padding: '12px 16px', color: 'var(--text3)', fontSize: 12 }}>
                              {user.last_login ? user.last_login.replace('T', ' ') : '—'}
                            </td>

                            {/* 삭제 */}
                            <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                              <button onClick={() => setModal({ type: 'delete', user })}
                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text4)', display: 'flex', padding: 4, borderRadius: 4, transition: 'color .15s' }}
                                onMouseEnter={e => e.currentTarget.style.color = '#dc2626'}
                                onMouseLeave={e => e.currentTarget.style.color = 'var(--text4)'}>
                                <Trash2 size={14} />
                              </button>
                            </td>
                          </tr>
                        )
                      })
                  }
                </tbody>
              </table>
          }
        </div>
      </div>

      {/* ── 모달 ── */}
      {modal && (
        <div style={overlayStyle} onClick={e => e.target === e.currentTarget && setModal(null)}>
          <div style={modalStyle}>
            {modal === 'add'
              ? <AddUserForm form={form} setForm={setForm} error={formErr} saving={saving}
                  onSubmit={handleAdd} onClose={() => setModal(null)} />
              : <DeleteConfirm user={modal.user} saving={saving}
                  onConfirm={() => handleDelete(modal.user)} onClose={() => setModal(null)} />
            }
          </div>
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

function AddUserForm({ form, setForm, error, saving, onSubmit, onClose }) {
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>사용자 추가</h3>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)', display: 'flex' }}><X size={18} /></button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Field label="이름">
          <input value={form.name} onChange={e => set('name', e.target.value)}
            placeholder="홍길동" style={inputStyle} autoFocus />
        </Field>
        <Field label="이메일">
          <input value={form.email} onChange={e => set('email', e.target.value)}
            placeholder="user@company.com" type="email" style={inputStyle} />
        </Field>
        <Field label="역할">
          <select value={form.role} onChange={e => set('role', e.target.value)} style={selectStyle}>
            {Object.entries(ROLES).map(([v, r]) => <option key={v} value={v}>{r.label}</option>)}
          </select>
          <div style={{ fontSize: 11, color: 'var(--text4)', marginTop: 4 }}>
            {form.role === 'admin' && '모든 기능 접근 · 설정 변경 가능'}
            {form.role === 'operator' && '설비 진단 · 작업지시 생성 가능 · 설정 변경 불가'}
            {form.role === 'viewer' && '조회만 가능 · 데이터 수정 불가'}
          </div>
        </Field>
      </div>

      {error && <div style={{ marginTop: 12, fontSize: 12, color: '#dc2626' }}>{error}</div>}

      <div style={{ display: 'flex', gap: 8, marginTop: 24, justifyContent: 'flex-end' }}>
        <button onClick={onClose} style={cancelBtnStyle}>취소</button>
        <button onClick={onSubmit} disabled={saving} style={primaryBtnStyle}>
          {saving ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> 추가 중...</> : '추가'}
        </button>
      </div>
    </>
  )
}

function DeleteConfirm({ user, saving, onConfirm, onClose }) {
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>사용자 삭제</h3>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)', display: 'flex' }}><X size={18} /></button>
      </div>
      <p style={{ fontSize: 14, color: 'var(--text2)', lineHeight: 1.6 }}>
        <strong>{user.name}</strong> ({user.email}) 계정을 삭제하시겠습니까?<br />
        <span style={{ color: 'var(--text3)', fontSize: 12 }}>삭제된 계정은 복구할 수 없습니다.</span>
      </p>
      <div style={{ display: 'flex', gap: 8, marginTop: 24, justifyContent: 'flex-end' }}>
        <button onClick={onClose} style={cancelBtnStyle}>취소</button>
        <button onClick={onConfirm} disabled={saving} style={{ ...primaryBtnStyle, background: '#dc2626' }}>
          {saving ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> 삭제 중...</> : '삭제'}
        </button>
      </div>
    </>
  )
}

function Field({ label, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>{label}</label>
      {children}
    </div>
  )
}

const base = { padding: '8px 12px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 6, fontSize: 13, outline: 'none', width: '100%' }
const inputStyle  = { ...base }
const selectStyle = { ...base, cursor: 'pointer' }

const addBtnStyle = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 16px', background: 'var(--brand)', border: 'none',
  color: '#fff', borderRadius: 7, fontSize: 13, fontWeight: 600, cursor: 'pointer',
}
const primaryBtnStyle = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 18px', background: 'var(--brand)', border: 'none',
  color: '#fff', borderRadius: 7, fontSize: 13, fontWeight: 600, cursor: 'pointer',
}
const cancelBtnStyle = {
  padding: '8px 18px', background: 'var(--bg)', border: '1px solid var(--border)',
  color: 'var(--text2)', borderRadius: 7, fontSize: 13, cursor: 'pointer',
}
const overlayStyle = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
}
const modalStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 14, padding: '24px 28px', width: 440, maxWidth: '90vw',
  boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
}
