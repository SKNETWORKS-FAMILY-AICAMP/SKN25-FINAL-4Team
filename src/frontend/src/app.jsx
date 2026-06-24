import { useState, useRef, useEffect, useCallback, lazy, Suspense } from 'react'
import PanelSkeleton from './components/panel_skeleton'
import LoginScreen     from './components/login_screen'
import { getMe, logout as apiLogout, setAuthToken, getToken } from './api/client'
import {
  LayoutDashboard, Factory, Wrench, AlertTriangle, AlertCircle, Info, SlidersHorizontal,
  TrendingUp, Wallet, FileText, MessageSquare, Settings, Users,
  Bot, Zap, X, Sun, Moon, LogOut,
} from 'lucide-react'
import { T } from './theme'

const DashboardPanel = lazy(() => import('./components/panels/dashboard_panel'))
const ChatPanel = lazy(() => import('./components/panels/chat_panel'))
const ChatWorkspacePanel = lazy(() => import('./components/panels/chat_workspace_panel'))
const AnomalyPanel = lazy(() => import('./components/panels/anomaly_panel'))
const EquipmentPanel = lazy(() => import('./components/panels/equipment_panel'))
const MaintenancePanel = lazy(() => import('./components/panels/maintenance_panel'))
const ReportPanel = lazy(() => import('./components/panels/report_panel'))
const ForecastPanel = lazy(() => import('./components/panels/forecast_panel'))
const SettingsPanel = lazy(() => import('./components/panels/settings_panel'))
const UsersPanel = lazy(() => import('./components/panels/users_panel'))
const ControlPanel = lazy(() => import('./components/panels/control_panel'))
const BillingPanel = lazy(() => import('./components/panels/billing_panel'))

const ROLE_LABEL = { admin: '관리자 (Admin)', operator: '운영자 (Operator)', viewer: '뷰어 (Viewer)' }

const TABS = [
  // ── 핵심 (CMS) ──
  { id: 'dashboard',   label: '대시보드',        icon: LayoutDashboard },
  { id: 'equipment',   label: '설비 상태 감시',  icon: Factory },
  { id: 'maintenance', label: '정비 작업지시',   icon: Wrench },
  { id: 'anomaly',     label: '이상 탐지 내역',  icon: AlertTriangle },
  { id: 'divider1',    isDivider: true },
  // ── 운영 보조 ──
  { id: 'control',     label: '제어 및 최적화',  icon: SlidersHorizontal },
  { id: 'forecast',    label: '수요 예측 현황',  icon: TrendingUp },
  { id: 'billing',     label: '목표 요금 관리',  icon: Wallet },
  { id: 'report',      label: '에너지 분석',     icon: FileText },
  { id: 'divider2',    isDivider: true },
  // ── 참고 ──
  { id: 'chat',        label: 'AI 대화',         icon: MessageSquare },
  { id: 'divider3',    isDivider: true },
  // ── 관리 ──
  { id: 'settings',    label: '시스템 설정',     icon: Settings },
  { id: 'users',       label: '사용자 관리',     icon: Users },
]

const PANELS = {
  dashboard: DashboardPanel,
  equipment: EquipmentPanel,
  maintenance: MaintenancePanel,
  chat:      ChatWorkspacePanel,
  anomaly:   AnomalyPanel,
  report:    ReportPanel,
  forecast:  ForecastPanel,
  control:   ControlPanel,
  billing:   BillingPanel,
  settings:  SettingsPanel,
  users:     UsersPanel,
}

