# Frontend — React 대시보드

EMS Agent 프론트엔드. React(Vite) + Recharts 기반 설비 상태감시·AI 코파일럿 대시보드.

---

## 로컬 개발 실행

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
```

백엔드는 `http://localhost:8000` 에서 실행 중이어야 합니다.

### 프로덕션 빌드

```bash
npm run build     # dist/ 생성
npm run preview   # 빌드 결과 로컬 확인
```

Docker Compose 실행 시 nginx가 `dist/`를 `8080` 포트로 서빙합니다.

---

## 소스 구조

```
src/
├── api/
│   └── client.js              # axios 기반 백엔드 API 클라이언트 (baseURL: /api)
├── components/
│   ├── common/
│   │   ├── EquipmentIcon.jsx  # 설비 유형별 아이콘
│   │   └── SimulatorClock.jsx # 시뮬레이터 가상 시계 표시
│   └── panels/                # 화면 단위 패널 (사이드바 메뉴별 1:1 매핑)
│       ├── DashboardPanel.jsx     # 메인 대시보드 (KPI 요약)
│       ├── EquipmentPanel.jsx     # 설비 상태감시 (헬스 스코어·카드)
│       ├── AnomalyPanel.jsx       # 이상탐지 결과 목록·차트
│       ├── AnomalyChartPanel.jsx  # 이상 상세 시계열 차트
│       ├── ChatPanel.jsx          # AI 코파일럿 채팅 인터페이스
│       ├── ChatWorkspacePanel.jsx # 채팅 + 작업지시 통합 뷰
│       ├── ChatHistoryPanel.jsx   # 대화 이력 관리
│       ├── MaintenancePanel.jsx   # 정비 작업지시 칸반
│       ├── ForecastPanel.jsx      # 수요 예측 차트
│       ├── ControlPanel.jsx       # 운영 권고 승인/거부
│       ├── ReportPanel.jsx        # 월간 보고서·에너지 분석
│       ├── DailyReportPanel.jsx   # 일일 브리핑
│       ├── BillingPanel.jsx       # 요금·목표 관리
│       ├── TopologyPanel.jsx      # 계량기 토폴로지 시각화
│       ├── SettingsPanel.jsx      # LLM·알림·스케줄 설정
│       └── UsersPanel.jsx         # 사용자 관리
├── data/                          # 정적 카탈로그 (설비 유형·계량기 목록 등)
├── App.jsx                        # 라우팅 · 사이드바 레이아웃
├── theme.js                       # 색상·다크모드 테마
└── index.css
```

---

## API 클라이언트

`src/api/client.js`에서 axios 인스턴스를 export합니다.

```js
import api from '../api/client'

// 예시
const { data } = await api.get('/cms/equipment')
await api.post('/chat', { message: '...' })
```

Docker Compose 환경에서는 nginx가 `/api/*` 요청을 백엔드 `8000`으로 프록시합니다.
로컬 개발 시 `vite.config.js`의 proxy 설정이 동일한 역할을 합니다.

---

## 주요 의존성

| 라이브러리 | 용도 |
|---|---|
| `recharts` | 시계열·바·파이 차트 |
| `lucide-react` | 아이콘 |
| `react-markdown` + `rehype-katex` | AI 응답 마크다운·수식 렌더링 |
| `axios` | API 클라이언트 |

---

## 실시간 기능

- **SSE 스트리밍**: `/api/chat/stream` — AI 응답을 토큰 단위로 수신
- **알림 SSE**: `/api/notifications/stream` — 이상 탐지 토스트 알림 수신
- **시뮬레이터**: 가상 시계(SimulatorClock)가 백엔드 `/simulator` API로 과거 데이터 재생 제어
