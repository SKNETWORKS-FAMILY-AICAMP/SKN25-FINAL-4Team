import { useState, useMemo, useEffect } from 'react'
import { METER_CATALOG } from '../data/meterCatalog'

// ─── Constants ────────────────────────────────────────────────────────────────

const ROLE_COLOR = {
  production:   '#3fb950',
  consumption:  '#58a6ff',
  distribution: '#d29922',
  grid:         '#f85149',
}
const ROLE_ICON  = { production: '↑', consumption: '↓', distribution: '⇄', grid: '⟺' }
const ROLE_LABEL = { production: '생산', consumption: '소비', distribution: '배전', grid: '계통' }
const TYPE_ICON  = { electricity: '⚡', heat: '🔥', cooling: '❄', weather: '🌤' }

const BUILDING_TABS = [
  { id: 'all', label: '전체' },
  { id: 'H1',  label: 'H1 — Emission Lab' },
  { id: 'H2',  label: 'H2 — Workshop/Office' },
  { id: 'H3',  label: 'H3 — Design Studio' },
  { id: 'H4',  label: 'H4 — Office B4' },
  { id: 'V',   label: 'V — 부지 공통' },
]

// ─── Animation CSS ────────────────────────────────────────────────────────────

const ANIM_CSS = `
@keyframes dashDown { from { stroke-dashoffset: 12 } to { stroke-dashoffset: 0 } }
@keyframes dashUp   { from { stroke-dashoffset: 0 } to { stroke-dashoffset: 12 } }
`

function InjectStyles() {
  useEffect(() => {
    if (document.getElementById('_topo_anim')) return
    const s = document.createElement('style')
    s.id = '_topo_anim'
    s.textContent = ANIM_CSS
    document.head.appendChild(s)
  }, [])
  return null
}

// ─── EnergyNode ───────────────────────────────────────────────────────────────

