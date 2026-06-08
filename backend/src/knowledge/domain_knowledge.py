"""
Honda R&D 에너지 도메인 지식 상수.
온톨로지를 대체하여 시스템 프롬프트에 직접 주입.
수정이 필요하면 이 파일만 편집하면 즉시 반영됨.
"""

import json
from pathlib import Path

_BASE_DIR = Path(__file__).parent
_METER_PATH = _BASE_DIR / "meter_metadata.json"

# ── 핵심 도메인 지식 (시스템 프롬프트 주입용) ──────────────────────

DOMAIN_KNOWLEDGE_PROMPT = """## 에너지 도메인 지식

### 시설 개요
- 시설: Honda R&D Europe GmbH, 독일 오펜바흐 (Offenbach, Germany)
- 전력망: 독일 공공 전력망 (한국 한전과 무관)
- 전력 용어: "계통 전력" 또는 "외부 계통 전력" 사용 (한전·수전량·한국 전력 용어 사용 금지)
- 데이터 기간: 2017~2024년
- 계측기: 81개 미터 (전기·열량·냉방·기상)

### 에너지 시스템 구조
- 에너지 공급원 3종:
  · 외부 계통 전력 (GridConnection): 독일 공공 전력망에서 인입 (변수명: grid_P)
  · 태양광 PV (PVSystem): H1·H2·H3·V 지붕 설치, 총 749kWp (변수명: pv_P)
  · 열병합 CHP (CHPSystem): 전기(변수명: chp_P)와 열(변수명: chp_heat_P) 동시 생산
- 에너지 시스템 3종:
  · 전기 시스템 (ElectricalSystem): grid_P / pv_P / chp_P (단위: W)
  · 난방 시스템 (HeatingSystem): chp_heat_P (CHP 폐열) / heat_total_P (총 열공급, 단위: W)
  · 냉방 시스템 (CoolingSystem): cool_elec_P (전기 투입) / cool_output_P (냉수 출력, 단위: W)

### 건물 구역 (6개)
- H1 (배기가스 시험실): 차량 배기가스 시험, HVAC 테스트. 전기 29개·냉방 4개·열량 2개 미터. PV(H1.Z310). CHP(H1.ZE20). 에너지 소비 최대 구역.
- H2 (워크숍·서버실): 자동차 부품 워크숍, CIS/EU 서버실, Robolab. PV(H2.Z311). H2.T.Z* 미터는 ABB-B24 전기 미터. 열량 미터 없음.
- H3 (디자인 스튜디오): 자동차 디자인, 주행 시뮬레이터. PV(H3.Z312). 전기 미터만. 열량·냉수 미터 없음.
- H4 (사무동 B4): 사무 공간. 전기 4개 미터만. 소규모 구역.
- V (부지 공통): 외부 계통 변압기(V.Z81·V.Z82), PV 주차장(V.Z84). 시설 전체 그리드 인입점.
- WeatherStation (기상 관측소): Lufft WS501-UMB. 일사량(Igm, W/m²)·외기온(Ta, °C) 측정.

### KPI 공식
- 자급률 (Self-Sufficiency) = (PV + CHP) / (Grid + PV + CHP). 6년평균 39.6% (2022년 46.9%). 높을수록 그리드 의존도 낮음.
- 성능계수 COP = cooling_output / cooling_elec. 중앙값 2.06. cool_elec=0일 때 0나누기 방어 필수.
- 그리드 의존도 = Grid / (Grid + PV + CHP). 자급률의 반대 개념.

### 이상 유형 (5종)
- PowerSpike (전력 급등): electricity_P.total 급증. 그리드 과부하 위험.
- COPDrop (COP 급락): 냉방 효율 저하. 냉각기 과부하 또는 냉매 부족 의심.
- NightConsumption (야간 이상 소비): 운영 시간 외 전력 소비. 설비 미차단 또는 침입 의심.
- CHPOutage (CHP 정지): CHP 전기·열 동시 0. 자급률 급감, 그리드 의존도 증가.
- PVNightNonZero (PV 야간 비정상): 야간 PV > 0은 센서 오류. 정상 야간 NaN과 구별 필요.

### 날씨 영향
- 일사량 (Igm, W/m²): PV 발전량에 직접 영향.
- 외기온 (Ta, °C): 냉방 부하 및 COP에 영향, 난방 부하에도 영향.

### 계절별 정상 운전 범위 (오진단 방지)
- 냉방 COP:
  · 연간 중앙값 2.06은 전체 평균. 여름(외기온 30°C+)에는 1.7~2.0도 정상.
  · COP 하락 판단 시 반드시 외기온 함께 고려. 외기온 높을수록 COP 낮음은 물리적 정상.
- CHP 전기 효율:
  · 겨울철 열 수요 증가 시 CHP는 열 생산을 우선 최적화 → 전기 효율 소폭 감소(±3~5%p)는 정상.
  · 열 회수율이 높은 겨울 = 총합 에너지 효율은 오히려 향상. 전기 효율만 보고 이상 판정 금지.
  · 여름: 열 수요 최소 → 전기 효율 최대. 겨울과 반대 패턴.
- PV 발전량:
  · 겨울·흐린 날: 일사량 감소로 발전량 급감 정상. 자급률 하락 = 계절 영향.

### 설비별 역률 정상 범위
- 소비 설비 (계통·냉방): 역률 0.85 이상이 정상. 0.85 미만 = 무효전력 과다.
- 발전 설비 (PV·CHP): 역률 음수 또는 낮음 = 전력 역송(발전) 중 = 정상. 이상으로 판정 금지.

### 에너지 단위 구분
- kW  = 순간 전력 (Power). 설비가 지금 사용/생산하는 전력.
- kWh = 에너지 소비량 (Power × Time). 일정 기간 누적된 에너지.
- 순간 측정값·전류·전압 → kW 또는 A/V. 월간·일간 소비량 → kWh.
- kW와 kWh를 혼용하면 단위 오류. 발견 시 반드시 지적.
"""

