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
"""

# ── FEMS 업계 표준 용어 (공통 어휘) ────────────────────────────────

KFEMS_STANDARD_TERMS = """## FEMS 업계 표준 용어

### 주요 개념
- EIS (Energy Information System): 데이터 수집·분석·가시화
- EOS (Energy Optimization System): 최적화·운전 권고
- M&V (Measurement & Verification): 절감량 정량 검증 (IPMVP 기준)

### 핵심 지표
- η (유틸리티 효율) = E_meas / E_BL  — 측정값과 베이스라인의 비율
- EI (공정 원단위) = E_BL / P        — 생산량(P) 대비 에너지 소비
- E_BL (Energy Baseline)             — 기준선 에너지 모델 출력값
- E_BL = Σ E_i (에너지 Balancing)    — 부문별 합산이 메인 미터와 일치해야 함
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
- 자급률 6년평균 39.6%. 30% 이하이면 그리드 과의존.
- cool_elec=0일 때 COP 계산 불가 (0나누기 방어 필수).
- 에너지 공급원 관계: CHP→전기+열 생산, PV→전기 생산, Grid→전기 공급, 냉방→전기 소비.
- PV 야간 NaN은 정상 (센서 꺼짐). 야간 PV > 0만 이상.

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
