import axios from 'axios'

// 개발: localhost:8000 직접, 프로덕션(Docker): Nginx 프록시 경유 /api
export const BASE = import.meta.env.PROD
  ? '/api'
  : import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
export const api = axios.create({ baseURL: BASE, timeout: 30000 })

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
export const runDetection       = (start, end) => api.post('/anomalies/run', null, { params: { start, end } })
export const getDetectionStatus = (jobId) => api.get(`/anomalies/run/status/${jobId}`)

export const getForecastModels  = () => api.get('/forecast/models')
export const getForecastStatus  = () => api.get('/forecast/train/status')
export const trainModel         = (model, start = '2018-01-01', end = '2024-01-01', horizon = 24) =>
  api.post(`/forecast/train/${model}`, null, { params: { start, end, horizon } })
export const getForecastCompare = (hours = 24, start = '2023-01-01', end = '2024-01-01') =>
  api.get('/forecast/compare', { params: { hours, start, end } })
export const getForecastBacktest = (trainEnd = '2020-12-31', testEnd = '2023-12-31', freq = 'W') =>
  api.get('/forecast/backtest', { params: { train_end: trainEnd, test_end: testEnd, freq }, timeout: 120000 })
export const predictModel       = (model, hours = 24, start = '2023-01-01', end = '2024-01-01') =>
  api.get(`/forecast/predict/${model}`, { params: { hours, start, end } })
