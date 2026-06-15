import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid
} from 'recharts'
import { BASE, getToken } from '../../api/client'

const SEV_COLORS = {
  HIGH: '#f85149',
  MEDIUM: '#d29922',
  LOW: '#2563eb',
  CRITICAL: '#f85149'
}

const TYPE_COLORS = [
  '#2563eb', '#39c5cf', '#3fb950', '#a371f7', '#d29922', '#f85149'
]

export default function anomaly_chart_panel() {
  const [activeTab, setActiveTab] = useState('summary')
  const [summaryData, setSummaryData] = useState([])
  const [typesData, setTypesData] = useState([])
  const [timelineData, setTimelineData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    async function fetchData() {
      try {
        setLoading(true)
        const authHeader = getToken() ? { Authorization: `Bearer ${getToken()}` } : {}
        const [resSum, resTypes, resTime] = await Promise.all([
          fetch(`${BASE}/anomalies/summary`, { headers: authHeader }),
          fetch(`${BASE}/anomalies/types`, { headers: authHeader }),
          fetch(`${BASE}/anomalies/timeline`, { headers: authHeader }),
        ])

        if (!resSum.ok || !resTypes.ok || !resTime.ok) {
          throw new Error('API 호출에 실패했습니다.')
        }

        const dataSum = await resSum.json()
        const dataTypes = await resTypes.json()
        const dataTime = await resTime.json()

        setSummaryData(dataSum.summary || [])
        setTypesData(dataTypes.types || [])
        setTimelineData(dataTime.timeline || [])
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  if (loading) {
    return (
      <div style={s.center}>
        <div style={s.spinner}></div>
        <span style={s.loadingText}>이상 분석 통계를 불러오고 있어요...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div style={s.center}>
        <span style={{ color: '#f85149', fontSize: 13 }}>⚠ {error}</span>
      </div>
    )
  }

  // 1. 심각도 탭 렌더링
  const renderSummary = () => {
    if (summaryData.length === 0) return <div style={s.noData}>데이터가 없습니다.</div>

    // 도넛 차트용 데이터 포맷
    const chartData = summaryData.map(d => ({
      name: d.severity,
      value: parseInt(d.count, 10)
    }))

    return (
      <div style={s.chartBox}>
        <div style={s.chartTitle}>🚨 심각도 등급별 감지 분포</div>
        <div style={{ width: '100%', height: 260 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={90}
                paddingAngle={4}
                dataKey="value"
                label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
              >
                {chartData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={SEV_COLORS[entry.name] || '#6e7681'} />
                ))}
              </Pie>
              <Tooltip formatter={(val) => [`${val}건`, '건수']} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>
    )
  }

  // 2. 유형 탭 렌더링
  const renderTypes = () => {
    if (typesData.length === 0) return <div style={s.noData}>데이터가 없습니다.</div>

    // 유형 한글화 맵
    const CAUSE_MAP = {
      COPDrop: '효율 저하',
      CHPOutage: 'CHP 정지',
      PowerSpike: '전력 급등',
      NightConsumption: '야간 과소비',
      PVNightNonZero: 'PV 야간 오류',
      Unknown: '미분류'
    }

    const chartData = typesData.map(d => ({
      name: CAUSE_MAP[d.type] || d.type,
      count: d.count
    }))

    return (
      <div style={s.chartBox}>
        <div style={s.chartTitle}>🔍 이상 유형별 발생 통계</div>
        <div style={{ width: '100%', height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={chartData} layout="vertical" margin={{ left: 15, right: 15 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis type="number" stroke="var(--text3)" />
              <YAxis dataKey="name" type="category" width={80} stroke="var(--text3)" style={{ fontSize: 11 }} />
              <Tooltip formatter={(val) => [`${val}건`, '건수']} />
              <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                {chartData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={TYPE_COLORS[index % TYPE_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    )
  }

  // 3. 월별 추이 탭 렌더링
  const renderTimeline = () => {
    if (timelineData.length === 0) return <div style={s.noData}>데이터가 없습니다.</div>

    return (
      <div style={s.chartBox}>
        <div style={s.chartTitle}>📅 월간 이상 감지 누적 추이</div>
        <div style={{ width: '100%', height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={timelineData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="month" stroke="var(--text3)" style={{ fontSize: 11 }} />
              <YAxis stroke="var(--text3)" />
              <Tooltip formatter={(val) => [`${val}건`, '건수']} />
              <Legend verticalAlign="top" height={36} />
              <Bar dataKey="HIGH" name="심각 (HIGH)" stackId="a" fill={SEV_COLORS.HIGH} />
              <Bar dataKey="MEDIUM" name="경고 (MEDIUM)" stackId="a" fill={SEV_COLORS.MEDIUM} />
              <Bar dataKey="LOW" name="경미 (LOW)" stackId="a" fill={SEV_COLORS.LOW} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    )
  }

  return (
    <div style={s.container}>
      {/* 탭 버튼 */}
      <div style={s.tabHeader}>
        <button
          style={{ ...s.tabBtn, ...(activeTab === 'summary' ? s.tabBtnActive : {}) }}
          onClick={() => setActiveTab('summary')}
        >
          🚨 심각도 비율
        </button>
        <button
          style={{ ...s.tabBtn, ...(activeTab === 'types' ? s.tabBtnActive : {}) }}
          onClick={() => setActiveTab('types')}
        >
          🔍 유형별 현황
        </button>
        <button
          style={{ ...s.tabBtn, ...(activeTab === 'timeline' ? s.tabBtnActive : {}) }}
          onClick={() => setActiveTab('timeline')}
        >
          📅 월별 트렌드
        </button>
      </div>

      {/* 차트 영역 */}
      <div style={s.tabContent}>
        {activeTab === 'summary' && renderSummary()}
        {activeTab === 'types' && renderTypes()}
        {activeTab === 'timeline' && renderTimeline()}
      </div>
    </div>
  )
}

const s = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    height: '100%',
    background: 'var(--surface)',
    borderRadius: 8,
    border: '1px solid var(--border)',
    overflow: 'hidden'
  },
  tabHeader: {
    display: 'flex',
    background: 'var(--line)',
    borderBottom: '1px solid var(--border)',
    padding: '4px 8px 0',
    gap: 4
  },
  tabBtn: {
    padding: '8px 16px',
    background: 'none',
    border: 'none',
    borderRadius: '6px 6px 0 0',
    color: 'var(--text3)',
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 500,
    outline: 'none',
    transition: 'all 0.15s ease'
  },
  tabBtnActive: {
    background: 'var(--surface)',
    color: 'var(--text)',
    borderLeft: '1px solid var(--border)',
    borderRight: '1px solid var(--border)',
    borderTop: '1px solid var(--border)',
    fontWeight: 600
  },
  tabContent: {
    flex: 1,
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    background: 'var(--surface)'
  },
  chartBox: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%'
  },
  chartTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: 'var(--text)',
    marginBottom: 12,
    textAlign: 'center'
  },
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: 300,
    gap: 12,
    color: 'var(--text3)'
  },
  spinner: {
    width: 28,
    height: 28,
    borderRadius: '50%',
    border: '3px solid var(--line)',
    borderTopColor: '#2563eb',
    animation: 'spin 1s linear infinite'
  },
  loadingText: {
    fontSize: 12,
    color: 'var(--text3)'
  },
  noData: {
    textAlign: 'center',
    color: 'var(--text4)',
    fontSize: 12,
    padding: 20
  }
}
