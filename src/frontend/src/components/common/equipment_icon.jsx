// 설비 타입 → 의미색 라인 아이콘
import { Zap, Snowflake, Flame, Sun, Box } from 'lucide-react'

const MAP = {
  grid:    { I: Zap,       color: '#d97706' },  // 계통/수전 — 앰버
  cooling: { I: Snowflake, color: '#0ea5e9' },  // 냉방 — 시안
  chp:     { I: Flame,     color: '#ea580c' },  // 열병합 — 오렌지
  pv:      { I: Sun,       color: '#16a34a' },  // 태양광 — 그린
}

export function EquipmentIcon({ id, size = 20, color, strokeWidth = 2 }) {
  const m = MAP[id] || { I: Box, color: '#5a6675' }
  const Ic = m.I
  return <Ic size={size} color={color || m.color} strokeWidth={strokeWidth} />
}
