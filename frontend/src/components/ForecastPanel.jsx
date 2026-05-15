import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from 'recharts'
import axios from 'axios'

const api = axios.create({ baseURL: 'http://localhost:8000' })

const MODEL_COLOR = { prophet: '#58a6ff', xgboost: '#3fb950', lstm: '#d29922' }
const MODEL_LABEL = { prophet: 'Prophet', xgboost: 'XGBoost', lstm: 'LSTM' }

const tooltip = {
  contentStyle: { background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e6edf3' },
}

function StatusBadge({ status }) {
  const color = status === 'done' ? '#3fb950' : status === 'running' ? '#d29922' : status?.startsWith('error') ? '#f85149' : '#6e7681'
  const label = status === 'done' ? '학습 완료' : status === 'running' ? '학습 중...' : status?.startsWith('error') ? '오류' : '미학습'
  return <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 4, background: color + '22', color, border: `1px solid ${color}44` }}>{label}</span>
}

export default function ForecastPanel() {
  const [tab,      setTab]      = useState('forecast')  // 'forecast' | 'backtest'
  const [status,   setStatus]   = useState({})
  const [hours,    setHours]    = useState(24)
  const [models,   setModels]   = useState({ prophet: true, xgboost: true, lstm: true })

  // Prophet은 72h 초과 시 자동 비활성화
  useEffect(() => {
    if (hours > 72) setModels(p => ({ ...p, prophet: false }))
    else            setModels(p => ({ ...p, prophet: true  }))
  }, [hours])
  const [forecast, setForecast] = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')
  const [btFreq,   setBtFreq]   = useState('W')
  const [btData,   setBtData]   = useState(null)
  const [btLoading,setBtLoading]= useState(false)

  // 학습 상태 폴링
  useEffect(() => {
    const poll = () => api.get('/forecast/train/status').then(r => setStatus(r.data.status ?? {})).catch(() => {})
    poll()
    const id = setInterval(poll, 3000)
    return () => clearInterval(id)
  }, [])

  const trainAll = async () => {
    await Promise.all(['prophet','xgboost','lstm'].map(m =>
      api.post(`/forecast/train/${m}?start=2018-01-01&end=2024-01-01&horizon=${hours}`).catch(() => {})
    ))
    setStatus(s => ({ ...s, prophet: 'running', xgboost: 'running', lstm: 'running' }))
  }

  const runForecast = async () => {
    setLoading(true); setError(''); setForecast(null)
    try {
      const r = await api.get(`/forecast/compare?hours=${hours}&start=2023-06-01&end=2024-01-01`)
      // 결과 병합: 타임스탬프 기준으로 합치기
      const merged = {}
      for (const [model, rows] of Object.entries(r.data.models ?? {})) {
        if (Array.isArray(rows)) {
          rows.forEach(row => {
            const key = row.ts?.slice(0, 16)
            if (!merged[key]) merged[key] = { ts: key }
            merged[key][model] = row.yhat
          })
        }
      }
      setForecast({
        data:   Object.values(merged).sort((a,b) => a.ts > b.ts ? 1 : -1),
        errors: Object.fromEntries(Object.entries(r.data.models ?? {}).filter(([,v]) => v?.error).map(([k,v]) => [k, v.error])),
      })
    } catch (e) {
      setError('예측 실패: ' + (e.message ?? ''))
    } finally {
      setLoading(false)
    }
  }

  const runBacktest = async () => {
    setBtLoading(true); setBtData(null)
    try {
      const r = await api.get(`/forecast/backtest?freq=${btFreq}`, { timeout: 120000 })
      setBtData(r.data)
    } catch (e) {
      setBtData({ error: e.message })
    } finally {
      setBtLoading(false)
    }
  }

  const activeModels = Object.entries(models).filter(([,v]) => v).map(([k]) => k)
  const allDone = ['prophet','xgboost','lstm'].every(m => status[m] === 'done')

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <span style={s.title}>계통 전력 소비 예측</span>
        <span style={s.sub}>grid_P (kW) · Prophet / XGBoost / LSTM 비교</span>
      </div>

      {/* 탭 */}
      <div style={s.tabRow}>
        {[['forecast','🔮 미래 예측'],['backtest','📊 백테스트 (실제 vs 예측)']].map(([id,label]) => (
          <button key={id} style={{ ...s.tabBtn, ...(tab===id ? s.tabActive : {}) }} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      <div style={s.body}>
        {/* ── 백테스트 탭 ── */}
        {tab === 'backtest' && (<>
          <div style={s.btDesc}>
            <b style={{ color:'#e6edf3' }}>2018~2020 학습 → 2021~2023 예측</b>하여 실제값과 비교합니다.
            XGBoost는 백테스트 전용으로 재학습, LSTM은 저장된 모델로 rolling 예측을 수행합니다.{' '}
            <span style={{ color:'#d29922' }}>Prophet은 단기(≤7일) 전용으로 장기 백테스트에서 제외됩니다.</span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            <span style={{ fontSize:12, color:'#8b949e' }}>집계 단위:</span>
            {[['D','일별'],['W','주별'],['ME','월별']].map(([v,l]) => (
              <button key={v} style={{ ...s.hBtn, ...(btFreq===v ? s.hBtnActive : {}) }} onClick={() => setBtFreq(v)}>{l}</button>
            ))}
            <button style={{ ...s.predictBtn, marginLeft:'auto', opacity: btLoading ? 0.5 : 1 }}
              onClick={runBacktest} disabled={btLoading}>
              {btLoading ? '분석 중... (2~3분 소요)' : '백테스트 실행'}
            </button>
          </div>

          {btData?.mae_kw && (
            <div style={{ display:'flex', gap:10, alignItems:'flex-start' }}>
              {['xgboost','lstm'].filter(m => btData.mae_kw[m] != null).map(m => (
                <div key={m} style={s.maeCard}>
                  <div style={{ fontSize:11, color:'#8b949e' }}>{MODEL_LABEL[m]} MAE</div>
                  <div style={{ fontSize:22, fontWeight:700, color: MODEL_COLOR[m] }}>{btData.mae_kw[m]} <span style={{ fontSize:12 }}>kW</span></div>
                  <div style={{ fontSize:11, color:'#6e7681' }}>평균 절대 오차</div>
                </div>
              ))}
              <div style={{ ...s.maeCard, borderColor:'#d2922244' }}>
                <div style={{ fontSize:11, color:'#8b949e' }}>Prophet</div>
                <div style={{ fontSize:13, fontWeight:600, color:'#d29922', marginTop:4 }}>단기 전용</div>
                <div style={{ fontSize:11, color:'#6e7681', marginTop:2 }}>≤72h 예측에 사용</div>
              </div>
            </div>
          )}

          {btData?.data?.length > 0 && (
            <div style={s.chartBox}>
              <div style={s.chartTitle}>
                실제 vs 예측 — {btData.test_period} ({btFreq === 'D' ? '일별' : btFreq === 'W' ? '주별' : '월별'} 평균 kW)
              </div>
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={btData.data} margin={{ top:8, right:20, left:-10, bottom:0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                  <XAxis dataKey="ts" tick={{ fontSize:10, fill:'#8b949e' }} tickLine={false}
                    interval={Math.floor(btData.data.length / 10)}
                    tickFormatter={v => v?.slice(2,10)}/>
                  <YAxis tick={{ fontSize:10, fill:'#8b949e' }} tickLine={false} axisLine={false}
                    tickFormatter={v => `${v}kW`}/>
                  <Tooltip {...tooltip} formatter={(v,n) => [`${v} kW`, n === 'actual' ? '실제값' : MODEL_LABEL[n] ?? n]}/>
                  <Legend wrapperStyle={{ fontSize:12 }} formatter={v => v === 'actual' ? <span style={{ color:'#e6edf3' }}>실제값</span> : <span style={{ color: MODEL_COLOR[v] }}>{MODEL_LABEL[v]}</span>}/>
                  <Line type="monotone" dataKey="actual"  stroke="#e6edf3" strokeWidth={2} dot={false} connectNulls/>
                  <Line type="monotone" dataKey="xgboost" stroke={MODEL_COLOR.xgboost} strokeWidth={1.5} dot={false} strokeDasharray="4 2" connectNulls/>
                  <Line type="monotone" dataKey="lstm"    stroke={MODEL_COLOR.lstm}    strokeWidth={1.5} dot={false} strokeDasharray="2 3" connectNulls/>
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {btData?.errors && Object.keys(btData.errors).length > 0 && (
            <div style={s.errBox}>{Object.entries(btData.errors).map(([m,e]) => <div key={m}><b>{m}</b>: {e}</div>)}</div>
          )}
        </>)}

        {/* ── 미래 예측 탭 ── */}
        {tab === 'forecast' && (<>
        {/* 모델 상태 카드 */}
        <div style={s.cards}>
          {['prophet','xgboost','lstm'].map(m => (
            <div key={m} style={s.card}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
                <span style={{ fontSize:13, fontWeight:600, color: MODEL_COLOR[m] }}>{MODEL_LABEL[m]}</span>
                <StatusBadge status={status[m]}/>
              </div>
              <div style={{ fontSize:11, color:'#6e7681' }}>
                {m === 'prophet'  && '계절성 분해 + 날씨 회귀'}
                {m === 'xgboost' && 'Lag 피처 + 시간/날씨'}
                {m === 'lstm'    && '다변량 시퀀스 학습'}
              </div>
              <div style={{ display:'flex', gap:4, marginTop:6, flexWrap:'wrap' }}>
                {m === 'prophet' && (
                  <span style={{ fontSize:10, color:'#d29922', background:'#2d2209', borderRadius:4, padding:'2px 6px' }}>단기(≤72h) 전용</span>
                )}
                {m === 'xgboost' && (
                  <span style={{ fontSize:10, color:'#3fb950', background:'#0f2d1a', borderRadius:4, padding:'2px 6px' }}>MAPE 4.7%</span>
                )}
                {m === 'xgboost' && (
                  <span style={{ fontSize:10, color:'#6e7681', background:'#161b22', borderRadius:4, padding:'2px 6px' }}>3년 백테스트</span>
                )}
                {m === 'lstm' && (
                  <span style={{ fontSize:10, color:'#d29922', background:'#2d2209', borderRadius:4, padding:'2px 6px' }}>MAPE 8.5%</span>
                )}
                {m === 'lstm' && (
                  <span style={{ fontSize:10, color:'#6e7681', background:'#161b22', borderRadius:4, padding:'2px 6px' }}>3년 백테스트</span>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* 컨트롤 */}
        <div style={s.controls}>
          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
            <span style={{ fontSize:12, color:'#8b949e' }}>예측 시간:</span>
            {[24, 48, 72, 168].map(h => (
              <button key={h} style={{ ...s.hBtn, ...(hours===h ? s.hBtnActive : {}) }} onClick={() => setHours(h)}>
                {h === 168 ? '7일' : `${h}h`}
              </button>
            ))}
          </div>
          <div style={{ display:'flex', gap:8 }}>
            {!allDone && (
              <button style={s.trainBtn} onClick={trainAll}>
                모델 학습 시작
              </button>
            )}
            <button style={{ ...s.predictBtn, opacity: loading ? 0.5 : 1 }}
              onClick={runForecast} disabled={loading}>
              {loading ? '예측 중...' : '예측 실행'}
            </button>
          </div>
        </div>

        {/* 모델 선택 토글 */}
        <div style={{ display:'flex', gap:10, alignItems:'center' }}>
          {['prophet','xgboost','lstm'].map(m => {
            const disabled = m === 'prophet' && hours > 72
            return (
              <label key={m} style={{ display:'flex', alignItems:'center', gap:5, cursor: disabled ? 'not-allowed' : 'pointer', fontSize:12, opacity: disabled ? 0.4 : 1 }}>
                <input type="checkbox" checked={models[m]} disabled={disabled}
                  onChange={e => setModels(p => ({...p, [m]: e.target.checked}))}/>
                <span style={{ color: MODEL_COLOR[m] }}>{MODEL_LABEL[m]}</span>
                {disabled && <span style={{ fontSize:10, color:'#8b949e' }}>(단기 전용)</span>}
              </label>
            )
          })}
        </div>

        {error && <div style={s.errBox}>{error}</div>}

        {/* 예측 차트 */}
        {forecast?.data?.length > 0 && (
          <div style={s.chartBox}>
            <div style={s.chartTitle}>계통 전력 소비 예측 — 향후 {hours >= 168 ? '7일' : `${hours}시간`} (kW)</div>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={forecast.data} margin={{ top: 8, right: 20, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d"/>
                <XAxis dataKey="ts" tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false}
                  tickFormatter={v => v?.slice(5,16)}
                  interval={Math.floor(forecast.data.length / 8)}/>
                <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} tickLine={false} axisLine={false}
                  tickFormatter={v => `${v}kW`}/>
                <Tooltip {...tooltip} labelFormatter={v => v?.slice(0,16)} formatter={(v,n) => [`${v} kW`, MODEL_LABEL[n] ?? n]}/>
                <Legend wrapperStyle={{ fontSize: 12 }}
                  formatter={v => <span style={{ color: MODEL_COLOR[v] }}>{MODEL_LABEL[v]}</span>}/>
                {activeModels.map(m => (
                  <Line key={m} type="monotone" dataKey={m} name={m}
                    stroke={MODEL_COLOR[m]} strokeWidth={2} dot={false} connectNulls/>
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* 오류 표시 */}
        {forecast?.errors && Object.keys(forecast.errors).length > 0 && (
          <div style={s.errBox}>
            {Object.entries(forecast.errors).map(([m,e]) => (
              <div key={m}><b>{MODEL_LABEL[m]}</b>: {e}</div>
            ))}
          </div>
        )}

        {/* 학습 미완료 안내 */}
        {!allDone && !forecast && (
          <div style={s.guide}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>🤖</div>
            <div style={{ fontSize: 14, color: '#e6edf3', marginBottom: 6 }}>모델 학습이 필요합니다</div>
            <div style={{ fontSize: 12, color: '#8b949e' }}>
              Prophet·XGBoost·LSTM 3개 모델을 6년(2018~2023) 데이터로 학습합니다.<br/>
              Prophet·XGBoost는 수 분, LSTM은 약 10~20분 소요됩니다.
            </div>
          </div>
        )}
        </>)}
      </div>
    </div>
  )
}

const s = {
  wrap:       { display:'flex', flexDirection:'column', height:'100%', overflow:'hidden' },
  header:     { padding:'16px 24px 12px', borderBottom:'1px solid #21262d', display:'flex', alignItems:'center', gap:12, flexShrink:0 },
  title:      { fontWeight:700, fontSize:15, color:'#e6edf3' },
  sub:        { fontSize:12, color:'#8b949e' },
  tabRow:     { display:'flex', gap:4, padding:'8px 24px 0', borderBottom:'1px solid #21262d', flexShrink:0 },
  tabBtn:     { padding:'7px 16px', background:'none', border:'none', borderBottom:'2px solid transparent', color:'#8b949e', fontSize:13, cursor:'pointer' },
  tabActive:  { color:'#58a6ff', borderBottomColor:'#58a6ff', fontWeight:600 },
  body:       { flex:1, overflowY:'auto', padding:'20px 24px', display:'flex', flexDirection:'column', gap:16 },
  btDesc:     { fontSize:13, color:'#8b949e', background:'#161b22', border:'1px solid #21262d', borderRadius:8, padding:'10px 14px' },
  maeCard:    { background:'#161b22', border:'1px solid #21262d', borderRadius:10, padding:'12px 16px', minWidth:140 },
  cards:      { display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:12 },
  card:       { background:'#161b22', border:'1px solid #21262d', borderRadius:10, padding:'14px 16px' },
  controls:   { display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:10 },
  hBtn:       { padding:'4px 12px', background:'#161b22', border:'1px solid #30363d', borderRadius:6, color:'#8b949e', fontSize:12, cursor:'pointer' },
  hBtnActive: { borderColor:'#58a6ff', color:'#58a6ff', background:'#1f6feb22' },
  trainBtn:   { padding:'7px 16px', background:'#21262d', border:'1px solid #30363d', borderRadius:8, color:'#e6edf3', fontWeight:600, cursor:'pointer', fontSize:13 },
  predictBtn: { padding:'7px 20px', background:'#1f6feb', border:'none', borderRadius:8, color:'#fff', fontWeight:600, cursor:'pointer', fontSize:13 },
  chartBox:   { background:'#161b22', border:'1px solid #21262d', borderRadius:10, padding:'16px 18px' },
  chartTitle: { fontSize:13, fontWeight:600, color:'#e6edf3', marginBottom:12 },
  errBox:     { background:'#2d1517', border:'1px solid #f85149', borderRadius:8, padding:'10px 14px', fontSize:12, color:'#f85149' },
  guide:      { flex:1, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', paddingTop:40, textAlign:'center' },
}