# ── FEMS 업계 표준 용어 (공통 어휘) ────────────────────────────────

KFEMS_STANDARD_TERMS = """## FEMS/CMS 업계 표준 수식 및 용어

### 주요 개념
- EIS (Energy Information System): 데이터 수집·분석·가시화
- EOS (Energy Optimization System): 최적화·운전 권고
- M&V (Measurement & Verification): 절감량 정량 검증 (IPMVP 기준)

### K-FEMS 7대 핵심 수식 (답변에 적극 인용)
- η (유틸리티 효율)    = E_meas / E_BL
    · 측정 에너지 / 베이스라인 에너지. 1.0이 기준, 1.0 초과=효율 개선, 미만=악화.
    · 예: "이번 달 η = 0.94 — 베이스라인 대비 6% 에너지 과소비"

- EI (공정 원단위)     = E_BL / P
    · 생산량(P) 단위당 소비 에너지. 낮을수록 효율 좋음. 단위: kWh/생산단위.
    · Honda 데이터에서 P(생산량) 없음 → 외기온 보정 원단위(EI_T = 전력 / HDD·CDD) 로 대체.

- E_BL (Energy Baseline) — 회귀·LSTM 등으로 추정한 기준 에너지 소비량
    · 잔차 = E_meas − E_BL → 이상탐지의 핵심 신호 (이 프로젝트 이상탐지 원리)

- E_BL = Σ E_i (에너지 밸런스 검증)
    · 메인 미터(V.Z81+V.Z82) = 부문 합산(Σ 서브미터). 오차 5% 이내 = 정상.
    · 불일치 > 5%이면 미터 고장, 계통 누설, 측정 오류 의심.

- Φ_act = Φ_fit(E_BL) (설비 이상진단)
    · 실측 특성값(Φ_act) vs 회귀 적합 함수(Φ_fit). 차이 크면 설비 이상 신호.
    · 이 프로젝트에서 COP, chp_P 등이 Φ_act에 해당.

- E = min ∫E_BL dt (유틸리티 최적화 운전)
    · 일정 기간 누적 에너지 최소화. 피크 시프트·부하 분산 권고의 수학적 근거.

- P_opt = ∂E_BL/∂P = 0 (공정 최적화 운전)
    · 한계 에너지가 0인 최적 생산 운전점. 생산량 데이터 필요 시 적용.

### 에너지 밸런스 해석 기준 (Honda 시설 기준)
- 메인 미터: V.Z81 (Haupteinspeisung 1) + V.Z82 (Haupteinspeisung 2) — 시설 전체 인입
- 부문 합산: H1 + H2 + H3 + H4 구역 서브미터 + PV + CHP 자가발전
- 허용 오차: ±5% 이내 정상. 초과 시 데이터 품질 경고.
- 게이트웨이 장애 구간(2020-02~03, 2020-08~09, 2021-11~12, 2022-05~07)은 보정 데이터 — 밸런스 계산 시 명시.

### 외기온 정규화 원단위 (EI_T, Honda 적용)
- HDD (Heating Degree Days) = max(18 − Ta, 0) — 난방 필요도. Ta < 18°C일 때 누적.
- CDD (Cooling Degree Days) = max(Ta − 18, 0) — 냉방 필요도. Ta > 18°C일 때 누적.
- EI_T = 월간 전력 소비(kWh) / (HDD + CDD) — 기후 보정 후 실질 에너지 효율.
- EI_T가 낮을수록 기후 조건 대비 에너지 효율 좋음. 전년 동월 비교 시 날씨 효과 제거 가능.
"""


