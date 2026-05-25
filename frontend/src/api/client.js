import axios from 'axios'

// 개발: localhost:8000 직접, 프로덕션(Docker): Nginx 프록시 경유 /api
const BASE = import.meta.env.PROD
  ? '/api'
  : import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
export const api = axios.create({ baseURL: BASE })

export const sendChat    = (question, history = []) => api.post('/chat', { question, history })
export const getAnomalies = (limit = 50, severity, year, month, offset = 0, excludeGf = true) =>
  api.get('/anomalies', { params: { limit, offset, exclude_gf: excludeGf, ...(severity && { severity }), ...(year && { year }), ...(month && { month }) } })
export const getAnomalySummary  = () => api.get('/anomalies/summary')
export const getAnomalyTimeline = () => api.get('/anomalies/timeline')
export const getAnomalyTypes    = () => api.get('/anomalies/types')
export const getAnomalyEvents   = (severity, year, month, gapHours = 2, excludeGf = true) =>
  api.get('/anomalies/events', { params: { ...(severity && { severity }), ...(year && { year }), ...(month && { month }), gap_hours: gapHours, exclude_gf: excludeGf } })
export const getAnomalyContext  = (id, hours = 24) => api.get(`/anomalies/${id}/context`, { params: { hours } })
export const getReport          = (months = 12) => api.get('/report', { params: { months } })

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
