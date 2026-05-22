import { useState, useRef, useEffect } from 'react'
import DashboardPanel  from './components/DashboardPanel'
import ChatPanel       from './components/ChatPanel'
import AnomalyPanel    from './components/AnomalyPanel'
import ReportPanel     from './components/ReportPanel'
import ForecastPanel   from './components/ForecastPanel'
import TopologyPanel   from './components/TopologyPanel'

const TABS = [
  { id: 'dashboard', label: '🏠 대시보드' },
  { id: 'chat',      label: '💬 채팅' },
  { id: 'forecast',  label: '🔮 예측' },
  { id: 'anomaly',   label: '⚠️ 이상탐지' },
  { id: 'report',    label: '📊 보고서' },
  { id: 'topology',  label: '🔌 계량기' },
]

const PANELS = {
  dashboard: DashboardPanel,
  chat:      ChatPanel,
  anomaly:   AnomalyPanel,
  report:    ReportPanel,
  forecast:  ForecastPanel,
  topology:  TopologyPanel,
}

export default function App() {
  const [tab, setTab]   = useState('dashboard')
  const visited = useRef(new Set(['dashboard']))
  const [apiOk, setApiOk] = useState(null)  // null=확인중, true=연결, false=오류

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

  const handleTab = (id) => {
    visited.current.add(id)
    setTab(id)
  }

  return (
    <div style={styles.app}>
      <aside style={styles.sidebar}>
        <div style={styles.logo}>
          <div style={styles.logoIcon}>⚡</div>
          <div>
            <div style={styles.logoTitle}>EMS Agent</div>
            <div style={styles.logoSub}>Honda R&D Europe</div>
          </div>
        </div>
        <nav style={styles.nav}>
          {TABS.map(t => (
            <button
              key={t.id}
              style={{ ...styles.navBtn, ...(tab === t.id ? styles.navActive : {}) }}
              onClick={() => handleTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div style={styles.sidebarFooter}>
          <div style={{
            ...styles.footerDot,
            background: apiOk === null ? '#d29922' : apiOk ? '#3fb950' : '#f85149',
          }} />
          <span style={{ fontSize: 12, color: '#8b949e' }}>
            {apiOk === null ? 'API 확인 중' : apiOk ? 'API 연결됨' : 'API 오프라인'}
          </span>
        </div>
      </aside>

      <main style={styles.main}>
        {TABS.map(({ id }) => {
          if (!visited.current.has(id)) return null
          const Panel = PANELS[id]
          return (
            <div key={id} style={{ display: tab === id ? 'flex' : 'none', flexDirection: 'column', height: '100%' }}>
              <Panel />
            </div>
          )
        })}
      </main>
    </div>
  )
}

const styles = {
  app:          { display: 'flex', height: '100vh', overflow: 'hidden' },
  sidebar:      { width: 220, background: '#161b22', borderRight: '1px solid #21262d', display: 'flex', flexDirection: 'column', flexShrink: 0 },
  logo:         { padding: '20px 16px', borderBottom: '1px solid #21262d', display: 'flex', alignItems: 'center', gap: 10 },
  logoIcon:     { fontSize: 24, width: 36, height: 36, background: '#1f6feb22', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' },
  logoTitle:    { fontSize: 14, fontWeight: 700, color: '#e6edf3' },
  logoSub:      { fontSize: 11, color: '#8b949e' },
  nav:          { flex: 1, padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 4 },
  navBtn:       { width: '100%', padding: '9px 12px', background: 'transparent', border: 'none', borderRadius: 6, color: '#8b949e', fontSize: 13, textAlign: 'left', cursor: 'pointer', transition: 'background .15s, color .15s' },
  navActive:    { background: '#1f6feb22', color: '#58a6ff', fontWeight: 600 },
  sidebarFooter:{ padding: '14px 16px', borderTop: '1px solid #21262d', display: 'flex', alignItems: 'center', gap: 8 },
  footerDot:    { width: 7, height: 7, borderRadius: '50%', background: '#3fb950' },
  main:         { flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
}
