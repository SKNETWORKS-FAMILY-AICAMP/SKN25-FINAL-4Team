import { useState, useMemo } from 'react'
import { METER_CATALOG } from '../data/meterCatalog'

// ─── Constants ────────────────────────────────────────────────────────────────

const ROLE_COLOR = {
  production:   '#3fb950',
  consumption:  '#58a6ff',
  distribution: '#d29922',
  grid:         '#f85149',
}

const ROLE_ICON = {
  production:   '↑',
  consumption:  '↓',
  distribution: '⇄',
  grid:         '⟺',
}

const ROLE_LABEL = {
  production:   '생산',
  consumption:  '소비',
  distribution: '배전',
  grid:         '계통',
}

const TYPE_ICON = {
  electricity: '⚡',
  heat:        '🔥',
  cooling:     '❄',
  weather:     '🌤',
}

const BUILDING_TABS = [
  { id: 'all',   label: '전체' },
  { id: 'H1',   label: 'H1 — Emission Lab' },
  { id: 'H2',   label: 'H2 — Workshop' },
  { id: 'H3',   label: 'H3 — Design Studio' },
  { id: 'H4',   label: 'H4 — Office B4' },
  { id: 'V',    label: 'V — 부지 공통' },
]

// ─── Sub-components ───────────────────────────────────────────────────────────

