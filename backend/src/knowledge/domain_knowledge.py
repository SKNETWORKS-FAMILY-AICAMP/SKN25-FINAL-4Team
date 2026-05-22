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

# ── 이상 관련 도메인 지식 (anomaly agent 전용) ────────────────────

ANOMALY_DOMAIN_PROMPT = """## 이상탐지 관련 도메인 지식

### 이상 유형 및 영향
- PowerSpike → 전기 시스템 과부하. 그리드 전력 급증 확인 필요.
- COPDrop → 냉방 시스템·COP 지표 영향. 냉각기 상태·냉매 확인.
- NightConsumption → 전기 시스템. 야간 운영 설비 점검.
- CHPOutage → 자급률·그리드 의존도 급변. CHP 전기+열 동시 확인.
- PVNightNonZero → PV 시스템. 센서 교정 필요.

### KPI 해석 기준
- COP 중앙값 2.06. 1.5 이하이면 심각한 효율 저하.
- 자급률 6년평균 39.6%. 30% 이하이면 그리드 과의존.
- cool_elec=0일 때 COP 계산 불가 (0나누기 방어 필수).

### 주의사항
- 에너지 공급원 관계: CHP→전기+열 생산, PV→전기 생산, Grid→전기 공급, 냉방→전기 소비.
- PV 야간 NaN은 정상 (센서 꺼짐). 야간 PV > 0만 이상.
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