function EnergyNode({ id, label, desc, type, role, avgKw, small = false, dimmed = false }) {
  const [hovered, setHovered] = useState(false)
  const roleColor = ROLE_COLOR[role] || '#8b949e'
  const icon = TYPE_ICON[type] || '●'
  const barPct = avgKw != null ? Math.min(Math.abs(avgKw) / 150 * 100, 100) : null
  const kwColor = avgKw == null ? '#8b949e' : avgKw < 0 ? '#3fb950' : roleColor

  return (
    <div
      style={{ position: 'relative', display: 'inline-flex', opacity: dimmed ? 0.4 : 1 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div style={{
        background: '#0d1117',
        border: `1.5px solid ${roleColor}`,
        borderRadius: 8,
        padding: small ? '5px 10px' : '8px 13px',
        minWidth: small ? 108 : 140,
        maxWidth: 230,
        cursor: 'default',
        boxShadow: hovered ? `0 0 14px ${roleColor}55` : 'none',
        transition: 'box-shadow .2s',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: small ? 11 : 13 }}>{icon}</span>
          <span style={{
            fontSize: 9, color: roleColor, background: roleColor + '22',
            borderRadius: 3, padding: '1px 5px', fontWeight: 700, letterSpacing: '.3px',
          }}>{ROLE_LABEL[role]}</span>
        </div>
        <div style={{ fontFamily: 'monospace', fontSize: small ? 9 : 10, color: '#58a6ff', marginBottom: 2 }}>{id}</div>
        <div style={{ fontSize: small ? 10 : 11, color: '#e6edf3', lineHeight: 1.35 }}>{label}</div>
        {barPct !== null && (
          <div style={{ marginTop: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
              <span style={{ fontSize: 9, color: '#484f58' }}>avg</span>
              <span style={{ fontSize: small ? 10 : 11, fontWeight: 700, color: kwColor }}>
                {avgKw > 0 ? '+' : ''}{avgKw} kW
              </span>
            </div>
            <div style={{ background: '#161b22', borderRadius: 3, height: 3 }}>
              <div style={{
                background: kwColor, borderRadius: 3, height: '100%',
                width: `${barPct}%`, transition: 'width .3s',
              }} />
            </div>
          </div>
        )}
      </div>
      {hovered && desc && (
        <div style={{
          position: 'absolute',
          bottom: 'calc(100% + 6px)', left: '50%', transform: 'translateX(-50%)',
          background: '#161b22', border: '1px solid #30363d',
          borderRadius: 6, padding: '8px 10px',
          fontSize: 11, color: '#c9d1d9',
          width: 250, zIndex: 999,
          boxShadow: '0 4px 16px rgba(0,0,0,.7)',
          lineHeight: 1.55, whiteSpace: 'normal', pointerEvents: 'none',
        }}>
          <div style={{ fontFamily: 'monospace', color: '#58a6ff', marginBottom: 4, fontSize: 11 }}>{id}</div>
          {desc}
        </div>
      )}
    </div>
  )
}

// ─── FlowArrow (animated SVG) ─────────────────────────────────────────────────

function FlowArrow({ kw, reverse = false, color, h = 36, label }) {
  const c = color ?? (kw != null && kw < 0 ? '#3fb950' : '#58a6ff')
  const anim = reverse ? 'dashUp' : 'dashDown'
  const cx = 14
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, userSelect: 'none' }}>
      {label && <div style={{ fontSize: 9, color: '#484f58' }}>{label}</div>}
      <svg width={28} height={h} viewBox={`0 0 28 ${h}`} style={{ overflow: 'visible' }}>
        <line x1={cx} y1={4} x2={cx} y2={h - 6} stroke="#21262d" strokeWidth={2} />
        <line x1={cx} y1={4} x2={cx} y2={h - 6}
          stroke={c} strokeWidth={2.5} strokeDasharray="6 4"
          style={{ animation: `${anim} .7s linear infinite` }}
        />
        {reverse
          ? <polygon points={`${cx},4 ${cx - 5},13 ${cx + 5},13`} fill={c} />
          : <polygon points={`${cx},${h - 2} ${cx - 5},${h - 11} ${cx + 5},${h - 11}`} fill={c} />
        }
      </svg>
      {kw != null && (
        <div style={{ fontSize: 10, fontWeight: 700, color: c }}>{Math.abs(kw)} kW</div>
      )}
    </div>
  )
}

// ─── NodeGroup ────────────────────────────────────────────────────────────────

function NodeGroup({ nodes, title, color, column = false }) {
  if (!nodes?.length) return null
  return (
    <div style={{
      background: (color ?? '#58a6ff') + '0d',
      border: `1px solid ${color ?? '#58a6ff'}33`,
      borderRadius: 8, padding: '8px 12px',
    }}>
      {title && (
        <div style={{
          fontSize: 9, fontWeight: 700,
          color: color ?? '#8b949e',
          letterSpacing: '.6px', textTransform: 'uppercase', marginBottom: 8,
        }}>{title}</div>
      )}
      <div style={{
        display: 'flex',
        flexWrap: column ? 'nowrap' : 'wrap',
        flexDirection: column ? 'column' : 'row',
        gap: 8,
      }}>
        {nodes.map(n => <EnergyNode key={n.id} {...n} small />)}
      </div>
    </div>
  )
}

// ─── RefBox — 다른 건물 급전원 표시 ──────────────────────────────────────────

function RefBox({ id, label, sub, color = ROLE_COLOR.grid }) {
  return (
    <div style={{
      background: color + '10', border: `1px solid ${color}44`,
      borderRadius: 8, padding: '6px 18px', textAlign: 'center',
    }}>
      {id && <div style={{ fontFamily: 'monospace', fontSize: 9, color: '#58a6ff', marginBottom: 2 }}>{id}</div>}
      <div style={{ fontSize: 11, fontWeight: 600, color }}>{label}</div>
      {sub && <div style={{ fontSize: 9, color: '#8b949e', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

// ─── EnergyBalance ────────────────────────────────────────────────────────────

const BALANCE_DATA = {
  H1: {
    items: [
      { label: '그리드 유입',   kw: +45.7,  color: ROLE_COLOR.grid,       suffix: 'kW' },
      { label: 'CHP 전기발전',  kw: -85.0,  color: ROLE_COLOR.production, suffix: 'kW' },
      { label: 'PV 발전',       kw: -11.0,  color: ROLE_COLOR.production, suffix: 'kW' },
      { label: 'CHP 폐열회수',  kw: +90.2,  color: '#e3a855',             suffix: '열kW' },
      { label: '열 총공급',     kw: +247.1, color: '#e3a855',             suffix: '열kW' },
    ],
    selfSuf: 67,
    note: '2022–2023 측정 평균 · 부호: + 유입/소비  − 생산',
  },
  H2: {
    items: [
      { label: 'PV 발전 (옥상)', kw: -33.9, color: ROLE_COLOR.production, suffix: 'kW' },
    ],
    note: '변압기 유입 합계 미계측 (Z35/Z36 → Z351/Z361, 2020.9 교체)',
  },
  H3: {
    items: [
      { label: 'PV 발전', kw: -20.6, color: ROLE_COLOR.production, suffix: 'kW' },
    ],
    note: 'H2.T.Z33 경유 급전 · 자체 변압기 없음',
  },
  V: {
    items: [
      { label: 'PV 주차장',     kw: -14.6, color: ROLE_COLOR.production, suffix: 'kW' },
      { label: '중앙 냉각 공급', kw: +54.0, color: '#79c0ff',             suffix: '열kW' },
    ],
    note: '부지 변압기 V.Z81+V.Z82 · V.K21 고장 구간은 H1.K11+K12+K14+K16 합산 재구성',
  },
}

function EnergyBalance({ building }) {
  const d = BALANCE_DATA[building]
  if (!d) return null
  return (
    <div style={{
      background: '#0d1117', border: '1px solid #21262d',
      borderRadius: 8, padding: '10px 14px',
      display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'center',
      marginBottom: 12,
    }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: '#8b949e', flexShrink: 0 }}>에너지 균형</div>
      {d.items.map(it => (
        <div key={it.label} style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <span style={{ fontSize: 9, color: '#484f58' }}>{it.label}</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: it.color }}>
            {it.kw > 0 ? '+' : ''}{it.kw} {it.suffix}
          </span>
        </div>
      ))}
      {d.selfSuf != null && (
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontSize: 9, color: '#484f58' }}>전기 자립률 (avg)</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: '#3fb950' }}>{d.selfSuf}%</div>
        </div>
      )}
      {d.note && (
        <div style={{ width: '100%', fontSize: 10, color: '#484f58', marginTop: -4 }}>
          * {d.note}
        </div>
      )}
    </div>
  )
}