export default function App() {
  const [tab, setTab]   = useState('dashboard')
  const visited = useRef(new Set(['dashboard']))
  // 인증
  const [user, setUser]             = useState(null)
  const [authReady, setAuthReady]   = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [apiOk, setApiOk] = useState(null)
  const [woVersion, setWoVersion] = useState(0)   // 작업지시 생성 시 +1 → 정비 패널 자동 갱신
  const [anomalyFilter, setAnomalyFilter] = useState(null)  // 설비 → 이상 내역 드릴다운 필터
  const [chatContext, setChatContext] = useState(null)      // 플로팅 챗봇이 인지할 현재 설비 {equipmentId, equipmentName}
  const [chatSeed, setChatSeed] = useState(null)            // 알림 "AI 분석" 클릭 시 자동 전송할 질문
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('theme', theme)
  }, [theme])

  // 저장된 토큰이 있으면 검증해 자동 로그인
  useEffect(() => {
    if (!getToken()) { setAuthReady(true); return }
    getMe()
      .then(r => setUser(r.data))
      .catch(() => setAuthToken(null))
      .finally(() => setAuthReady(true))
  }, [])

  const handleLogout = useCallback(() => {
    apiLogout()
    setAuthToken(null)
    setUser(null)
  }, [])
  const [position, setPosition] = useState({ x: 0, y: 0 })
  const dragRef = useRef({ isDragging: false, startX: 0, startY: 0, initialX: 0, initialY: 0, moved: false })
  const [toasts, setToasts] = useState([])
  const toastIdRef = useRef(0)

  const addToast = useCallback((msg) => {
    const id = ++toastIdRef.current
    setToasts(prev => [...prev, { id, ...msg }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 8000)
  }, [])

  const dismissToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const handleMouseDown = (e) => {
    dragRef.current.isDragging = true
    dragRef.current.moved = false
    dragRef.current.startX = e.clientX
    dragRef.current.startY = e.clientY
    dragRef.current.initialX = position.x
    dragRef.current.initialY = position.y
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }

  const handleMouseMove = (e) => {
    if (!dragRef.current.isDragging) return
    const dx = e.clientX - dragRef.current.startX
    if (Math.abs(dx) > 3) {
      dragRef.current.moved = true
    }
    setPosition(prev => ({
      ...prev,
      x: dragRef.current.initialX + dx
    }))
  }

  const handleMouseUp = () => {
    dragRef.current.isDragging = false
    document.removeEventListener('mousemove', handleMouseMove)
    document.removeEventListener('mouseup', handleMouseUp)
  }

  const handleFabClick = () => {
    if (!dragRef.current.moved) {
      setChatOpen(prev => !prev)
    }
  }

  useEffect(() => {
    const BASE = import.meta.env.PROD ? '/api' : (import.meta.env.VITE_API_URL ?? 'http://localhost:8000')
    const check = () =>
      fetch(`${BASE}/health`, { signal: AbortSignal.timeout(4000) })
        .then(r => setApiOk(r.ok))
        .catch(() => setApiOk(false))
    check()
    const id = setInterval(check, 30000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const BASE = import.meta.env.PROD ? '/api' : (import.meta.env.VITE_API_URL ?? 'http://localhost:8000')
    const es = new EventSource(`${BASE}/notifications/stream`)
    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        const toast = { ...msg }
        if (msg.type === 'alert') {
          toast.actionLabel = 'AI 분석'
          toast.onAction = () => {
            // 알림 내용을 챗봇으로 넘겨 자동 분석 — 빈 창 문제 해결
            const m = (msg.message || '').replace(/상세 원인을 분석할까요\??/g, '').trim()
            setChatSeed(`방금 이상 알림이 발생했어:\n"${m}"\n이 이상의 원인과 권장 조치를 분석해줘.`)
            setChatOpen(true)
          }
        }
        addToast(toast)
      } catch {}
    }
    es.onerror = () => {}
    return () => es.close()
  }, [addToast, setChatOpen])

  const handleTab = (id) => {
    visited.current.add(id)
    setTab(id)
  }

  // ── 인증 게이트 ──────────────────────────────────────────────
  if (!authReady) return null                       // 토큰 검증 중 (깜빡임 방지)
  if (!user) return <LoginScreen onLogin={setUser} />

  return (
    <div style={styles.app}>
      <aside style={styles.sidebar}>
        <div style={styles.logo}>
          <div style={styles.logoIcon}><Zap size={18} color="#fff" strokeWidth={2.5} /></div>
          <div>
            <div style={styles.logoTitle}>TTF-FMS</div>
            <div style={styles.logoSub}>Facility Management</div>
          </div>
        </div>
        <div style={styles.profileBox}>
          <div style={styles.avatar}>{(user.name || 'U').slice(0, 1)}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: T.text, textOverflow: 'ellipsis', whiteSpace: 'nowrap', overflow: 'hidden' }}>{user.name}</div>
            <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>{ROLE_LABEL[user.role] ?? user.role}</div>
          </div>
          <button onClick={handleLogout} title="로그아웃" style={styles.logoutBtn}>
            <LogOut size={15} />
          </button>
        </div>
        <nav style={styles.nav}>
          {TABS.map(t => {
            if (t.isDivider) {
              return <div key={t.id} style={{ margin: '10px 12px 6px', borderBottom: `1px solid ${T.border}` }} />
            }
            const Ic = t.icon
            const active = tab === t.id
            return (
              <button
                key={t.id}
                style={{ ...styles.navBtn, ...(active ? styles.navActive : {}) }}
                onClick={() => handleTab(t.id)}
              >
                <Ic size={17} strokeWidth={active ? 2.4 : 2} color={active ? T.brand : T.textMuted} />
                {t.label}
              </button>
            )
          })}
        </nav>

        <div style={styles.sidebarFooter}>
          <div style={{
            ...styles.footerDot,
            background: apiOk === null ? T.warning : apiOk ? T.success : T.danger,
          }} />
          <span style={{ fontSize: 12, color: T.textMuted }}>
            {apiOk === null ? '서버 확인 중' : apiOk ? '서버 연결됨' : '서버 오프라인'}
          </span>
          <button
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            style={styles.themeBtn}
            title={theme === 'dark' ? '라이트 모드로' : '다크 모드로'}
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </div>
      </aside>

      {/* 우측 전체 영역 (메인 컨텐츠) */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* 하단 컨텐츠 영역 */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

          <main style={styles.main}>
            {TABS.map(({ id }) => {
              if (!PANELS[id] || !visited.current.has(id)) return null
              const Panel = PANELS[id]
              const extraProps =
                id === 'equipment'   ? {
                  onEquipmentClick: (item) => {
                    setAnomalyFilter(item?.types?.length ? { name: item.name, types: item.types } : null)
                    handleTab('anomaly')
                  },
                  onWorkOrderCreated: () => setWoVersion(v => v + 1),
                  onFocusEquipment: (item) => setChatContext(item ? { equipmentId: item.id, equipmentName: item.name } : null),
                  onNavigate: handleTab,
                  onAskAI: (q) => { setChatSeed(q); setChatOpen(true) },
                } :
                id === 'maintenance' ? { refreshSignal: woVersion, onNavigate: handleTab, onAskAI: (q) => { setChatSeed(q); setChatOpen(true) } } :
                id === 'anomaly'     ? { equipmentFilter: anomalyFilter } :
                id === 'control'     ? { onAskAI: (q) => { setChatSeed(q); setChatOpen(true) } } :
                id === 'forecast'    ? { onNavigate: handleTab } :
                id === 'report'      ? { onNavigate: handleTab } :
                id === 'dashboard'   ? { onNavigate: handleTab } : {}
              return (
                <div key={id} style={{ display: tab === id ? 'flex' : 'none', flexDirection: 'column', height: '100%' }}>
                  {Panel ? <Suspense fallback={<PanelSkeleton />}><Panel {...extraProps} /></Suspense> : <div style={{ padding: 20, color: '#8b949e' }}>Component not found</div>}
                </div>
              )
            })}
          </main>
        </div>
      </div>

      {/* 플로팅 챗봇 버튼 (좌우 드래그 가능, 항상 노출) */}
      <button
        style={{
          ...styles.chatFab,
          transform: `translate(${position.x}px, 0)`,
          cursor: 'ew-resize',
          zIndex: 1001
        }}
        onMouseDown={handleMouseDown}
        onClick={handleFabClick}
      >
        {chatOpen ? <X size={24} color="#fff" /> : <Bot size={26} color="#fff" />}
      </button>

      {/* 토스트 알림 컨테이너 */}
      <div style={styles.toastContainer}>
        {toasts.map(t => (
          <Toast key={t.id} toast={t} onDismiss={() => dismissToast(t.id)} />
        ))}
      </div>

      {/* 플로팅 챗봇 창 (좌우 이동 연동) */}
      {chatOpen && (
        <div style={{
          ...styles.chatWindow,
          transform: `translate(${position.x}px, 0)`
        }}>
          <div style={styles.chatHeader}>
            <div style={{ fontWeight: 700, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Bot size={18} color={T.brand} /> AI 코파일럿
            </div>
            <button onClick={() => setChatOpen(false)} style={styles.chatClose}><X size={16} /></button>
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <ChatPanel
              context={chatContext}
              onClearContext={() => setChatContext(null)}
              seedQuestion={chatSeed}
              onSeedConsumed={() => setChatSeed(null)}
              onNavigate={(id) => { handleTab(id); setChatOpen(false) }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

const TOAST_LEVEL_STYLE = {
  HIGH:     { border: '#f85149', icon: 'alert', bg: '#2d1517' },
  CRITICAL: { border: '#f85149', icon: 'alert', bg: '#2d1517' },
  WARNING:  { border: '#d29922', icon: 'warn',  bg: '#2b2409' },
  INFO:     { border: '#1f6feb', icon: 'info',  bg: '#0d1d36' },
}

function Toast({ toast, onDismiss }) {
  const lvl = TOAST_LEVEL_STYLE[toast.level] ?? TOAST_LEVEL_STYLE.INFO
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      padding: '12px 14px', borderRadius: 10, marginTop: 8,
      background: lvl.bg, border: `1px solid ${lvl.border}`,
      boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
      animation: 'slideIn .25s ease',
      maxWidth: 380, wordBreak: 'break-word', pointerEvents: 'auto',
    }}>
      <span style={{ fontSize: 18, flexShrink: 0, marginTop: 1 }}>{lvl.icon}</span>
      <div style={{ flex: 1, fontSize: 13, color: '#e6edf3', lineHeight: 1.5 }}>
        {toast.message}
        {toast.onAction && (
          <div style={{ marginTop: 8 }}>
            <button onClick={() => { toast.onAction(); onDismiss() }} style={{
              padding: '4px 12px', background: '#1f6feb', border: 'none',
              borderRadius: 4, color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer',
            }}>{toast.actionLabel}</button>
          </div>
        )}
      </div>
      <button onClick={onDismiss} style={{
        background: 'none', border: 'none', color: '#6e7681',
        cursor: 'pointer', fontSize: 14, flexShrink: 0, padding: '0 2px',
      }}><X size={12}/></button>
    </div>
  )
}

const styles = {
  app:          { display: 'flex', height: '100vh', overflow: 'hidden', background: T.bg },
  sidebar:      { width: 232, background: T.surface, borderRight: `1px solid ${T.border}`, display: 'flex', flexDirection: 'column', flexShrink: 0, zIndex: 10 },
  logo:         { padding: '18px 18px 16px', display: 'flex', alignItems: 'center', gap: 11, borderBottom: `1px solid ${T.border}` },
  logoIcon:     { width: 34, height: 34, background: `linear-gradient(135deg, ${T.brand}, ${T.brandStrong})`, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 2px 6px rgba(13,148,136,.3)' },
  logoTitle:    { fontSize: 15, fontWeight: 800, color: T.text, letterSpacing: -0.2 },
  logoSub:      { fontSize: 10, color: T.textFaint, fontWeight: 600, marginTop: 1 },
  profileBox:   { padding: '11px 14px', margin: '14px 12px 8px', background: T.surface2, border: `1px solid ${T.border}`, borderRadius: 10, display: 'flex', alignItems: 'center', gap: 11 },
  avatar:       { width: 32, height: 32, borderRadius: '50%', background: `linear-gradient(135deg, ${T.brand}, ${T.accent})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, color: '#fff', fontWeight: 700, flexShrink: 0 },
  logoutBtn:    { width: 26, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', border: `1px solid ${T.border}`, borderRadius: 7, color: T.textMuted, cursor: 'pointer', flexShrink: 0 },
  nav:          { flex: 1, padding: '4px 12px 12px', display: 'flex', flexDirection: 'column', gap: 2, overflowY: 'auto' },
  navBtn:       { display: 'flex', alignItems: 'center', gap: 11, width: '100%', padding: '9px 12px', background: 'transparent', border: 'none', borderRadius: 8, color: T.textMuted, fontSize: 13, fontWeight: 500, textAlign: 'left', cursor: 'pointer', transition: 'all .15s ease' },
  navActive:    { background: T.brandSoft, color: T.brandStrong, fontWeight: 700 },
  sidebarFooter:{ padding: '13px 16px', borderTop: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', gap: 8 },
  footerDot:    { width: 7, height: 7, borderRadius: '50%', flexShrink: 0 },
  themeBtn:     { marginLeft: 'auto', width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'transparent', border: `1px solid ${T.border}`, borderRadius: 7, color: T.textMuted, cursor: 'pointer', flexShrink: 0 },

  main:         { flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
  chatFab: {
    position: 'fixed', bottom: 24, right: 24, width: 58, height: 58,
    borderRadius: '50%', background: `linear-gradient(135deg, ${T.brand}, ${T.brandStrong})`,
    border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center',
    cursor: 'pointer', boxShadow: '0 6px 18px rgba(13,148,136,.4)', zIndex: 1000
  },
  chatWindow: {
    position: 'fixed', bottom: 96, right: 24, width: 420, height: 650,
    background: T.surface, border: `1px solid ${T.border}`, borderRadius: 16,
    boxShadow: T.shadowMd, zIndex: 1000,
    display: 'flex', flexDirection: 'column', overflow: 'hidden'
  },
  chatHeader: {
    padding: '14px 18px', background: T.surface, borderBottom: `1px solid ${T.border}`,
    display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: T.text
  },
  chatClose: {
    background: 'none', border: 'none', color: T.textMuted, cursor: 'pointer', display: 'flex'
  },
  toastContainer: {
    position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
    zIndex: 2000, display: 'flex', flexDirection: 'column-reverse', alignItems: 'center',
    pointerEvents: 'none',
  },
}
