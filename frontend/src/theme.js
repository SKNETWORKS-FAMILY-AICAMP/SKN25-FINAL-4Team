// 라이트 엔터프라이즈 디자인 토큰 (틸 브랜드)
// 점진적 적용: 패널을 이 토큰으로 옮기며 상용 룩으로 전환
export const T = {
  // 표면
  bg:           '#f4f6fa',   // 앱 배경
  surface:      '#ffffff',   // 카드/패널
  surface2:     '#eef1f6',   // 옅은 보조 표면
  surfaceHover: '#f7f9fc',
  border:       '#e2e7ef',
  borderStrong: '#cdd5e0',

  // 텍스트
  text:      '#1b2433',
  textMuted: '#5a6675',
  textFaint: '#909aa8',

  // 브랜드 (틸)
  brand:       '#0d9488',
  brandStrong: '#0f766e',
  brandSoft:   '#d3f3ee',

  // 보조/상태
  accent:  '#2563eb',
  success: '#16a34a',
  warning: '#d97706',
  danger:  '#dc2626',
  info:    '#2563eb',
  successSoft: '#dcfce7',
  warningSoft: '#fef3c7',
  dangerSoft:  '#fee2e2',
  infoSoft:    '#dbeafe',

  // 형태
  radius:   10,
  radiusSm: 6,
  shadow:   '0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.10)',
  shadowMd: '0 4px 14px rgba(16,24,40,.10)',
  font: "'Inter','Pretendard','Segoe UI',system-ui,sans-serif",
}

// recharts 라이트 토큰
export const CHART = {
  grid: '#e7ebf1',
  tick: '#5a6675',
  tooltipBg: '#ffffff',
  tooltipBorder: '#e2e7ef',
}

// 상태 → 색 매핑 헬퍼
export const STATUS_COLOR = {
  정상: T.success, 주의: T.warning, 경고: T.danger,
  HIGH: T.danger, MEDIUM: T.warning, LOW: T.info,
}