// ─── SankeyBar — 6년 전기 공급원 비율 ────────────────────────────────────────

function SankeyBar() {
  const segs = [
    { label: '그리드',  pct: 62, color: ROLE_COLOR.grid,       sub: '9,262 MWh' },
    { label: 'CHP',     pct: 22, color: ROLE_COLOR.production, sub: '3,388 MWh' },
    { label: 'PV',      pct: 16, color: '#79c0ff',             sub: '2,467 MWh' },
  ]
  return (
    <div style={{ marginBottom: 16, width: '100%', maxWidth: 700 }}>
      <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 6 }}>
        전기 공급원 비율 — 6년 합계 소비 14,782 MWh (Gruner et al. 2025, Fig. 5)
      </div>
      <div style={{ display: 'flex', height: 22, borderRadius: 6, overflow: 'hidden', gap: 1 }}>
        {segs.map(s => (
          <div key={s.label} style={{ width: `${s.pct}%`, background: s.color, position: 'relative' }}>
            <span style={{
              position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
              justifyContent: 'center', fontSize: 10, fontWeight: 700, color: '#0d1117',
            }}>{s.pct}%</span>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 16, marginTop: 5, flexWrap: 'wrap', alignItems: 'center' }}>
        {segs.map(s => (
          <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: s.color }} />
            <span style={{ fontSize: 10, color: '#8b949e' }}>{s.label} {s.sub}</span>
          </div>
        ))}
        <span style={{ fontSize: 10, color: '#484f58', marginLeft: 'auto' }}>
          Grid 역송전 334 MWh (2%) — PV 잉여 역조류
        </span>
      </div>
    </div>
  )
}

// ─── Campus Overview (전체 뷰) ────────────────────────────────────────────────

