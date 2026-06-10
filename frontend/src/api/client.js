import axios from 'axios'

// 개발: localhost:8000 직접, 프로덕션(Docker): Nginx 프록시 경유 /api
export const BASE = import.meta.env.PROD
  ? '/api'
  : import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
export const api = axios.create({ baseURL: BASE, timeout: 30000 })

// ── 인증 토큰 관리 ────────────────────────────────────────────────
const TOKEN_KEY = 'auth_token'
export const getToken = () => localStorage.getItem(TOKEN_KEY)
export function setAuthToken(token) {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token)
    api.defaults.headers.common['Authorization'] = `Bearer ${token}`
  } else {
    localStorage.removeItem(TOKEN_KEY)
    delete api.defaults.headers.common['Authorization']
  }
}
// 새로고침 시 저장된 토큰 복원
{
  const _t = getToken()
  if (_t) api.defaults.headers.common['Authorization'] = `Bearer ${_t}`
}

export const login  = (email, password) => api.post('/auth/login', { email, password })
export const logout = () => api.post('/auth/logout').catch(() => {})
export const getMe  = () => api.get('/auth/me')

export const sendChat    = (question, history = []) => api.post('/chat', { question, history })
export const listChatSessions = (search = '', limit = 50) =>
  api.get('/chat/sessions', { params: { ...(search && { search }), limit } })
export const getChatSession   = (sessionId) => api.get(`/chat/sessions/${sessionId}`)
export const deleteChatSession = (sessionId) => api.delete(`/chat/sessions/${sessionId}`)
export const deleteAllChatSessions = () => api.delete('/chat/sessions')

// CMS — 설비 상태 감시
export const getEquipmentStatus = (windowDays = 30) =>
  api.get('/cms/equipment', { params: { window_days: windowDays }, timeout: 60000 })
export const diagnoseEquipment = (id, regenerate = false, windowDays = 30) =>
  api.get(`/cms/equipment/${id}/diagnose`, { params: { window_days: windowDays, regenerate }, timeout: 60000 })
export const getPredictiveMaintenance = (months = 8) =>
  api.get('/cms/predictive', { params: { months }, timeout: 60000 })

// 정비 작업지시
export const createWorkOrder       = (body) => api.post('/cms/work-orders', body)
export const listWorkOrders        = (status, equipmentId, limit = 100) =>
  api.get('/cms/work-orders', { params: { ...(status && { status }), ...(equipmentId && { equipment_id: equipmentId }), limit } })
export const updateWorkOrderStatus = (id, body) => api.post(`/cms/work-orders/${id}/status`, body)
export const getWorkOrderStats     = () => api.get('/cms/work-orders/stats')

export const getControlRecommendations = (hours = 24) =>
  api.get('/control/recommendations', { params: { hours }, timeout: 60000 })
export const approveRecommendation = (id, body = {}) =>
  api.post(`/control/recommendations/${encodeURIComponent(id)}/approve`, body)
export const rejectRecommendation  = (id, body = {}) =>
  api.post(`/control/recommendations/${encodeURIComponent(id)}/reject`, body)
export const getLearningStats      = () => api.get('/control/learning-stats')
export const clearRecommendations  = () => api.delete('/control/recommendations')

// 시뮬레이션 시계
export const getSimulatorStatus = () => api.get('/simulator/status')
export const startSimulator     = () => api.post('/simulator/start')
export const pauseSimulator     = () => api.post('/simulator/pause')
export const resetSimulator     = () => api.post('/simulator/reset')
export const setSimulatorSpeed  = (value) => api.post('/simulator/speed', null, { params: { value } })
export const seekSimulator      = (to) => api.post('/simulator/seek', null, { params: { to } })
export const getAnomalies = (limit = 50, severity, year, month, offset = 0, excludeGf = true, anomalyType) =>
  api.get('/anomalies', { params: { limit, offset, exclude_gf: excludeGf, ...(severity && { severity }), ...(year && { year }), ...(month && { month }), ...(anomalyType && { anomaly_type: anomalyType }) } })
