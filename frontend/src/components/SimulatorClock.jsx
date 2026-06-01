import { useEffect, useState, useCallback } from 'react'
import {
  getSimulatorStatus, startSimulator, pauseSimulator,
  resetSimulator, setSimulatorSpeed, seekSimulator,
} from '../api/client'

const SPEEDS = [
  { label: '1×',  value: 3600,  hint: '1초 = 1시간'  },
  { label: '6×',  value: 21600, hint: '1초 = 6시간'  },
  { label: '24×', value: 86400, hint: '1초 = 1일'    },
]

function fmt(iso) {
  if (!iso) return '–'
  // "2023-06-15T14:23:00" → "2023-06-15 14:23"
  return iso.slice(0, 16).replace('T', ' ')
}

export default function SimulatorClock({ onAnomalyClick } = {}) {
  const [status, setStatus] = useState(null)
  const [busy,   setBusy]   = useState(false)

  const pull = useCallback(() => {
    getSimulatorStatus()
      .then(r => setStatus(r.data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    pull()
    // 실행 중일 때만 빠르게 폴링, 일시정지면 느리게
    const id = setInterval(pull, status?.running ? 5000 : 15000)
    return () => clearInterval(id)
  }, [pull, status?.running])

  const wrap = (fn) => async () => {
    setBusy(true)
    try { await fn() } catch {}
    pull()
    setTimeout(() => setBusy(false), 200)
  }

  if (!status) return null

  const running = !!status.running

  return (
    <div style={s.wrap}>
      {/* 시각 디스플레이 */}
      <div style={{ ...s.clock, color: running ? '#3fb950' : '#5a6675' }}>
        <span style={{ fontSize: 10, color: '#909aa8', marginRight: 6 }}>SIM</span>
        <span style={s.time}>{fmt(status.now)}</span>
        {running && <span style={s.livedot} />}
      </div>

      {/* 컨트롤 버튼 */}
      <div style={s.controls}>
        {running ? (
          <button onClick={wrap(pauseSimulator)} disabled={busy} style={{ ...s.btn, ...s.btnPause }} title="일시정지">⏸</button>
        ) : (
          <button onClick={wrap(startSimulator)} disabled={busy} style={{ ...s.btn, ...s.btnPlay }} title="시작">▶</button>
        )}
        <button onClick={wrap(resetSimulator)} disabled={busy} style={s.btn} title="리셋 (2023-01-01)">⏮</button>

        {/* 날짜 점프 — Seek */}
        <input
          type="date"
          min="2023-01-01"
          max="2023-12-31"
          value={status.now?.slice(0, 10) || '2023-01-01'}
          onChange={(e) => {
            const v = e.target.value
            if (!v) return
            wrap(() => seekSimulator(`${v}T00:00:00`))()
          }}
          disabled={busy}
          style={s.dateInput}
          title="원하는 날짜로 점프"
        />

        {/* 속도 선택 */}
        <div style={s.speedGroup}>
          {SPEEDS.map(sp => (
            <button
              key={sp.value}
              onClick={wrap(() => setSimulatorSpeed(sp.value))}
              disabled={busy}
              style={{
                ...s.speedBtn,
                ...(status.speed === sp.value ? s.speedActive : {}),
              }}
              title={sp.hint}
            >
              {sp.label}
            </button>
          ))}
        </div>
      </div>

      {/* 워커 통계 */}
      {status.worker && (
        <div style={s.stats}>
          <span style={s.statItem} title={`마지막 검사 시각: ${status.worker.last_check_at?.slice(11, 19) ?? '-'}`}>
            🔍 검사 {status.worker.checks}회
          </span>
          {status.worker.anomalies_found > 0 && (
            <button
              onClick={onAnomalyClick}
              style={{ ...s.statItem, ...s.alertBtn }}
              title="알림 내역 보기"
            >
              🚨 이상 {status.worker.anomalies_found}건
            </button>
          )}
        </div>
      )}
    </div>
  )
}

const s = {
  wrap:        { display: 'flex', alignItems: 'center', gap: 12, padding: '8px 14px', background: '#ffffff', border: '1px solid #e2e7ef', borderRadius: 8, fontFamily: 'monospace' },
  clock:       { display: 'flex', alignItems: 'center', fontSize: 12, fontWeight: 600, gap: 6 },
  time:        { fontVariantNumeric: 'tabular-nums', letterSpacing: 0.5 },
  livedot:     { width: 6, height: 6, borderRadius: '50%', background: '#3fb950', marginLeft: 4, animation: 'pulse 1.4s ease-in-out infinite' },

  controls:    { display: 'flex', alignItems: 'center', gap: 4 },
  btn:         { width: 24, height: 24, padding: 0, background: '#e7ebf1', border: '1px solid #e2e7ef', borderRadius: 4, color: '#1b2433', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  btnPlay:     { background: '#2563eb', border: '1px solid #2563eb', color: '#fff' },
  btnPause:    { background: '#d29922', border: '1px solid #d29922', color: '#fff' },

  speedGroup:  { display: 'flex', gap: 2, marginLeft: 4 },
  speedBtn:    { padding: '3px 6px', background: 'transparent', border: '1px solid #e2e7ef', borderRadius: 3, color: '#5a6675', fontSize: 10, cursor: 'pointer', fontWeight: 600, fontFamily: 'monospace' },
  speedActive: { background: '#2563eb33', color: '#2563eb', borderColor: '#2563eb66' },

  stats:       { display: 'flex', gap: 8, paddingLeft: 8, borderLeft: '1px solid #e2e7ef', fontSize: 10, color: '#5a6675' },
  statItem:    { display: 'inline-flex', alignItems: 'center', gap: 3 },
  alertBtn:    { background: '#f8514922', border: '1px solid #f8514955', borderRadius: 4, padding: '2px 8px', color: '#f85149', fontSize: 10, fontWeight: 700, cursor: 'pointer', fontFamily: 'monospace' },
  dateInput:   { padding: '2px 4px', background: '#f4f6fa', border: '1px solid #e2e7ef', borderRadius: 4, color: '#2563eb', fontSize: 10, marginLeft: 6, fontFamily: 'monospace', colorScheme: 'light' },
}