function BuildingBox({ label, sub, meters, tags, tagColor = '#3fb950' }) {
  return (
    <div style={{
      background: '#161b22', border: '1px solid #21262d',
      borderRadius: 8, padding: '10px 14px', minWidth: 150,
    }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: '#e6edf3' }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: '#8b949e', marginTop: 1 }}>{sub}</div>}
      <div style={{ fontSize: 10, color: '#484f58', marginTop: 3 }}>계량기 {meters}개</div>
      {tags.length > 0 && (
        <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
          {tags.map(t => (
            <span key={t} style={{
              fontSize: 9, background: tagColor + '22', color: tagColor,
              borderRadius: 3, padding: '1px 5px',
            }}>{t}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function CampusOverview() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
      <SankeyBar />

      {/* 외부 전원 3종 */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
        <div style={{
          background: '#f8514918', border: '1.5px solid #f85149',
          borderRadius: 8, padding: '8px 16px', textAlign: 'center', minWidth: 160,
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#f85149' }}>⟺ 외부 계통 20kV</div>
          <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>Stadtwerke Offenbach am Main</div>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#f85149', marginTop: 3 }}>9,262 MWh / 6년</div>
        </div>
        <div style={{
          background: '#3fb95018', border: '1.5px solid #3fb950',
          borderRadius: 8, padding: '8px 16px', textAlign: 'center', minWidth: 160,
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#3fb950' }}>↑ 총 PV 설비</div>
          <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>749 kWp (6개 그룹)</div>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#3fb950', marginTop: 3 }}>avg −80 kW · 2,467 MWh/6년</div>
        </div>
        <div style={{
          background: '#3fb95018', border: '1.5px solid #3fb950',
          borderRadius: 8, padding: '8px 16px', textAlign: 'center', minWidth: 160,
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#3fb950' }}>↑ CHP (H1)</div>
          <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>Viessmann Vitobloc 199 kWel</div>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#3fb950', marginTop: 3 }}>avg −85 kW · 3,388 MWh/6년</div>
        </div>
      </div>

      <FlowArrow color={ROLE_COLOR.grid} h={28} />

      {/* V 부지 변압기 */}
      <div style={{
        background: '#d2992218', border: '1.5px solid #d29922',
        borderRadius: 8, padding: '10px 22px', textAlign: 'center', minWidth: 280,
      }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#d29922' }}>⇄ V — 부지 공통 변압기</div>
        <div style={{ fontSize: 10, color: '#8b949e', marginTop: 3 }}>
          V.Z81 + V.Z82 (20kV→230V 이중권선) · V.Z84/ZE84 PV 136kWp (주차장)
        </div>
        <div style={{ fontSize: 10, color: '#79c0ff', marginTop: 2 }}>
          ❄ 중앙 냉각 V.K21 — CM1+CM2+CM3 avg 54kW
        </div>
      </div>

      {/* H1 분기 + H2→H3/H4 분기 */}
      <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', justifyContent: 'center', alignItems: 'flex-start' }}>
        {/* H1 */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <FlowArrow kw={45.7} color={ROLE_COLOR.grid} h={28} />
          <BuildingBox label="H1 — Emission Lab" sub="차량 배기 테스트 시설" meters={29}
            tags={['CHP 85kW', 'PV 11kW', '열 247kW', '자립 67%']} />
        </div>

        {/* H2 + H3 + H4 */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <FlowArrow color={ROLE_COLOR.grid} h={28} />
          <BuildingBox label="H2 — Workshop / Office" sub="서버룸 · 사무동 · 워크샵" meters={26}
            tags={['PV 34kW (옥상)', 'ZE 교정미터 2023']} />
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
              <FlowArrow color={ROLE_COLOR.distribution} h={22} label="T.Z33" />
              <BuildingBox label="H3 — Design Studio" sub="디자인 스튜디오" meters={16}
                tags={['PV 21kW (~193kWp)']} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
              <FlowArrow color={ROLE_COLOR.distribution} h={22} label="T.Z30" />
              <BuildingBox label="H4 — Office B4" sub="사무동 B4" meters={4}
                tags={['ZE 교정미터 (2023.6~)']} tagColor="#8b949e" />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
              <FlowArrow color={ROLE_COLOR.distribution} h={22} label="T.Z34" />
              <div style={{
                background: '#161b22', border: '1px solid #21262d',
                borderRadius: 8, padding: '10px 14px', minWidth: 120,
              }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: '#e6edf3' }}>Workshop 부하</div>
                <div style={{ fontSize: 10, color: '#484f58', marginTop: 2 }}>서버·냉각·환기</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ fontSize: 10, color: '#484f58', marginTop: 4, textAlign: 'center' }}>
        * 토폴로지 출처: Fig. 1 — Gruner et al. Scientific Data 2025 (doi:10.1038/s41597-025-05186-3)
      </div>
    </div>
  )
}

// ─── FlowDiagram ─────────────────────────────────────────────────────────────

function FlowDiagram({ building }) {
  if (building === 'all') return <CampusOverview />

  const meters = METER_CATALOG.filter(m => m.building === building)
  const byRole = role => meters.filter(m => m.role === role)
  const byRoleType = (role, type) => meters.filter(m => m.role === role && m.type === type)
  const byTag = tag => meters.filter(m => m.tags.includes(tag))

  // ── H1: Emission Lab ──────────────────────────────────────────────────────
  if (building === 'H1') {
    const grid = byRole('grid')
    const elecDist = byRoleType('distribution', 'electricity')
    // ZE20이 현행(2023~), Z20은 구형(~2022) — 동일 선로 합산 금지
    const activeProd = meters.filter(m => m.role === 'production' && m.type === 'electricity' && m.id !== 'H1.Z20')
    const legacyCHP  = meters.filter(m => m.id === 'H1.Z20')
    const chpHeat    = byRoleType('production', 'heat')
    const heatDist   = byRoleType('distribution', 'heat')
    // 냉각기 전기 소비 (CM1·CM2·CM3) — Fig. 2a
    const coolElec   = byTag('냉각기')
    // 테스트·HVAC 전기 소비
    const testCons   = meters.filter(m =>
      m.role === 'consumption' && m.type === 'electricity' && !m.tags.includes('냉각기')
    )
    // 냉수 소비 (K 미터) — Fig. 2a 하단
    const coldCons   = byRoleType('consumption', 'cooling')

    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>

        {/* 1. 계통 급전 */}
        <NodeGroup nodes={grid} title="계통 급전 (V.Z81/V.Z82 → H1)" color={ROLE_COLOR.grid} />
        <FlowArrow kw={45.7} color={ROLE_COLOR.grid} />

        {/* 2. 전기 배전반 — Z17(61kW 대형부하) · Z29(CHP·PV 역방향 가능) */}
        <NodeGroup
          nodes={elecDist}
          title="전기 배전반 — Z17 (61kW 대형부하) · Z29 (역방향 허용)"
          color={ROLE_COLOR.distribution}
        />

        {/* 3. 세 병렬 컬럼 */}
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', justifyContent: 'center', alignItems: 'flex-start' }}>

          {/* 전기 생산 */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <FlowArrow kw={96} color={ROLE_COLOR.production} reverse />
            <NodeGroup nodes={activeProd} title="⚡ 전기 생산 (CHP · PV)" color={ROLE_COLOR.production} column />
            {legacyCHP.length > 0 && (
              <>
                <div style={{ fontSize: 9, color: '#484f58', marginTop: 2 }}>구형 미터 (~2022, 동일 선로)</div>
                {legacyCHP.map(m => <EnergyNode key={m.id} {...m} small dimmed />)}
              </>
            )}
          </div>

          {/* 전기 소비 */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <FlowArrow color={ROLE_COLOR.consumption} />
            <NodeGroup nodes={testCons} title="⚡ 테스트 챔버 / HVAC 전기 소비" color={ROLE_COLOR.consumption} />
          </div>

          {/* 열 + 냉각 (Fig. 2 구조) */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            {/* 열 생산 — CHP 폐열 → W12, 보일러+CHP → W11 */}
            <FlowArrow kw={90.2} color="#e3a855" label="CHP 폐열 회수" />
            <NodeGroup nodes={chpHeat} title="🔥 CHP 열 생산 (H1.W12)" color="#e3a855" />
            <FlowArrow kw={247.1} color="#e3a855" label="보일러 + CHP 총합" />
            <NodeGroup nodes={heatDist} title="🔥 열 총량 (H1.W11)" color="#e3a855" />

            <div style={{ borderTop: '1px dashed #21262d', width: '90%', margin: '4px 0' }} />

            {/* 냉각기 전기 소비 → V.K21 → 냉수 분배 (Fig. 2a) */}
            <NodeGroup nodes={coolElec} title="⚡ 냉각기 전기 소비 (CM1 · CM2 · CM3)" color={ROLE_COLOR.consumption} />
            <FlowArrow kw={54} color="#79c0ff" label="냉수 출력 → V.K21" />
            <div style={{
              background: '#79c0ff12', border: '1px solid #79c0ff44',
              borderRadius: 8, padding: '7px 14px', textAlign: 'center',
            }}>
              <div style={{ fontFamily: 'monospace', fontSize: 9, color: '#58a6ff' }}>V.K21</div>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#79c0ff' }}>❄ 중앙 냉각기 총합</div>
              <div style={{ fontSize: 9, color: '#484f58', marginTop: 2 }}>
                CM1+CM2+CM3 · avg 54kW · Tvl=6.5°C<br />
                고장 구간 → K11+K12+K14+K16 합산 재구성
              </div>
            </div>
            <FlowArrow color="#79c0ff" />
            <NodeGroup nodes={coldCons} title="❄ 냉수 공급 (HVAC · 서버룸 · 사무실)" color="#79c0ff" />
          </div>
        </div>
      </div>
    )
  }

  // ── H2: Workshop / Office ──────────────────────────────────────────────────
  if (building === 'H2') {
    // Z351/Z361 = 현행(2020.9~), Z35/Z36 = 구형(dimmed)
    const activeTx = meters.filter(m => m.role === 'grid' && !['H2.Z35', 'H2.Z36'].includes(m.id))
    const oldTx    = meters.filter(m => ['H2.Z35', 'H2.Z36'].includes(m.id))
    const prod = byRole('production')
    const dist = byRole('distribution')
    const serverCons  = byTag('서버룸')
    const serverIds   = new Set(serverCons.map(m => m.id))
    const coolVentCons = meters.filter(m =>
      m.role === 'consumption' && !serverIds.has(m.id) &&
      (m.tags.includes('냉각') || m.tags.includes('HVAC') || m.type === 'cooling')
    )
    const cvIds = new Set(coolVentCons.map(m => m.id))
    const otherCons = meters.filter(m =>
      m.role === 'consumption' && !serverIds.has(m.id) && !cvIds.has(m.id)
    )

    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>

        {/* 변압기 */}
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', justifyContent: 'center', alignItems: 'flex-end' }}>
          <NodeGroup nodes={activeTx} title="계통 급전 (2020.9~ 교체 Janitza 변압기)" color={ROLE_COLOR.grid} />
          {oldTx.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
              <div style={{ fontSize: 9, color: '#484f58' }}>구형 ABB-B24 (~2020.9)</div>
              <div style={{ display: 'flex', gap: 6 }}>
                {oldTx.map(m => <EnergyNode key={m.id} {...m} small dimmed />)}
              </div>
            </div>
          )}
        </div>
        <FlowArrow color={ROLE_COLOR.grid} />

        {/* 내부 배전 — T.Z30→H4 / T.Z32→로비 / T.Z33→H3 / T.Z34→Workshop */}
        <NodeGroup
          nodes={dist}
          title="내부 배전 — T.Z30→H4 · T.Z32→로비 · T.Z33→H3(Design Studio) · T.Z34→Workshop"
          color={ROLE_COLOR.distribution}
        />

        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', justifyContent: 'center', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <FlowArrow kw={33.9} color={ROLE_COLOR.production} reverse />
            <NodeGroup nodes={prod} title="⚡ PV 발전 (옥상 ~317kWp)" color={ROLE_COLOR.production} column />
          </div>
          {serverCons.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <FlowArrow color={ROLE_COLOR.consumption} />
              <NodeGroup nodes={serverCons} title="🖥 서버룸 (CIS · EU)" color={ROLE_COLOR.consumption} />
            </div>
          )}
          {coolVentCons.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <FlowArrow color={ROLE_COLOR.consumption} />
              <NodeGroup nodes={coolVentCons} title="❄🌬 로컬 냉각 / HVAC / 냉수" color={ROLE_COLOR.consumption} />
            </div>
          )}
          {otherCons.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <FlowArrow color={ROLE_COLOR.consumption} />
              <NodeGroup nodes={otherCons} title="기타 부하 (로보랩 등)" color={ROLE_COLOR.consumption} />
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── H3: Design Studio ─────────────────────────────────────────────────────
  if (building === 'H3') {
    const prod = byRole('production')
    const dist = byRole('distribution')
    const serverCons = byTag('서버룸')
    const simCons    = byTag('시뮬레이터')
    const serverIds  = new Set(serverCons.map(m => m.id))
    const simIds     = new Set(simCons.map(m => m.id))
    const otherCons  = meters.filter(m =>
      m.role === 'consumption' && !serverIds.has(m.id) && !simIds.has(m.id)
    )

    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
        <RefBox
          id="H2.T.Z33"
          label="H2 사무동 → H3 급전"
          sub="Feed design studio · H2 변압기(Z351/Z361) 경유"
        />
        <FlowArrow color={ROLE_COLOR.grid} />
        <NodeGroup
          nodes={dist}
          title="디자인 스튜디오 배전반 (ZE = 2023 교정미터 · 동일 선로 합산 금지)"
          color={ROLE_COLOR.distribution}
        />
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', justifyContent: 'center', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <FlowArrow kw={20.6} color={ROLE_COLOR.production} reverse />
            <NodeGroup nodes={prod} title="⚡ PV 발전 (~193kWp · 그룹 3&4 23.4% + 그룹 5&6)" color={ROLE_COLOR.production} column />
          </div>
          {serverCons.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <FlowArrow color={ROLE_COLOR.consumption} />
              <NodeGroup nodes={serverCons} title="🖥 서버 O4 (전원 · 냉각)" color={ROLE_COLOR.consumption} />
            </div>
          )}
          {simCons.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <FlowArrow color={ROLE_COLOR.consumption} />
              <NodeGroup nodes={simCons} title="🚗 드라이빙 시뮬레이터" color={ROLE_COLOR.consumption} />
            </div>
          )}
          {otherCons.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <FlowArrow color={ROLE_COLOR.consumption} />
              <NodeGroup nodes={otherCons} title="🌬❄ 환기 / 냉각" color={ROLE_COLOR.consumption} />
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── H4: Office B4 ─────────────────────────────────────────────────────────
  if (building === 'H4') {
    const dist = byRole('distribution')
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
        <RefBox
          id="H2.T.Z30"
          label="H2 사무동 B2 → H4 급전"
          sub="Office B2 total · H2 변압기 경유"
        />
        <FlowArrow color={ROLE_COLOR.grid} />
        <NodeGroup
          nodes={dist}
          title="Office B4 배전반 — ZE50/ZE51: 2023 교정미터 · Z50/Z51: 2023.6 이후 하드웨어 장애"
          color={ROLE_COLOR.distribution}
        />
        <div style={{
          background: '#f8514910', border: '1px solid #f8514933',
          borderRadius: 6, padding: '6px 14px',
          fontSize: 10, color: '#f85149', marginTop: 4,
        }}>
          ⚠ H4.Z50 / Z51 — 2023년 6월 이후 장애 · 현재 ZE50 / ZE51 교정미터로 대체
        </div>
      </div>
    )
  }

  // ── V: 부지 공통 ──────────────────────────────────────────────────────────
  if (building === 'V') {
    const grid       = byRole('grid')
    const activeProd = byRole('production').filter(m => !m.tags.includes('백업미터'))
    const backupProd = byRole('production').filter(m => m.tags.includes('백업미터'))
    const dist       = byRole('distribution')
    const weather    = meters.filter(m => m.type === 'weather')

    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>

        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', justifyContent: 'center', alignItems: 'flex-end' }}>
          <NodeGroup nodes={grid} title="외부 계통 (20kV 이중권선 변압기)" color={ROLE_COLOR.grid} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center' }}>
            <NodeGroup nodes={activeProd} title="⚡ PV 주차장 (2×68kWp = 136kWp · 2019.6~)" color={ROLE_COLOR.production} />
            {backupProd.length > 0 && (
              <div style={{ fontSize: 9, color: '#484f58', textAlign: 'center' }}>
                ZE84: 2023 교정미터 (동일 선로, 합산 금지)
              </div>
            )}
          </div>
          {weather.length > 0 && (
            <NodeGroup nodes={weather} title="🌤 기상관측소 (Lufft WS501-UMB)" color="#8b949e" />
          )}
        </div>

        <FlowArrow color={ROLE_COLOR.distribution} />

        <NodeGroup nodes={dist} title="❄ 중앙 냉각 배전 (V.K21 — CM1+CM2+CM3 합계 출력)" color={ROLE_COLOR.distribution} />

        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', justifyContent: 'center' }}>
          {[
            { id: 'H1.Z15 + Z28', label: 'H1 급전' },
            { id: 'H2.Z351 + Z361', label: 'H2 급전' },
          ].map(b => (
            <div key={b.id} style={{ textAlign: 'center' }}>
              <FlowArrow color={ROLE_COLOR.grid} h={24} />
              <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>
                <span style={{ fontFamily: 'monospace', color: '#58a6ff', fontSize: 9 }}>{b.id}</span>
                <br />{b.label}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return null
}

// ─── SummaryCard ──────────────────────────────────────────────────────────────

function SummaryCard({ value, label, sub, color = '#58a6ff' }) {
  return (
    <div style={{
      background: '#161b22', border: '1px solid #21262d',
      borderRadius: 8, padding: '14px 18px', minWidth: 130, flex: 1,
    }}>
      <div style={{ fontSize: 28, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 13, color: '#e6edf3', marginTop: 4 }}>{label}</div>
      {sub && <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

// ─── RoleLegend ──────────────────────────────────────────────────────────────

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
      <div style={{ borderTop: '1px solid #21262d', paddingTop: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
        <div style={{ fontSize: 10, color: '#484f58', lineHeight: 1.65 }}>
          <span style={{ color: '#58a6ff' }}>ZE</span> 미터: 2023년 독일 교정법<br />
          대응 이중 설치 — 동일 선로,<br />
          합산 시 이중 계산 주의
        </div>
        <div style={{ fontSize: 10, color: '#484f58' }}>
          <span style={{ opacity: 0.4 }}>■</span> 흐릿한 노드: 구형/비활성 미터
        </div>
      </div>
    </div>
  )
}

// ─── RoleBreakdown ────────────────────────────────────────────────────────────

function RoleBreakdown({ meters }) {
  const counts = useMemo(() => {
    const c = { production: 0, consumption: 0, distribution: 0, grid: 0 }
    meters.forEach(m => { if (c[m.role] !== undefined) c[m.role]++ })
    return c
  }, [meters])
  const total = meters.length
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
      {Object.entries(counts).map(([role, count]) => {
        const pct = total ? Math.round((count / total) * 100) : 0
        return (
          <div key={role}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
              <span style={{ fontSize: 12, color: '#e6edf3' }}>{ROLE_ICON[role]} {ROLE_LABEL[role]}</span>
              <span style={{ fontSize: 12, color: '#8b949e' }}>{count}개 ({pct}%)</span>
            </div>
            <div style={{ background: '#21262d', borderRadius: 4, height: 7, width: 180 }}>
              <div style={{
                background: ROLE_COLOR[role], borderRadius: 4, height: '100%',
                width: `${pct}%`, transition: 'width .3s',
              }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ─── MeterRow ─────────────────────────────────────────────────────────────────

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
          fontSize: 12, fontWeight: 600, color: roleColor,
          background: roleColor + '22', borderRadius: 4, padding: '2px 7px', whiteSpace: 'nowrap',
        }}>
          {ROLE_ICON[meter.role]} {ROLE_LABEL[meter.role]}
        </span>
      </td>
      <td style={{ padding: '8px 10px', fontSize: 12, color: '#e6edf3', maxWidth: 200 }}>
        <div>{meter.label}</div>
        {meter.desc && <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>{meter.desc}</div>}
      </td>
      <td style={{ padding: '8px 10px', fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap' }}>
        {meter.avgKw != null ? (
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
              fontSize: 10, background: '#21262d', color: '#8b949e',
              borderRadius: 3, padding: '1px 5px',
            }}>{tag}</span>
          ))}
        </div>
      </td>
    </tr>
  )
}

// ─── PaperFigures ─────────────────────────────────────────────────────────────

function PaperFigures() {
  const [open, setOpen] = useState(false)

  return (
    <div style={{ marginTop: 28, borderTop: '1px solid #21262d', paddingTop: 20 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'none', border: 'none', cursor: 'pointer',
          color: '#8b949e', fontSize: 13, padding: 0, marginBottom: open ? 16 : 0,
        }}
      >
        <span style={{ fontSize: 16, lineHeight: 1 }}>{open ? '▾' : '▸'}</span>
        <span style={{ fontWeight: 600, color: '#e6edf3' }}>논문 원본 다이어그램</span>
        <span style={{ fontSize: 11, color: '#484f58' }}>Gruner et al. Scientific Data 2025</span>
      </button>

      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

          {/* Fig. 1 */}
          <div style={{ background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '16px 20px' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#58a6ff', marginBottom: 4 }}>Fig. 1</div>
            <div style={{ fontSize: 12, color: '#8b949e', marginBottom: 12, lineHeight: 1.5 }}>
              전기 계량기 계층 구조 — H1(Emission Lab) · H2(Workshop/Office) · H3(Design Studio) · H4(Office B4) · V(부지 공통)
            </div>
            <img
              src="/fig1_meter_hierarchy.png"
              alt="Fig.1 Electricity metering hierarchy"
              style={{ width: '100%', borderRadius: 6, border: '1px solid #21262d', display: 'block' }}
            />
          </div>

          {/* Fig. 2 */}
          <div style={{ background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '16px 20px' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#58a6ff', marginBottom: 4 }}>Fig. 2</div>
            <div style={{ fontSize: 12, color: '#8b949e', marginBottom: 12, lineHeight: 1.5 }}>
              냉방·난방 시스템 미터 구성 — 냉각기(CM1/2/3) · V.K21 냉각 통합 · CHP(H1.ZE20) · 보일러(H1.W12)
            </div>
            <img
              src="/fig2_hvac_meters.png"
              alt="Fig.2 Central cooling and heating system meters"
              style={{ width: '100%', borderRadius: 6, border: '1px solid #21262d', display: 'block' }}
            />
          </div>

        </div>
      )}
    </div>
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
    if (activeBuilding !== 'all') list = list.filter(m => m.building === activeBuilding)
    if (roleFilter !== 'all') list = list.filter(m => m.role === roleFilter)
    if (typeFilter !== 'all') list = list.filter(m => m.type === typeFilter)
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

  const totalElec     = METER_CATALOG.filter(m => m.type === 'electricity').length
  const totalHeatCool = METER_CATALOG.filter(m => m.type === 'heat' || m.type === 'cooling').length
  const totalProduce  = METER_CATALOG.filter(m => m.role === 'production').length

  return (
    <div style={{ background: '#0d1117', minHeight: '100%', padding: 20, overflow: 'auto', color: '#e6edf3' }}>
      <InjectStyles />

      {/* ── Header ── */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: '#e6edf3' }}>계량기 토폴로지</div>
        <div style={{ fontSize: 13, color: '#8b949e', marginTop: 4 }}>
          81개 계량기 · 4개 건물 + 부지 · Honda R&D Europe (Offenbach am Main)
          <span style={{ marginLeft: 8, fontSize: 11, color: '#484f58' }}>
            출처: Gruner et al. Scientific Data 2025
          </span>
        </div>
      </div>

      {/* ── Summary Cards (전체 탭) ── */}
      {activeBuilding === 'all' && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
          <SummaryCard value={81}            label="총 계량기 수"       sub="전체 건물 합계"   color="#e6edf3" />
          <SummaryCard value={totalElec}     label="전기 미터 (Z/ZE)"   sub="electricity"    color="#58a6ff" />
          <SummaryCard value={totalHeatCool} label="열/냉각 미터 (K/W)" sub="heat + cooling" color="#d29922" />
          <SummaryCard value={totalProduce}  label="생산 미터"           sub="PV + CHP"       color="#3fb950" />
        </div>
      )}

      {/* ── Building Tabs ── */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}>
        {BUILDING_TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => { setActiveBuilding(tab.id); setSearch(''); setRoleFilter('all'); setTypeFilter('all') }}
            style={{
              padding: '6px 14px', borderRadius: 6, border: '1px solid',
              borderColor: activeBuilding === tab.id ? '#58a6ff' : '#21262d',
              background:  activeBuilding === tab.id ? '#1f6feb22' : 'transparent',
              color:       activeBuilding === tab.id ? '#58a6ff' : '#8b949e',
              fontSize: 13, cursor: 'pointer',
              fontWeight: activeBuilding === tab.id ? 600 : 400,
              transition: 'all .15s',
            }}
          >{tab.label}</button>
        ))}
      </div>

      {/* ── Energy Balance (건물별) ── */}
      <EnergyBalance building={activeBuilding} />

      {/* ── Energy Flow Diagram ── */}
      <div style={{
        background: '#161b22', border: '1px solid #21262d',
        borderRadius: 8, padding: '14px 16px', marginBottom: 20,
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 12 }}>
          에너지 흐름
          {activeBuilding !== 'all' && (
            <span style={{ fontSize: 11, color: '#8b949e', marginLeft: 6 }}>({activeBuilding})</span>
          )}
        </div>
        <FlowDiagram building={activeBuilding} />
      </div>

      {/* ── Two-Column: Stats + Table ── */}
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', flexWrap: 'wrap' }}>

        {/* Left */}
        <div style={{ width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 12 }}>역할 분포</div>
            <RoleBreakdown meters={filteredMeters} />
          </div>
          <div style={{ background: '#161b22', border: '1px solid #21262d', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', marginBottom: 10 }}>범례</div>
            <RoleLegend />
          </div>
        </div>

        {/* Right: Table */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              placeholder="계량기 ID 또는 라벨 검색..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                flex: 1, minWidth: 180, padding: '7px 12px',
                background: '#161b22', border: '1px solid #21262d',
                borderRadius: 6, color: '#e6edf3', fontSize: 13, outline: 'none',
              }}
            />
            <select value={roleFilter} onChange={e => setRoleFilter(e.target.value)}
              style={{ padding: '7px 10px', background: '#161b22', border: '1px solid #21262d', borderRadius: 6, color: '#e6edf3', fontSize: 13, cursor: 'pointer', outline: 'none' }}>
              <option value="all">모든 역할</option>
              <option value="production">생산</option>
              <option value="consumption">소비</option>
              <option value="distribution">배전</option>
              <option value="grid">계통</option>
            </select>
            <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
              style={{ padding: '7px 10px', background: '#161b22', border: '1px solid #21262d', borderRadius: 6, color: '#e6edf3', fontSize: 13, cursor: 'pointer', outline: 'none' }}>
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

          <div style={{ background: '#161b22', border: '1px solid #21262d', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#0d1117', borderBottom: '1px solid #21262d' }}>
                    <th style={{ padding: '10px 10px', textAlign: 'left',   color: '#8b949e', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap' }}>ID</th>
                    <th style={{ padding: '10px 6px',  textAlign: 'center', color: '#8b949e', fontWeight: 600, fontSize: 12 }}>유형</th>
                    <th style={{ padding: '10px 6px',  textAlign: 'center', color: '#8b949e', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap' }}>역할</th>
                    <th style={{ padding: '10px 10px', textAlign: 'left',   color: '#8b949e', fontWeight: 600, fontSize: 12 }}>라벨</th>
                    <th style={{ padding: '10px 10px', textAlign: 'right',  color: '#8b949e', fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap' }}>평균 전력</th>
                    <th style={{ padding: '10px 10px', textAlign: 'left',   color: '#8b949e', fontWeight: 600, fontSize: 12 }}>태그</th>
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
                    filteredMeters.map(meter => <MeterRow key={meter.id} meter={meter} />)
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* ── 논문 원본 다이어그램 ── */}
      <PaperFigures />
    </div>
  )
}
