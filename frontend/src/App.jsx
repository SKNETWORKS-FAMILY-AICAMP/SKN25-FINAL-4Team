import { useState, useRef, useEffect } from 'react'
import DashboardPanel  from './components/DashboardPanel'
import ChatPanel       from './components/ChatPanel'
import AnomalyPanel    from './components/AnomalyPanel'
import ReportPanel     from './components/ReportPanel'
import DailyReportPanel from './components/DailyReportPanel'
import ForecastPanel   from './components/ForecastPanel'
import TopologyPanel   from './components/TopologyPanel'

const TABS = [
  { id: 'dashboard', label: '🏠 대시보드' },
  { id: 'chat',      label: '💬 채팅' },
  { id: 'forecast',  label: '🔮 예측' },
  { id: 'anomaly',   label: '⚠️ 이상탐지' },
  { id: 'report',    label: '📊 보고서' },
  { id: 'daily',     label: '📅 일일 보고서' },
  { id: 'topology',  label: '🔌 계량기' },
]

const PANELS = {
  dashboard: DashboardPanel,
  chat:      ChatPanel,
  anomaly:   AnomalyPanel,
  report:    ReportPanel,
  daily:     DailyReportPanel,
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
  app:          { display: 'flex', height: '100vh', overflow: 'hidden', background: 'radial-gradient(circle at 10% 20%, #161b22 0%, #0d1117 100%)' },
  sidebar:      { width: 230, background: 'rgba(22, 27, 34, 0.4)', backdropFilter: 'blur(20px)', borderRight: '1px solid rgba(48, 54, 61, 0.5)', display: 'flex', flexDirection: 'column', flexShrink: 0, zIndex: 10 },
  logo:         { padding: '24px 20px', display: 'flex', alignItems: 'center', gap: 12 },
  logoIcon:     { fontSize: 24, width: 40, height: 40, background: 'linear-gradient(135deg, #1f6feb, #58a6ff)', borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 4px 12px rgba(88, 166, 255, 0.3)' },
  logoTitle:    { fontSize: 14, fontWeight: 700, color: '#e6edf3' },
  logoSub:      { fontSize: 11, color: '#8b949e', letterSpacing: '0.5px' },
  nav:          { flex: 1, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6 },
  navBtn:       { width: '100%', padding: '10px 14px', background: 'transparent', border: 'none', borderRadius: 8, color: '#8b949e', fontSize: 13, fontWeight: 500, textAlign: 'left', cursor: 'pointer', transition: 'all .2s ease' },
  navActive:    { background: 'rgba(31, 111, 235, 0.15)', color: '#58a6ff', fontWeight: 600, boxShadow: 'inset 3px 0 0 #58a6ff' },
  sidebarFooter:{ padding: '14px 16px', borderTop: '1px solid #21262d', display: 'flex', alignItems: 'center', gap: 8 },
  footerDot:    { width: 7, height: 7, borderRadius: '50%', background: '#3fb950' },
  main:         { flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
}
