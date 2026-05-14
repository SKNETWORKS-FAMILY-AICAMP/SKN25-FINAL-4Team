# Meter Description Table
이 문서는 계량기 이름을 EDA/이상탐지/예측 해석에 바로 연결하기 위한 초안입니다. 상세 원배선도나 운영 로직은 포함하지 않고, 현재 `meter_metadata.json` 기준 의미만 정리합니다.
## 핵심 해석 규칙
- `meter_type`: 전기/열량/기상 구분
- `energy_type`: 소비/생산/중립 구분
- `anomaly_target`: 현재 공통 파이프라인에서 이상탐지 기준으로 쓰는 컬럼
- `anomaly_target = null`: 현재 공통 이상탐지 대상에서 제외
- `redundant_pair`: 이중화 계량기 쌍

## chp
역할 요약: CHP 발전 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H1.Z20 | CHP 전기 생산 메인 | 전기 | 생산 | - | P | H1.ZE20 | H1 prefix는 physical location / sub-distribution group 의미, 이중화 계량기 쌍 존재 |
| H1.ZE20 | CHP 전기 생산 redundant | 전기 | 생산 | - | P | H1.Z20 | H1 prefix는 physical location / sub-distribution group 의미, 이중화 계량기 쌍 존재 |

## pv
역할 요약: 태양광 발전 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| V.Z84 | PV 그룹1&2 메인 | 전기 | 생산 | - | P | V.ZE84 | V prefix는 physical location / sub-distribution group 의미, 2019-06 설치 시작, 이중화 계량기 쌍 존재 |
| V.ZE84 | PV 그룹1&2 redundant | 전기 | 생산 | - | P | V.Z84 | V prefix는 physical location / sub-distribution group 의미, 2022-11 redundant 설치, 이중화 계량기 쌍 존재 |
| H1.Z310 | PV 그룹3 | 전기 | 생산 | - | P | None | H1 prefix는 physical location / sub-distribution group 의미, 2020-06 설치 시작 |
| H2.Z311 | PV 그룹4&5 | 전기 | 생산 | - | P | None | H2 prefix는 physical location / sub-distribution group 의미, 2020-06 설치 시작 |
| H3.Z312 | PV 그룹6 | 전기 | 생산 | - | P | None | H3 prefix는 physical location / sub-distribution group 의미, 2020-06 설치 시작 |

## cooling_central
역할 요약: 중앙 냉각기 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H1.Z16 | CM1 | 전기 | 소비 | - | P | None |  |
| H1.Z11 | CM2-1 | 전기 | 소비 | - | P | None |  |
| H1.Z12 | CM2-2 | 전기 | 소비 | - | P | None |  |
| H1.Z24 | CM3-1 | 전기 | 소비 | - | P | None |  |
| H1.Z25 | CM3-2 | 전기 | 소비 | - | P | None |  |

## cooling_local
역할 요약: 로컬 냉각기 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H3.Z45 | Design Studio 로컬 냉각기 | 전기 | 소비 | - | P | None |  |
| H2.Z66 | Workshop 로컬 냉각기1 메인 | 전기 | 소비 | - | P | H2.ZE66 | 이중화 계량기 쌍 존재 |
| H2.ZE66 | Workshop 로컬 냉각기1 redundant | 전기 | 소비 | - | P | H2.Z66 | 이중화 계량기 쌍 존재 |
| H2.Z67 | Workshop 로컬 냉각기2 메인 | 전기 | 소비 | - | P | H2.ZE67 | 이중화 계량기 쌍 존재 |
| H2.ZE67 | Workshop 로컬 냉각기2 redundant | 전기 | 소비 | - | P | H2.Z67 | 이중화 계량기 쌍 존재 |

## cooling_thermal
역할 요약: 냉방 열량 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| V.K21 | 중앙 냉수 공급 메인 | 열량 | 중립 | 냉방 | Tdiff | None |  |
| H1.K11 | HVAC 3&5 | 열량 | 중립 | 냉방 | Tdiff | None |  |
| H1.K12 | HVAC 2 | 열량 | 중립 | 냉방 | Tdiff | None |  |
| H1.K14 | Office&Reception | 열량 | 중립 | 냉방 | Tdiff | None |  |
| H1.K15 | HVAC 3 | 열량 | 중립 | 냉방 | Tdiff | None |  |
| H1.K16 | Server O1 | 열량 | 중립 | 냉방 | Tdiff | None |  |
| H2.K21 | Office 냉방 | 열량 | 중립 | 냉방 | Tdiff | None |  |

## heating_thermal
역할 요약: 난방 열량 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H1.W11 | 총 난방 | 열량 | 중립 | 난방 | Tdiff | None |  |
| H1.W12 | CHP 열 생산 | 열량 | 중립 | 난방 | Tdiff | None | H1 prefix는 physical location / sub-distribution group 의미, CHP thermal side metering |

## weather
역할 요약: 기상 데이터 계측

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| WeatherStation.Weather | 기상관측소 | 기상 | 중립 | - | None | None | 공통 이상탐지 파이프라인 제외 대상 |

## transformer
역할 요약: 변압기/상위 전력 공급 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| V.Z81 | Transformer | 전기 | 소비 | - | P | None |  |
| V.Z82 | Transformer | 전기 | 소비 | - | P | None |  |
| H2.Z35 | Transformer | 전기 | 소비 | - | P | H2.Z351 | 이중화 계량기 쌍 존재 |
| H2.Z351 | Transformer redundant | 전기 | 소비 | - | P | H2.Z35 | 이중화 계량기 쌍 존재 |
| H2.Z36 | Transformer | 전기 | 소비 | - | P | H2.Z361 | 이중화 계량기 쌍 존재 |
| H2.Z361 | Transformer redundant | 전기 | 소비 | - | P | H2.Z36 | 이중화 계량기 쌍 존재 |