export const getAnomalySummary  = () => api.get('/anomalies/summary')
export const getAnomalyTimeline = () => api.get('/anomalies/timeline')
export const getAnomalyTypes    = () => api.get('/anomalies/types')
export const getAnomalyEvents   = (severity, year, month, gapHours = 2, excludeGf = true) =>
  api.get('/anomalies/events', { params: { ...(severity && { severity }), ...(year && { year }), ...(month && { month }), gap_hours: gapHours, exclude_gf: excludeGf } })
export const getAnomalyContext  = (id, hours = 24) => api.get(`/anomalies/${id}/context`, { params: { hours } })
export const getReport          = (months = 12, skipAi = true) =>
  api.get('/report', { params: { months, skip_ai: skipAi } })
export const getBalanceReport      = (months = 24, skipAi = true) =>
  api.get('/report/balance', { params: { months, skip_ai: skipAi } })
export const getEnergyIntensity    = (months = 24, skipAi = true) =>
  api.get('/report/energy-intensity', { params: { months, skip_ai: skipAi } })
export const getBilling            = (month, unitPrice, peakPrice, targetEur) =>
  api.get('/report/billing', {
    params: {
      ...(month      && { month }),
      ...(unitPrice != null && { unit_price: unitPrice }),
      ...(peakPrice != null && { peak_price: peakPrice }),
      ...(targetEur != null && { target_eur: targetEur }),
    },
    timeout: 60000,
  })

// 일일 보고서
export const getDailyReport      = (date, regenerate = false) =>
  api.get('/report/daily', { params: { date, regenerate }, timeout: 60000 })
export const aggregateDailyReport = (date) =>
  api.post('/report/daily/aggregate', null, { params: { date }, timeout: 60000 })
export const getDailyReportList  = (limit = 30) => api.get('/report/daily/list', { params: { limit } })
export const getLatestDataDate   = () => api.get('/report/daily/latest-data-date')
export const getSchedulerStatus  = () => api.get('/report/daily/scheduler')
export const runSchedulerNow     = () => api.post('/report/daily/scheduler/run', null, { timeout: 60000 })
export const dailyDownloadUrl    = (date, format) =>
  `${BASE}/report/daily/download?date=${encodeURIComponent(date)}&format=${format}`
export const monthlyDownloadUrl  = (months, format) =>
  `${BASE}/report/download?months=${months}&format=${format}`
export const runDetection       = (start, end) => api.post('/anomalies/run', null, { params: { start, end } })
export const getDetectionStatus = (jobId) => api.get(`/anomalies/run/status/${jobId}`)

export const listUsers   = () => api.get('/users')
export const createUser  = (body) => api.post('/users', body)
export const updateUser  = (id, body) => api.patch(`/users/${id}`, body)
export const deleteUser  = (id) => api.delete(`/users/${id}`)
export const resetUserPassword = (id, password) => api.post(`/users/${id}/password`, { password })

export const getSettings        = () => api.get('/settings')
export const updateSettings     = (body) => api.post('/settings', body)
export const testLlm            = (body = {}) => api.post('/settings/test-llm', body, { timeout: 30000 })

export const getForecastModels  = () => api.get('/forecast/models')
export const getForecastStatus  = () => api.get('/forecast/train/status')
export const trainModel         = (model, horizon = 1, meters = null) =>
  api.post(`/forecast/train/${model}`, null, { params: { horizon, ...(meters ? { meters } : {}) } })
export const predictModel       = (model, meterUrn, horizon = 1) =>
  api.get(`/forecast/predict/${model}`, { params: { meter_urn: meterUrn, horizon } })

// 계통 인입 피크 예측 (Import P-Max v29 — 향후 60분, 4개 논리 계량기)
export const getPeakForecast    = (asOf = null, lookbackDays = 14) =>
  api.get('/forecast/peak', { params: { ...(asOf && { as_of: asOf }), lookback_days: lookbackDays }, timeout: 120000 })