function SummaryCard({ value, label, sub, color = '#58a6ff' }) {
  return (
    <div style={{
      background: '#161b22',
      border: '1px solid #21262d',
      borderRadius: 8,
      padding: '14px 18px',
      minWidth: 130,
      flex: 1,
    }}>
      <div style={{ fontSize: 28, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 13, color: '#e6edf3', marginTop: 4 }}>{label}</div>
      {sub && <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function RoleLegend() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {Object.entries(ROLE_COLOR).map(([role, color]) => (
        <div key={role} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 12, height: 12, borderRadius: 3, background: color, flexShrink: 0 }} />
          <span style={{ fontSize: 12, color: '#e6edf3' }}>
            {ROLE_ICON[role]} {ROLE_LABEL[role]}
          </span>
        </div>
      ))}
      <div style={{ borderTop: '1px solid #21262d', marginTop: 4, paddingTop: 8 }}>
        {Object.entries(TYPE_ICON).map(([type, icon]) => (
          <div key={type} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 14, width: 16, textAlign: 'center' }}>{icon}</span>
            <span style={{ fontSize: 12, color: '#8b949e' }}>{type}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function RoleBreakdown({ meters }) {
  const counts = useMemo(() => {
    const c = { production: 0, consumption: 0, distribution: 0, grid: 0 }
    meters.forEach(m => { if (c[m.role] !== undefined) c[m.role]++ })
    return c
  }, [meters])

  const total = meters.length
  const barWidth = 180

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
      {Object.entries(counts).map(([role, count]) => {
        const pct = total ? Math.round((count / total) * 100) : 0
        return (
          <div key={role}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
              <span style={{ fontSize: 12, color: '#e6edf3' }}>
                {ROLE_ICON[role]} {ROLE_LABEL[role]}
              </span>
              <span style={{ fontSize: 12, color: '#8b949e' }}>{count}개 ({pct}%)</span>
            </div>
            <div style={{ background: '#21262d', borderRadius: 4, height: 7, width: barWidth }}>
              <div style={{
                background: ROLE_COLOR[role],
                borderRadius: 4,
                height: '100%',
                width: `${pct}%`,
                transition: 'width .3s',
              }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

// Energy flow diagram — full-width layout, no horizontal scroll
function FlowDiagram({ building }) {
  const box = (label, color, sub) => (
    <div style={{
      background: '#161b22',
      border: `1px solid ${color}`,
      borderRadius: 6,
      padding: '6px 12px',
      textAlign: 'center',
      minWidth: 120,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>{sub}</div>}
    </div>
  )

  const arrow = (dir = '↓') => (
    <div style={{ fontSize: 18, color: '#484f58', textAlign: 'center', lineHeight: '20px', userSelect: 'none' }}>
      {dir}
    </div>
  )

  if (building === 'H1') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '8px 0' }}>
        {box('외부 계통 전력 (변압기)', ROLE_COLOR.grid, 'V.Z81 / V.Z82')}
        {arrow()}
        {/* Feed meters — V.Z81/Z82 → H1 공급선 (역방향 설치) */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', justifyContent: 'center' }}>
          {box('EM 주 급전 1', ROLE_COLOR.grid, 'H1.Z15 · 29kW')}
          {box('EM 주 급전 2', ROLE_COLOR.grid, 'H1.Z28 · 17kW')}
        </div>
        {arrow()}
        {/* Distribution meters — 건물 내부 배전반 */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', justifyContent: 'center' }}>
          {box('내부 배전 1', ROLE_COLOR.distribution, 'H1.Z17 · 61kW')}
          {box('내부 배전 2', ROLE_COLOR.distribution, 'H1.Z29')}
        </div>
        {arrow()}
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('CHP 발전기', ROLE_COLOR.production, 'H1.Z20 / ZE20')}
            {arrow()}
            {box('CHP 열 회수', ROLE_COLOR.production, 'H1.W12')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('PV 태양광', ROLE_COLOR.production, 'H1.Z310')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('냉각기 부하', ROLE_COLOR.consumption, 'K11/K12/K14~16')}
            {arrow()}
            {box('중앙 냉각기', ROLE_COLOR.distribution, 'V.K21')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('대형 부하', ROLE_COLOR.consumption, 'Z13/Z14/Z26/Z27')}
            {arrow()}
            {box('소형 부하', ROLE_COLOR.consumption, 'Z10/Z18~22')}
          </div>
        </div>
        {arrow()}
        {box('H1 열 공급 총량', ROLE_COLOR.distribution, 'H1.W11 · 247kW avg')}
      </div>
    )
  }

  if (building === 'H2') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '8px 0' }}>
        {box('외부 계통 전력 (부지 변압기)', ROLE_COLOR.grid, 'V.Z81 / V.Z82')}
        {arrow()}
        {/* Feed — 사무동 변압기 급전 미터 (2020.9 이전: Z35/Z36) */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', justifyContent: 'center' }}>
          {box('사무동 변압기 급전 1', ROLE_COLOR.grid, 'H2.Z351 (구: Z35)')}
          {box('사무동 변압기 급전 2', ROLE_COLOR.grid, 'H2.Z361 (구: Z36)')}
        </div>
        {arrow()}
        {/* Internal distribution — T.Z30~Z34 */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap' }}>
          {box('B2 전체', ROLE_COLOR.distribution, 'H2.T.Z30')}
          {box('HVAC 50/51', ROLE_COLOR.distribution, 'H2.T.Z31')}
          {box('로비', ROLE_COLOR.distribution, 'H2.T.Z32')}
          {box('→ H3 급전', ROLE_COLOR.distribution, 'H2.T.Z33')}
          {box('→ 워크샵 급전', ROLE_COLOR.distribution, 'H2.T.Z34')}
        </div>
        {arrow()}
        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('PV 발전 (옥상)', ROLE_COLOR.production, 'H2.Z311')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('CIS 서버룸', ROLE_COLOR.consumption, 'Z61/Z62/Z63')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('EU 서버룸', ROLE_COLOR.consumption, 'Z64/ZE64/Z65/ZE65')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('로컬 냉각', ROLE_COLOR.consumption, 'Z66/ZE66/Z67/ZE67')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('환기', ROLE_COLOR.consumption, 'Z68/Z69/Z70')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('로보랩', ROLE_COLOR.consumption, 'H2.ZE74')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('HVAC 냉수', ROLE_COLOR.consumption, 'H2.K21')}
          </div>
        </div>
      </div>
    )
  }

  if (building === 'H3') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '8px 0' }}>
        {/* H3는 V.Z81/Z82 직접 연결 아님 — H2.T.Z33을 통해 급전 */}
        {box('사무동 급전 경유 (H2)', ROLE_COLOR.grid, 'H2.T.Z33 (Feed design studio)')}
        {arrow()}
        {/* Distribution within H3 */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', justifyContent: 'center' }}>
          {box('디자인 스튜디오 배전 1', ROLE_COLOR.distribution, 'H3.Z(E)40')}
          {box('디자인 스튜디오 배전 4', ROLE_COLOR.distribution, 'H3.Z(E)41')}
        </div>
        {arrow()}
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('PV 발전', ROLE_COLOR.production, 'H3.Z312')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('환기', ROLE_COLOR.consumption, 'H3.Z42')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('서버 O4 전원', ROLE_COLOR.consumption, 'Z46 / Z71')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('서버 O4 냉각', ROLE_COLOR.consumption, 'Z43/ZE43/Z44/ZE44')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('냉각 (설계동)', ROLE_COLOR.consumption, 'H3.Z45')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('드라이빙 시뮬레이터', ROLE_COLOR.consumption, 'Z47/Z48/Z49')}
          </div>
        </div>
      </div>
    )
  }

  if (building === 'H4') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '8px 0' }}>
        {/* H4는 V.Z81/Z82 직접 연결 아님 — H2 사무동 배전(H2.T.Z30) 하위 */}
        {box('사무동 배전 경유 (H2)', ROLE_COLOR.grid, 'H2.T.Z30 (Office B2) 하위')}
        {arrow()}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('B4 배전 3', ROLE_COLOR.distribution, 'H4.Z50 (→ ZE50: 2023.6~)')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('B4 배전 4', ROLE_COLOR.distribution, 'H4.Z51 (→ ZE51: 2023.6~)')}
          </div>
        </div>
      </div>
    )
  }

  if (building === 'V') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '8px 0' }}>
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('외부 계통 전력 (수전)', ROLE_COLOR.grid, 'V.Z81 / V.Z82')}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('PV 발전 (주차장)', ROLE_COLOR.production, 'V.Z84 · 136kWp')}
          </div>
        </div>
        {arrow()}
        {box('부지 공통 배전', ROLE_COLOR.distribution, 'V 빌딩')}
        {arrow()}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {box('중앙 냉각기 총합', ROLE_COLOR.distribution, 'V.K21 · 54kW avg')}
          </div>
        </div>
      </div>
    )
  }

  // 전체 overview
  return (
    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', padding: '4px 0' }}>
      {[
        { id: 'H1', label: 'H1 — Emission Lab',   selfSuf: '74.8%', meters: 29 },
        { id: 'H2', label: 'H2 — Workshop',        selfSuf: '—',     meters: 26 },
        { id: 'H3', label: 'H3 — Design Studio',   selfSuf: '—',     meters: 16 },
        { id: 'H4', label: 'H4 — Office B4',       selfSuf: '—',     meters: 4  },
        { id: 'V',  label: 'V — 부지 공통',          selfSuf: '—',     meters: 5  },
      ].map(b => (
        <div key={b.id} style={{
          background: '#161b22',
          border: '1px solid #21262d',
          borderRadius: 8,
          padding: '10px 14px',
          minWidth: 140,
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3' }}>{b.label}</div>
          <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>계량기 {b.meters}개</div>
          <div style={{ fontSize: 11, color: '#3fb950', marginTop: 2 }}>자립율 {b.selfSuf}</div>
          <div style={{ display: 'flex', gap: 4, marginTop: 8 }}>
            {METER_CATALOG.filter(m => m.building === b.id).some(m => m.role === 'grid') && (
              <span style={{ fontSize: 10, background: '#f8514922', color: '#f85149', borderRadius: 3, padding: '1px 4px' }}>그리드</span>
            )}
            {METER_CATALOG.filter(m => m.building === b.id).some(m => m.role === 'production') && (
              <span style={{ fontSize: 10, background: '#3fb95022', color: '#3fb950', borderRadius: 3, padding: '1px 4px' }}>생산</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function MeterRow({ meter }) {
  const roleColor = ROLE_COLOR[meter.role] || '#8b949e'
  return (
    <tr style={{ borderBottom: '1px solid #21262d' }}>
      <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: 12, color: '#58a6ff', whiteSpace: 'nowrap' }}>
        {meter.id}
      </td>
      <td style={{ padding: '8px 6px', fontSize: 14, textAlign: 'center' }}>
        {TYPE_ICON[meter.type]}
      </td>
      <td style={{ padding: '8px 6px', textAlign: 'center' }}>
        <span style={{
          fontSize: 12,
          fontWeight: 600,
          color: roleColor,
          background: roleColor + '22',
          borderRadius: 4,
          padding: '2px 7px',
          whiteSpace: 'nowrap',
        }}>
          {ROLE_ICON[meter.role]} {ROLE_LABEL[meter.role]}
        </span>
      </td>
      <td style={{ padding: '8px 10px', fontSize: 12, color: '#e6edf3', maxWidth: 200 }}>
        <div>{meter.label}</div>
        {meter.desc && (
          <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>{meter.desc}</div>
        )}
      </td>
      <td style={{ padding: '8px 10px', fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap' }}>
        {meter.avgKw !== null && meter.avgKw !== undefined ? (
          <span style={{ color: meter.avgKw < 0 ? '#3fb950' : '#58a6ff', fontWeight: 600 }}>
            {meter.avgKw > 0 ? '+' : ''}{meter.avgKw} kW
          </span>
        ) : (
          <span style={{ color: '#484f58' }}>—</span>
        )}
      </td>
      <td style={{ padding: '8px 10px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
          {meter.tags.map(tag => (
            <span key={tag} style={{
              fontSize: 10,
              background: '#21262d',
              color: '#8b949e',
              borderRadius: 3,
              padding: '1px 5px',
            }}>
              {tag}
            </span>
          ))}
        </div>
      </td>
    </tr>
  )
}

// ─── Main Panel ───────────────────────────────────────────────────────────────

export default function TopologyPanel() {
  const [activeBuilding, setActiveBuilding] = useState('all')
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')

  const filteredMeters = useMemo(() => {
    let list = METER_CATALOG

    if (activeBuilding !== 'all') {
      list = list.filter(m => m.building === activeBuilding)
    }

    if (roleFilter !== 'all') {
      list = list.filter(m => m.role === roleFilter)
    }

    if (typeFilter !== 'all') {
      list = list.filter(m => m.type === typeFilter)
    }

    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(m =>
        m.id.toLowerCase().includes(q) ||
        m.label.toLowerCase().includes(q) ||
        (m.desc || '').toLowerCase().includes(q) ||
        m.tags.some(t => t.toLowerCase().includes(q))
      )
    }

    return list
  }, [activeBuilding, search, roleFilter, typeFilter])

  // Summary stats (overall)
  const totalElec    = METER_CATALOG.filter(m => m.type === 'electricity').length
  const totalHeatCool = METER_CATALOG.filter(m => m.type === 'heat' || m.type === 'cooling').length
  const totalProduce = METER_CATALOG.filter(m => m.role === 'production').length

  const showSummary = activeBuilding === 'all'

  return (
    <div style={{ background: '#0d1117', minHeight: '100%', padding: 20, overflow: 'auto', color: '#e6edf3' }}>

      {/* ── Header ── */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: '#e6edf3' }}>계량기 토폴로지</div>
        <div style={{ fontSize: 13, color: '#8b949e', marginTop: 4 }}>
          81개 계량기 · 4개 건물 · Honda R&D Europe
        </div>
      </div>

      {/* ── Summary Cards (전체 탭만) ── */}
      {showSummary && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
          <SummaryCard value={81}           label="총 계량기 수"       sub="전체 건물 합계"     color="#e6edf3" />
          <SummaryCard value={totalElec}    label="전기 미터 (Z/ZE)"   sub="electricity"      color="#58a6ff" />
          <SummaryCard value={totalHeatCool} label="열/냉각 미터 (K/W)" sub="heat + cooling"   color="#d29922" />
          <SummaryCard value={totalProduce} label="생산 미터"           sub="PV + CHP"          color="#3fb950" />
        </div>
      )}

      {/* ── Building Tabs ── */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}>
        {BUILDING_TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => { setActiveBuilding(tab.id); setSearch(''); setRoleFilter('all'); setTypeFilter('all') }}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              border: '1px solid',
              borderColor: activeBuilding === tab.id ? '#58a6ff' : '#21262d',
              background: activeBuilding === tab.id ? '#1f6feb22' : 'transparent',
              color: activeBuilding === tab.id ? '#58a6ff' : '#8b949e',
              fontSize: 13,
              cursor: 'pointer',
              fontWeight: activeBuilding === tab.id ? 600 : 400,
              transition: 'all .15s',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Energy Flow (full width, no horizontal scroll) ── */}
      <div style={{
        background: '#161b22',
        border: '1px solid #21262d',
        borderRadius: 8,
        padding: '14px 16px',
        marginBottom: 20,
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 10 }}>
          에너지 흐름
          {activeBuilding !== 'all' && (
            <span style={{ fontSize: 11, color: '#8b949e', marginLeft: 6 }}>
              ({activeBuilding})
            </span>
          )}
        </div>
        <FlowDiagram building={activeBuilding} />
      </div>

      {/* ── Two-Column Layout ── */}
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexWrap: 'wrap' }}>

        {/* ── Left Column: Stats ── */}
        <div style={{
          width: 260,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}>

          {/* Role breakdown */}
          <div style={{
            background: '#161b22',
            border: '1px solid #21262d',
            borderRadius: 8,
            padding: '14px 16px',
          }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 12 }}>역할 분포</div>
            <RoleBreakdown meters={filteredMeters} />
          </div>

          {/* Legend */}
          <div style={{
            background: '#161b22',
            border: '1px solid #21262d',
            borderRadius: 8,
            padding: '14px 16px',
          }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 10 }}>범례</div>
            <RoleLegend />
          </div>
        </div>

        {/* ── Right Column: Table ── */}
        <div style={{ flex: 1, minWidth: 0 }}>

          {/* Search + Filter bar */}
          <div style={{
            display: 'flex',
            gap: 10,
            marginBottom: 12,
            flexWrap: 'wrap',
            alignItems: 'center',
          }}>
            <input
              type="text"
              placeholder="계량기 ID 또는 라벨 검색..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                flex: 1,
                minWidth: 180,
                padding: '7px 12px',
                background: '#161b22',
                border: '1px solid #21262d',
                borderRadius: 6,
                color: '#e6edf3',
                fontSize: 13,
                outline: 'none',
              }}
            />
            <select
              value={roleFilter}
              onChange={e => setRoleFilter(e.target.value)}
              style={{
                padding: '7px 10px',
                background: '#161b22',
                border: '1px solid #21262d',
                borderRadius: 6,
                color: '#e6edf3',
                fontSize: 13,
                cursor: 'pointer',
                outline: 'none',
              }}
            >
              <option value="all">모든 역할</option>
              <option value="production">생산</option>
              <option value="consumption">소비</option>
              <option value="distribution">배전</option>
              <option value="grid">계통</option>
            </select>
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              style={{
                padding: '7px 10px',
                background: '#161b22',
                border: '1px solid #21262d',
                borderRadius: 6,
                color: '#e6edf3',
                fontSize: 13,
                cursor: 'pointer',
                outline: 'none',
              }}
            >
              <option value="all">모든 유형</option>
              <option value="electricity">전기</option>
              <option value="heat">열</option>
              <option value="cooling">냉각</option>
              <option value="weather">기상</option>
            </select>
            <span style={{ fontSize: 12, color: '#8b949e', whiteSpace: 'nowrap' }}>
              {filteredMeters.length}개 표시
            </span>
          </div>

          {/* Table */}
          <div style={{
            background: '#161b22',
            border: '1px solid #21262d',
            borderRadius: 8,
            overflow: 'hidden',
          }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#0d1117', borderBottom: '1px solid #21262d' }}>
                    <th style={{ padding: '10px 10px', textAlign: 'left', color: '#8b949e', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap' }}>ID</th>
                    <th style={{ padding: '10px 6px', textAlign: 'center', color: '#8b949e', fontWeight: 600, fontSize: 12 }}>유형</th>
                    <th style={{ padding: '10px 6px', textAlign: 'center', color: '#8b949e', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap' }}>역할</th>
                    <th style={{ padding: '10px 10px', textAlign: 'left', color: '#8b949e', fontWeight: 600, fontSize: 12 }}>라벨</th>
                    <th style={{ padding: '10px 10px', textAlign: 'right', color: '#8b949e', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap' }}>평균 전력</th>
                    <th style={{ padding: '10px 10px', textAlign: 'left', color: '#8b949e', fontWeight: 600, fontSize: 12 }}>태그</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredMeters.length === 0 ? (
                    <tr>
                      <td colSpan={6} style={{ padding: '30px', textAlign: 'center', color: '#484f58', fontSize: 13 }}>
                        검색 결과가 없습니다.
                      </td>
                    </tr>
                  ) : (
                    filteredMeters.map(meter => (
                      <MeterRow key={meter.id} meter={meter} />
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