## distribution
역할 요약: 배전/분전 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H1.Z15 | Distribution | 전기 | 소비 | - | None | None | 공통 이상탐지 파이프라인 제외 대상 |
| H1.Z28 | Distribution | 전기 | 소비 | - | None | None | 공통 이상탐지 파이프라인 제외 대상 |
| H2.T.Z31 | Distribution | 전기 | 소비 | - | P | None |  |
| H2.T.Z32 | Distribution | 전기 | 소비 | - | P | None |  |
| H2.T.Z33 | Distribution | 전기 | 소비 | - | P | None |  |
| H2.T.Z34 | Distribution | 전기 | 소비 | - | P | None |  |
| H2.T.Z30 | Distribution | 전기 | 소비 | - | P | None |  |

## hvac
역할 요약: 공조 설비 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H1.Z13 | HVAC | 전기 | 소비 | - | P | None |  |
| H1.Z14 | HVAC | 전기 | 소비 | - | P | None |  |

## test
역할 요약: 테스트/실험 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H1.Z10 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z17 | Test | 전기 | 소비 | - | None | None | 공통 이상탐지 파이프라인 제외 대상 |
| H1.Z18 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z19 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z21 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z22 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z23 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z26 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z27 | Test | 전기 | 소비 | - | P | None |  |
| H1.Z29 | Test | 전기 | 소비 | - | None | None | 공통 이상탐지 파이프라인 제외 대상 |

## server
역할 요약: 서버/IT 부하 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H2.Z61 | Server | 전기 | 소비 | - | P | None |  |
| H2.Z62 | Server | 전기 | 소비 | - | P | None |  |
| H2.Z63 | Server | 전기 | 소비 | - | P | None |  |
| H2.Z64 | Server | 전기 | 소비 | - | P | H2.ZE64 | 이중화 계량기 쌍 존재 |
| H2.ZE64 | Server redundant | 전기 | 소비 | - | P | H2.Z64 | 이중화 계량기 쌍 존재 |
| H2.Z65 | Server | 전기 | 소비 | - | P | H2.ZE65 | 이중화 계량기 쌍 존재 |
| H2.ZE65 | Server redundant | 전기 | 소비 | - | P | H2.Z65 | 이중화 계량기 쌍 존재 |
| H2.Z71 | Server | 전기 | 소비 | - | P | None |  |
| H3.Z46 | Server | 전기 | 소비 | - | P | None |  |
| H3.Z43 | Server | 전기 | 소비 | - | P | H3.ZE43 | 이중화 계량기 쌍 존재 |
| H3.ZE43 | Server redundant | 전기 | 소비 | - | P | H3.Z43 | 이중화 계량기 쌍 존재 |
| H3.Z44 | Server | 전기 | 소비 | - | P | H3.ZE44 | 이중화 계량기 쌍 존재 |
| H3.ZE44 | Server redundant | 전기 | 소비 | - | P | H3.Z44 | 이중화 계량기 쌍 존재 |

## ventilation
역할 요약: 환기 설비 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H2.Z68 | Ventilation | 전기 | 소비 | - | P | None |  |
| H2.Z69 | Ventilation | 전기 | 소비 | - | P | None |  |
| H2.Z70 | Ventilation | 전기 | 소비 | - | P | None |  |
| H3.Z42 | Ventilation | 전기 | 소비 | - | P | None |  |

## design_dis
역할 요약: 디자인 구역 배전 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H3.Z40 | Design distribution | 전기 | 소비 | - | P | H3.ZE40 | 이중화 계량기 쌍 존재 |
| H3.ZE40 | Design distribution redundant | 전기 | 소비 | - | P | H3.Z40 | 이중화 계량기 쌍 존재 |
| H3.Z41 | Design distribution | 전기 | 소비 | - | P | H3.ZE41 | 이중화 계량기 쌍 존재 |
| H3.ZE41 | Design distribution redundant | 전기 | 소비 | - | P | H3.Z41 | 이중화 계량기 쌍 존재 |

## office_dis
역할 요약: 오피스 구역 배전 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H4.Z50 | Office distribution | 전기 | 소비 | - | P | H4.ZE50 | 이중화 계량기 쌍 존재 |
| H4.ZE50 | Office distribution redundant | 전기 | 소비 | - | P | H4.Z50 | 이중화 계량기 쌍 존재 |
| H4.Z51 | Office distribution | 전기 | 소비 | - | P | H4.ZE51 | 이중화 계량기 쌍 존재 |
| H4.ZE51 | Office distribution redundant | 전기 | 소비 | - | P | H4.Z51 | 이중화 계량기 쌍 존재 |

## simulation
역할 요약: 시뮬레이션 구역 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H3.Z47 | Simulation | 전기 | 소비 | - | P | None |  |
| H3.Z48 | Simulation | 전기 | 소비 | - | P | None |  |
| H3.Z49 | Simulation | 전기 | 소비 | - | P | None |  |

## robolab
역할 요약: 로보랩 전력 계통

| meter_urn | 설명 | 타입 | 에너지 | thermal_mode | anomaly_target | redundant_pair | 비고 |
|---|---|---|---|---|---|---|---|
| H2.ZE74 | Robolab | 전기 | 소비 | - | P | None |  |