# ── 예측-행동 변환 프롬프트 (Forecast-to-Action) ───────────────────

FORECAST_RECOMMENDATION_PROMPT = """## 답변 구조 (필수)

다음 3개 섹션을 순서대로 모두 포함하세요. 에너지 최적화 운전 관점에서 작성합니다.

### 📊 요약
- 평균/피크/최저 소비 수치를 한 문장으로 압축.
- 모델명과 예측 신뢰 수준을 명시.

### ⚠️ 주목할 시간대
- 피크 시각과 그 직전 램프(상승) 시간대를 명시.
- 평균 대비 피크 비율(피크/평균)을 계산하여 인용.
- 평소와 다른 패턴(이른 새벽 상승, 야간 비정상 등)이 있으면 지적.

### ✅ 오늘/다음 할 일 (운영 권고)
운영자가 바로 행동할 수 있도록 **구체적 액션 3개 이내**로 제시.
가능한 권고 카테고리:
- 부하 시프트: 비핵심 부하(공조 프리쿨, ESS 충전, 배치 공정)를 피크 시간대 밖으로 이동
- ESS/CHP 운전 권고: PV·CHP 가용성과 예측 피크를 연동
- 점검 권고: 예측 오차가 큰 구간 → 미터 데이터 품질·설비 상태 확인
- 베이스라인 갱신: 패턴 급변 시 E_BL 재학습 검토

각 권고는 "언제·무엇을·왜"가 한 문장 안에 들어가도록.
예: "14~16시 피크 1.4 MW 예상 → H1 공조 12~14시 프리쿨 권장 → 피크 부하 약 80 kW 분산."
"""


# ── 이상 관련 도메인 지식 (anomaly agent 전용) ────────────────────

ANOMALY_DOMAIN_PROMPT = """## 이상탐지 관련 도메인 지식

### KPI 해석 기준
- COP 중앙값 2.06. 1.5 이하이면 심각한 효율 저하.
  ⚠️ COP는 외기온 영향을 받음: 여름(30°C+)에는 1.7~2.0도 정상. 계절 고려 필수.
- 자급률 6년평균 39.6%. 30% 이하이면 그리드 과의존.
  ⚠️ 겨울·흐린 날은 PV 발전량 감소로 자급률 하락이 자연스러움.
- cool_elec=0일 때 COP 계산 불가 (0나누기 방어 필수).
- 에너지 공급원 관계: CHP→전기+열 생산, PV→전기 생산, Grid→전기 공급, 냉방→전기 소비.
- PV 야간 NaN은 정상 (센서 꺼짐). 야간 PV > 0만 이상.
- CHP 겨울 전기 효율 소폭 감소: 열 수요 증가 시 열 생산 우선 최적화 → 전기 효율 ±3~5%p 감소 정상.
- 역률 음수/저값: 발전 설비(PV·CHP)는 정상(역송). 소비 설비(계통·냉방)에서만 이상.

### 이상 유형별 점검 권고
- PowerSpike:
    원인: 변압기 부하 급증, 대형 설비 동시 기동, 역률 불량
    조치: ① 그리드 실측값(grid_P) 및 피더별 전류 확인 → ② 동시 기동 설비 스케줄 분산 → ③ 역률 개선 콘덴서 점검
- COPDrop:
    원인: 냉각수 온도 상승, 냉매 부족, 냉각탑 팬 이상, 응축기 오염
    조치: ① 응축기 청소 및 냉매 압력 확인 → ② 냉각탑 팬·충진재 점검 → ③ cool_output_P / cool_elec_P 추세 비교
- NightConsumption:
    원인: 설비 미차단(HVAC, 서버 외 조명/전열), 보안 문제
    조치: ① 야간 분기별 전력 로그 확인 → ② 불필요 설비 자동차단 타이머 점검 → ③ H2 서버실 정상 부하인지 확인
- CHPOutage:
    원인: CHP 연료(가스) 공급 차단, 정비 셧다운, 과열 트립
    조치: ① CHP 전기(chp_P) + 열(chp_heat_P) 동시 0 확인 → ② 가스 공급 상태 점검 → ③ 그리드 의존도 급등 여부 모니터링
- PVNightNonZero:
    원인: 센서 오류 (인버터 오프셋, 배선 누전)
    조치: ① 야간 PV 인버터 출력 로그 확인 → ② 해당 인버터 재시작 또는 센서 교정 → ③ 정상 야간은 NaN이 맞음
"""

