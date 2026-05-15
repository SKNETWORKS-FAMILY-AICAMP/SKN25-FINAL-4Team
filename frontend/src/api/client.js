import axios from 'axios'

// 개발: localhost:8000 직접, 프로덕션(Docker): Nginx 프록시 경유 /api
const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const api = axios.create({ baseURL: BASE })

export const sendChat    = (question, history = []) => api.post('/chat', { question, history })
export const getAnomalies = (limit = 50, severity, year, month, offset = 0) =>
  api.get('/anomalies', { params: { limit, offset, ...(severity && { severity }), ...(year && { year }), ...(month && { month }) } })
export const getAnomalySummary  = () => api.get('/anomalies/summary')
export const getAnomalyTimeline = () => api.get('/anomalies/timeline')
export const getAnomalyTypes    = () => api.get('/anomalies/types')
export const getAnomalyContext  = (id, hours = 24) => api.get(`/anomalies/${id}/context`, { params: { hours } })
export const getReport   = (months = 12) => api.get('/report', { params: { months } })
