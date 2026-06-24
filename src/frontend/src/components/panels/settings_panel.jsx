import { useState, useEffect } from 'react'
import { Settings, Cpu, Building2, CalendarClock, Bell, CheckCircle, XCircle, Loader2, Zap, BrainCircuit } from 'lucide-react'
import { getSettings, updateSettings, testLlm, trainModel, getForecastStatus } from '../../api/client'

const PROVIDERS = [
  { value: 'openai',    label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'gemini',    label: 'Google Gemini' },
  { value: 'ollama',    label: 'Ollama (로컬/RunPod)' },
]
const HOURS   = Array.from({ length: 24 }, (_, i) => i)
const MINUTES = [0, 15, 30, 45]

export default function SettingsPanel() {
  const [draft,      setDraft]      = useState(null)
  const [saving,     setSaving]     = useState(false)
  const [saveResult, setSaveResult] = useState(null)   // null | 'ok' | 'err'
  const [testing,    setTesting]    = useState(false)
  const [testResult, setTestResult] = useState(null)   // null | {ok, latency_ms?, error?}
  const [keyInput,   setKeyInput]   = useState('')      // API 키 입력 (저장 시에만 전송)
  const [trainStatus,  setTrainStatus]  = useState({})   // { 'v84-1h': 'idle'|'running'|'done', ... }
  const [training,     setTraining]     = useState({})   // { 1: false, 3: false }
  const [trainMsg,     setTrainMsg]     = useState({})   // { 1: '', 3: '' }

  useEffect(() => {
    const poll = () => getForecastStatus().then(r => setTrainStatus(r.data.status ?? {})).catch(() => {})
    poll()
    const id = setInterval(poll, 10000)
    return () => clearInterval(id)
  }, [])

  const handleTrain = async (horizon) => {
    setTraining(t => ({ ...t, [horizon]: true }))
    setTrainMsg(m => ({ ...m, [horizon]: '' }))
    try {
      await trainModel('v84-ensemble', horizon)
      const isRetrain = (trainStatus[`v84-${horizon}h`] ?? 'idle') === 'done'
      setTrainMsg(m => ({ ...m, [horizon]: isRetrain
        ? `${horizon}h 재학습 시작 — LSTM 스킵으로 수 분 내 완료 예정`
        : `${horizon}h 학습 시작 — 완료까지 20~40분 소요 (LSTM 6종 포함)` }))
      setTrainStatus(s => ({ ...s, [`v84-${horizon}h`]: 'running' }))
    } catch (e) {
      setTrainMsg(m => ({ ...m, [horizon]: '요청 실패: ' + (e.message ?? '') }))
    } finally {
      setTraining(t => ({ ...t, [horizon]: false }))
    }
  }

  useEffect(() => {
    getSettings().then(r => setDraft(r.data)).catch(console.error)
  }, [])

  if (!draft) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text3)' }}>
      <Loader2 size={20} style={{ marginRight: 8, animation: 'spin 1s linear infinite' }} /> 설정 불러오는 중...
    </div>
  )

  const set = (section, key, value) =>
    setDraft(d => ({ ...d, [section]: { ...d[section], [key]: value } }))

  const handleSave = async () => {
    setSaving(true); setSaveResult(null)
    try {
      const payload = keyInput.trim()
        ? { ...draft, llm: { ...draft.llm, api_keys: { [draft.llm.provider]: keyInput.trim() } } }
        : draft
      await updateSettings(payload)
      setKeyInput('')
      const r = await getSettings()   // api_key_set 등 상태 갱신
      setDraft(r.data)
      setSaveResult('ok')
      setTimeout(() => setSaveResult(null), 3000)
    } catch {
      setSaveResult('err')
    } finally {
      setSaving(false)
    }
  }

  const handleTestLlm = async () => {
    setTesting(true); setTestResult(null)
    try {
      const r = await testLlm({
        provider:   draft.llm.provider,
        model:      draft.llm.model,
        ollama_url: draft.llm.ollama_url,
      })
      setTestResult(r.data)
    } catch (e) {
      setTestResult({ ok: false, error: e.message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: 'var(--bg)' }}>
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '28px 32px 48px' }}>

        {/* 헤더 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 28 }}>
          <Settings size={20} color="var(--brand)" />
          <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: 0 }}>시스템 설정</h2>
        </div>

        {/* ── LLM 설정 ── */}
        <Section icon={<Cpu size={15} color="#2563eb" />} title="LLM 설정" color="#2563eb"
          desc="AI 응답에 사용할 언어 모델 프로바이더와 엔드포인트를 설정합니다.">

          <Row label="프로바이더">
            <select value={draft.llm.provider} onChange={e => { set('llm', 'provider', e.target.value); setTestResult(null); setKeyInput('') }}
              style={selectStyle}>
              {PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </Row>

          <Row label="모델명">
            <input value={draft.llm.model} onChange={e => set('llm', 'model', e.target.value)}
              placeholder="gpt-4o / exaone3.5:7.8b / ..."
              style={inputStyle} />
          </Row>

          {draft.llm.provider !== 'ollama' && (
            <Row label="API 키">
              <input type="password" value={keyInput} onChange={e => setKeyInput(e.target.value)}
                placeholder={draft.llm.api_key_set?.[draft.llm.provider] ? '설정됨 — 변경하려면 새 키 입력' : '키를 입력하세요 (sk-...)'}
                style={inputStyle} autoComplete="off" />
              <div style={{ fontSize: 11, color: draft.llm.api_key_set?.[draft.llm.provider] ? '#16a34a' : 'var(--text4)', marginTop: 4 }}>
                현재: {draft.llm.api_key_set?.[draft.llm.provider] ? '설정됨' : '미설정'} · 저장 시 적용됩니다 (키는 화면에 표시되지 않음)
              </div>
            </Row>
          )}

          {draft.llm.provider === 'ollama' && (
            <Row label="Ollama 엔드포인트 URL">
              <input value={draft.llm.ollama_url} onChange={e => set('llm', 'ollama_url', e.target.value)}
                placeholder="https://xxx-11434.proxy.runpod.net/v1"
                style={{ ...inputStyle, maxWidth: 480 }} />
              <div style={{ fontSize: 11, color: 'var(--text4)', marginTop: 4 }}>
                RunPod: Connect → HTTP Service → 포트 11434 URL + /v1
              </div>
            </Row>
          )}

          <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <button onClick={handleTestLlm} disabled={testing} style={secondaryBtnStyle}>
              {testing
                ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> 테스트 중...</>
                : <><Zap size={13} /> 연결 테스트</>}
            </button>
            {testResult && (
              testResult.ok
                ? <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#16a34a' }}>
                    <CheckCircle size={14} /> 연결 성공 · {testResult.latency_ms}ms
                  </span>
                : <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#dc2626' }}>
                    <XCircle size={14} /> 실패 — {testResult.error}
                  </span>
            )}
          </div>
        </Section>

        {/* ── 기업 프로필 ── */}
        <Section icon={<Building2 size={15} color="#7c3aed" />} title="기업 프로필" color="#7c3aed"
          desc="플랫폼에 표시될 기업명을 설정합니다.">
          <Row label="기업명">
            <input value={draft.profile.company_name} onChange={e => set('profile', 'company_name', e.target.value)}
              style={inputStyle} />
          </Row>
        </Section>

        {/* ── 일일 보고서 스케줄 ── */}
        <Section icon={<CalendarClock size={15} color="#0891b2" />} title="일일 보고서 스케줄" color="#0891b2"
          desc="전날 데이터를 기준으로 일일 보고서를 자동 생성하는 시각을 설정합니다.">

          <Row label="자동 생성">
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <div onClick={() => set('report_schedule', 'enabled', !draft.report_schedule.enabled)}
                style={{
                  width: 40, height: 22, borderRadius: 11, cursor: 'pointer', transition: 'background .2s',
                  background: draft.report_schedule.enabled ? 'var(--brand)' : 'var(--border)',
                  position: 'relative', flexShrink: 0,
                }}>
                <div style={{
                  position: 'absolute', top: 3, left: draft.report_schedule.enabled ? 21 : 3,
                  width: 16, height: 16, borderRadius: '50%', background: '#fff', transition: 'left .2s',
                }} />
              </div>
              <span style={{ fontSize: 13, color: 'var(--text2)' }}>
                {draft.report_schedule.enabled ? '활성화' : '비활성화'}
              </span>
            </label>
          </Row>

          {draft.report_schedule.enabled && (
            <Row label="실행 시각">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <select value={draft.report_schedule.hour}
                  onChange={e => set('report_schedule', 'hour', Number(e.target.value))}
                  style={{ ...selectStyle, width: 80 }}>
                  {HOURS.map(h => <option key={h} value={h}>{String(h).padStart(2, '0')}시</option>)}
                </select>
                <span style={{ color: 'var(--text3)' }}>:</span>
                <select value={draft.report_schedule.minute}
                  onChange={e => set('report_schedule', 'minute', Number(e.target.value))}
                  style={{ ...selectStyle, width: 80 }}>
                  {MINUTES.map(m => <option key={m} value={m}>{String(m).padStart(2, '0')}분</option>)}
                </select>
              </div>
            </Row>
          )}
        </Section>

        {/* ── 알림 임계값 ── */}
        <Section icon={<Bell size={15} color="#d97706" />} title="알림 임계값" color="#d97706"
          desc="실시간 이상탐지 알림을 발송할 최소 심각도 기준을 설정합니다.">
          <Row label="알림 기준">
            <div style={{ display: 'flex', gap: 12 }}>
              {[
                { value: 'HIGH',     label: 'HIGH 이상',  desc: 'HIGH + CRITICAL 모두 알림' },
                { value: 'CRITICAL', label: 'CRITICAL만', desc: '최고 심각도만 알림 (소음 감소)' },
              ].map(opt => (
                <label key={opt.value} onClick={() => set('alert', 'min_level', opt.value)}
                  style={{
                    flex: 1, padding: '10px 14px', border: `1.5px solid ${draft.alert.min_level === opt.value ? 'var(--brand)' : 'var(--border)'}`,
                    borderRadius: 8, cursor: 'pointer', background: draft.alert.min_level === opt.value ? 'var(--brand-soft)' : 'var(--bg)',
                    transition: 'all .15s',
                  }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                    <div style={{
                      width: 14, height: 14, borderRadius: '50%', border: `2px solid ${draft.alert.min_level === opt.value ? 'var(--brand)' : 'var(--border)'}`,
                      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                    }}>
                      {draft.alert.min_level === opt.value &&
                        <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--brand)' }} />}
                    </div>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{opt.label}</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', paddingLeft: 20 }}>{opt.desc}</div>
                </label>
              ))}
            </div>
          </Row>
        </Section>

        {/* ── 수요 예측 모델 ── */}
        <Section icon={<BrainCircuit size={15} color="#7c3aed" />} title="수요 예측 모델 관리" color="#7c3aed"
          desc="v84 앙상블 모델(LSTM × 6 + CatBoost + LightGBM + Ridge + Naive) 학습을 실행합니다. 최초 학습은 20~40분, 재학습은 수 분 소요됩니다.">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[1, 3].map(h => {
              const st = trainStatus[`v84-${h}h`] ?? 'idle'
              const isRunning = st === 'running' || st?.startsWith('running')
              const isDone = st === 'done'
              const statusColor = isDone ? '#16a34a' : isRunning ? '#d97706' : 'var(--text4)'
              const statusLabel = isDone ? '학습 완료' : isRunning ? '학습 중...' : '미학습'
              const msg = trainMsg[h]
              return (
                <div key={h} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{h}시간 예측 모델</span>
                    <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 4, background: statusColor + '22', color: statusColor, border: `1px solid ${statusColor}44` }}>
                      {statusLabel}
                    </span>
                  </div>
                  <button
                    onClick={() => handleTrain(h)}
                    disabled={training[h] || isRunning}
                    style={{ ...secondaryBtnStyle, opacity: (training[h] || isRunning) ? 0.5 : 1 }}>
                    {training[h] ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> 요청 중...</>
                      : isDone ? `${h}h 재학습` : `${h}h 학습 시작`}
                  </button>
                  {msg && (
                    <div style={{ width: '100%', fontSize: 12, padding: '6px 10px', borderRadius: 6,
                      ...(msg.includes('실패') ? { background: '#fee2e2', color: '#dc2626', border: '1px solid #fca5a5' }
                        : { background: '#f0fdf4', color: '#16a34a', border: '1px solid #86efac' }) }}>
                      {msg}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </Section>

        {/* ── 저장 버튼 ── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 8 }}>
          <button onClick={handleSave} disabled={saving} style={primaryBtnStyle}>
            {saving
              ? <><Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> 저장 중...</>
              : '설정 저장'}
          </button>
          {saveResult === 'ok' && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 13, color: '#16a34a' }}>
              <CheckCircle size={15} /> 저장되었습니다
            </span>
          )}
          {saveResult === 'err' && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 13, color: '#dc2626' }}>
              <XCircle size={15} /> 저장 실패
            </span>
          )}
        </div>

      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

function Section({ icon, title, color, desc, children }) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 12, padding: '20px 24px', marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
        {icon}
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color }}>{title}</h3>
      </div>
      <p style={{ margin: '0 0 18px', fontSize: 12, color: 'var(--text3)' }}>{desc}</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>{children}</div>
    </div>
  )
}

function Row({ label, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>{label}</label>
      {children}
    </div>
  )
}

const baseInput = {
  padding: '8px 12px', background: 'var(--bg)', border: '1px solid var(--border)',
  color: 'var(--text)', borderRadius: 6, fontSize: 13, outline: 'none',
}
const inputStyle  = { ...baseInput, width: '100%', maxWidth: 360 }
const selectStyle = { ...baseInput, width: '100%', maxWidth: 240, cursor: 'pointer' }

const primaryBtnStyle = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '9px 22px', background: 'var(--brand)', border: 'none',
  color: '#fff', borderRadius: 7, fontSize: 13, fontWeight: 600,
  cursor: 'pointer', transition: 'opacity .15s',
}
const secondaryBtnStyle = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '7px 16px', background: 'var(--bg)', border: '1px solid var(--border)',
  color: 'var(--text2)', borderRadius: 7, fontSize: 12, fontWeight: 500,
  cursor: 'pointer', transition: 'background .15s',
}