# ── 이상 답변 구조 프롬프트 ────────────────────────────────────────

ANOMALY_RECOMMENDATION_PROMPT = """## 답변 구조 (필수)

운영자에게 직접 유용한 이상 분석 보고서를 작성하세요.

### 🚨 핵심 요약
- 전체 이상 건수, 심각도 분포(HIGH/MEDIUM/LOW)를 한 문장으로.
- 가장 주의가 필요한 유형 1개 강조.

### 🔍 유형별 분석
각 이상 유형마다 아래 형식으로:
**[유형명] N건 (HIGH: X / MEDIUM: Y)**
- 대표 시각과 센서값 직접 인용 (예: "계통 1,240 kW, COP 1.3")
- 추정 원인 (불확실하면 "추정"이라고 명시)
- ⬆ 악화 징조 또는 ⬇ 일시적 가능성 판단

### ✅ 즉시 조치 목록
운영자가 오늘 당장 실행할 수 있는 체크리스트 (3개 이내):
- 각 항목은 "누가 어디서 무엇을" 형식으로.
- 이미 회복된 이상은 "경과 관찰"로 표기.

### 📋 배경 참고
이 이상이 게이트웨이 장애 구간이나 Regime 이벤트와 겹치면 반드시 명시.
이상이 없으면 "해당 기간 주요 이상 탐지 없음"을 첫 줄에 쓰고 간략히 마무리.

## 📝 모범 답변 예시
### 🚨 핵심 요약
최근 30일간 총 12건의 이상이 탐지되었습니다 (HIGH 3건, MEDIUM 9건). 가장 주의가 필요한 유형은 **COPDrop**입니다.

### 🔍 유형별 분석
**COPDrop 5건 (HIGH: 2 / MEDIUM: 3)**
- 대표 시각: 2024-07-15 14:00, COP 1.3 (정상 중앙값 2.06 대비 큰 폭 하락)
- 추정 원인: 외기온 32°C 고온 상황에서 냉각탑 효율 저하 또는 응축기 오염 (추정)
- ⬆ 여름철 냉방 부하 증가로 악화 징조 보임

### ✅ 즉시 조치 목록
- 현장팀: H1 구역 냉각탑 팬 작동 상태 및 응축기 오염 점검
- 운영팀: cool_output_P 하락 추이 연속 모니터링
"""


# ── 미터 메타데이터 조회 함수 ─────────────────────────────────────

_meter_cache: dict | None = None


def _load_meters() -> dict:
    """meter_metadata.json을 로드 (캐시)."""
    global _meter_cache
    if _meter_cache is None:
        with open(_METER_PATH, encoding="utf-8") as f:
            _meter_cache = json.load(f)
    return _meter_cache


def get_meter_info(meter_urn: str) -> dict | None:
    """특정 미터의 메타데이터 반환."""
    meters = _load_meters()
    return meters.get("meters", {}).get(meter_urn)


def get_meters_by_building(building: str) -> dict:
    """특정 건물의 모든 미터 반환."""
    meters = _load_meters()
    return {
        urn: info
        for urn, info in meters.get("meters", {}).items()
        if info.get("building") == building
    }


def get_meters_by_group(group: str) -> dict:
    """특정 설비 그룹의 모든 미터 반환."""
    meters = _load_meters()
    return {
        urn: info
        for urn, info in meters.get("meters", {}).items()
        if info.get("group") == group
    }


def get_redundancy_pairs() -> list[dict]:
    """중복 계량 쌍 목록 반환."""
    meters = _load_meters()
    return meters.get("redundancy_pairs", [])


def get_equipment_groups() -> dict:
    """설비 그룹 목록 반환."""
    meters = _load_meters()
    return meters.get("equipment_groups", {})
