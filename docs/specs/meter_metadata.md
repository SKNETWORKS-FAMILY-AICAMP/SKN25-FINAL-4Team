# CMS 계량기 Metadata 명세

**갱신일:** 2026-06-02  
**상태:** Vector DB 적재 기준 문서  
**범위:** CMS 계량기 식별자, 계측 도메인, 설비 mapping, redundancy, 부호 규약, 이상탐지 우선순위를 정의한다.

## 1. 목적

이 문서는 CMS 계량기 metadata의 기준 vocabulary를 정의한다. Vector DB, ontology, QA, report, service layer는 이 문서의 `meter_urn`, `meter_domain`, `meter_role`, `equipment_group`, `redundancy` 정의를 공통 기준으로 사용한다.

## 2. 계량기 수량 기준

| 항목 | 값 |
|---|---:|
| 전체 meter registry | 81 |
| 전기 계량기 | 71 |
| 열 계량기 | 9 |
| 기상 계량기 | 1 |
| redundancy pair | 12 |

## 3. Metadata field

| Field | 의미 | 예시 |
|---|---|---|
| `meter_urn` | 계량기 식별자 | `H1.Z16` |
| `meter_domain` | 계측 도메인 | `electricity`, `thermal`, `weather` |
| `meter_role` | 계량기 역할 | `consumption`, `production`, `weather`, `thermal_flow` |
| `equipment_group` | 설비 또는 계통 그룹 | `central_cooling`, `pv`, `chp`, `server`, `distribution` |
| `equipment_name` | 세부 설비명 | `CM1`, `PV group 1/2`, `CHP` |
| `building_code` | 건물 또는 구역 prefix | `H1`, `H2`, `H3`, `H4`, `V`, `WeatherStation` |
| `sign_convention` | 부호 해석 규칙 | `positive_consumption_negative_production` |
| `anomaly_priority` | 이상탐지 검토 우선순위 | `1`, `2`, `3`, `4` |
| `source_basis` | 분류 근거 | `paper_table`, `source_inventory`, `registry` |
| `note` | 보조 설명 | mapping 정정, 분석 주의점 |

## 4. 부호 규약

| meter_role | 양수 해석 | 음수 해석 | 분석 기준 |
|---|---|---|---|
| `consumption` | 소비 또는 유입 | 계측 오류 후보 | 음수 구간을 QA flag로 표시한다. |
| `production` | 미발전 잡음 또는 역방향 미세값 | 발전 또는 유출 | 음수값을 보존한다. |
| `thermal_flow` | 흡수 또는 유입 | 공급 또는 유출 | 설비 계통 기준으로 해석한다. |
| `weather` | 측정값 | 측정값 | 부호 규약을 적용하지 않는다. |

## 5. 전기 계량기 그룹

| equipment_group | meter_role | meter_urn | 수량 | 설명 |
|---|---|---|---:|---|
| `grid_transformer` | `consumption` | `V.Z82`, `V.Z81`, `H2.Z35`, `H2.Z351`, `H2.Z36`, `H2.Z361` | 6 | 외부 전력망 및 변압기 계통 |
| `chp` | `production` | `H1.Z20`, `H1.ZE20` | 2 | CHP main 및 redundant |
| `pv` | `production` | `V.Z84`, `V.ZE84`, `H1.Z310`, `H2.Z311`, `H3.Z312` | 5 | PV group 계측 |
| `emission_lab` | `consumption` | `H1.Z15`, `H1.Z28`, `H1.Z17`, `H1.Z29`, `H1.Z10`, `H1.Z13`, `H1.Z14`, `H1.Z19`, `H1.Z23`, `H1.Z18`, `H1.Z21`, `H1.Z22`, `H1.Z26`, `H1.Z27` | 14 | Emission Lab 배전 및 시험 설비 |
| `central_cooling` | `consumption` | `H1.Z16`, `H1.Z11`, `H1.Z12`, `H1.Z24`, `H1.Z25` | 5 | CM1, CM2, CM3 전력 계측 |
| `server_power` | `consumption` | `H3.Z43`, `H3.ZE43`, `H3.Z44`, `H3.ZE44`, `H3.Z46`, `H2.Z61`, `H2.Z62`, `H2.Z63`, `H2.Z64`, `H2.ZE64`, `H2.Z65`, `H2.ZE65`, `H3.Z71` | 13 | 서버 및 서버 전원 계통 |
| `local_cooling` | `consumption` | `H3.Z45`, `H2.Z66`, `H2.ZE66`, `H2.Z67`, `H2.ZE67` | 5 | 디자인 스튜디오 및 서버용 로컬 냉방 |
| `ventilation` | `consumption` | `H2.T.Z31`, `H3.Z42`, `H2.Z68`, `H2.Z69`, `H2.Z70` | 5 | 환기 계통 |
| `workshop_test` | `consumption` | `H2.T.Z34`, `H2.ZE74` | 2 | 워크샵 및 테스트 계통 |
| `office_distribution` | `consumption` | `H2.T.Z30`, `H2.T.Z32`, `H4.Z50`, `H4.ZE50`, `H4.Z51`, `H4.ZE51` | 6 | 오피스 및 배전 계통 |
| `design_studio_distribution` | `consumption` | `H2.T.Z33`, `H3.Z40`, `H3.ZE40`, `H3.Z41`, `H3.ZE41`, `H3.Z47`, `H3.Z48`, `H3.Z49` | 8 | 디자인 스튜디오 배전 및 시험 계통 |

전기 계량기 수량 합계는 71개다.

## 6. 열 및 기상 계량기 그룹

| equipment_group | meter_role | meter_urn | 설명 |
|---|---|---|---|
| `cooling_thermal` | `thermal_flow` | `V.K21` | Main cooling machines 1, 2, 3 |
| `hvac_thermal` | `thermal_flow` | `H1.K11` | Emission lab HVAC 3/5 |
| `hvac_thermal` | `thermal_flow` | `H1.K12` | Emission lab HVAC 1/2 |
| `hvac_thermal` | `thermal_flow` | `H1.K14` | Emission lab cooling to office |
| `hvac_thermal` | `thermal_flow` | `H1.K15` | Emission lab HVAC 3 |
| `server_thermal` | `thermal_flow` | `H1.K16` | Server room O1 |
| `hvac_thermal` | `thermal_flow` | `H2.K21` | HVAC office |
| `heat_generation` | `thermal_flow` | `H1.W11` | Total heat generation |
| `chp_heat_generation` | `thermal_flow` | `H1.W12` | CHP heat generation |
| `weather_station` | `weather` | `WeatherStation.Weather` | Weather station |

## 7. 중앙 냉각기 mapping

| 설비 | meter_urn | anomaly_priority | 설명 |
|---|---|---:|---|
| CM1 | `H1.Z16` | 1 | 중앙 냉각기 1 |
| CM2 | `H1.Z11`, `H1.Z12` | 1 | 중앙 냉각기 2 계측 쌍 |
| CM3 | `H1.Z24`, `H1.Z25` | 1 | 중앙 냉각기 3 계측 쌍 |
| 로컬 냉방 | `H3.Z45` | 2 | 디자인 스튜디오 로컬 냉방 |
| 로컬 냉방 | `H2.Z66`, `H2.ZE66` | 2 | 서버용 로컬 냉방 1 |
| 로컬 냉방 | `H2.Z67`, `H2.ZE67` | 2 | 서버용 로컬 냉방 2 |

CM1, CM2, CM3 분석에서는 `H3.Z45`, `H2.Z66`, `H2.ZE66`, `H2.Z67`, `H2.ZE67`을 중앙 냉각기 집계에서 제외한다.

## 8. Redundancy mapping

| primary_meter_urn | redundant_meter_urn | equipment_group | equipment_name |
|---|---|---|---|
| `H1.Z20` | `H1.ZE20` | `chp` | CHP |
| `V.Z84` | `V.ZE84` | `pv` | PV group 1/2 |
| `H3.Z40` | `H3.ZE40` | `design_studio_distribution` | Design studio distribution 1 |
| `H3.Z41` | `H3.ZE41` | `design_studio_distribution` | Design studio distribution 4 |
| `H3.Z43` | `H3.ZE43` | `server_power` | Server O4 cooling 1 |
| `H3.Z44` | `H3.ZE44` | `server_power` | Server O4 cooling 2 |
| `H4.Z50` | `H4.ZE50` | `office_distribution` | Office B4 distribution 3 |
| `H4.Z51` | `H4.ZE51` | `office_distribution` | Office B4 distribution 4 |
| `H2.Z64` | `H2.ZE64` | `server_power` | Server EU power supply 1 |
| `H2.Z65` | `H2.ZE65` | `server_power` | Server EU power supply 2 |
| `H2.Z66` | `H2.ZE66` | `local_cooling` | Local cooling 1 |
| `H2.Z67` | `H2.ZE67` | `local_cooling` | Local cooling 2 |

판정 규칙은 다음과 같다.

1. primary와 redundant가 함께 이상이면 설비 문제 후보로 분류한다.
2. 한쪽만 이상이면 계량기 문제 후보로 분류한다.
3. 두 계량기의 missing interval이 겹치면 source file lineage를 함께 확인한다.
4. production 계량기는 부호 규약을 적용한 뒤 비교한다.

## 9. 이상탐지 우선순위

| priority | 대상 | 기준 |
|---:|---|---|
| 1 | 중앙 냉각기, CHP, grid transformer | 설비 영향도와 에너지 규모가 큰 계통 |
| 2 | PV, 로컬 냉방, 서버 전원, thermal generation | 발전·냉방·서버 운영 영향 계통 |
| 3 | 배전반, 환기, 테스트 설비, 오피스 계통 | 구역별 이상탐지 및 보고 지표 계통 |
| 4 | 기상 계량기 | 외생 변수 품질 검증 계통 |

우선순위는 이상탐지 결과 정렬과 검토 순서에 사용한다. 모델 학습 가중치로 사용할 때는 별도 실험 기준을 둔다.

## 10. Vector DB 적재 기준

| chunk_id | 내용 | 주요 metadata |
|---|---|---|
| `meter.metadata.fields` | metadata field 정의 | `doc_type=spec`, `cms_layer=ontology` |
| `meter.electric_groups` | 전기 계량기 그룹 | `meter_domain=electricity` |
| `meter.thermal_weather_groups` | 열·기상 계량기 그룹 | `meter_domain=thermal,weather` |
| `meter.central_cooling_mapping` | 중앙 냉각기 mapping | `equipment_group=central_cooling` |
| `meter.redundancy_mapping` | redundancy pair | `relationship=redundant_meter` |
| `meter.anomaly_priority` | 이상탐지 우선순위 | `cms_layer=qa` |

## 11. 검증 기준

- `meter_urn` 수량은 81개다.
- `meter_domain`은 `electricity`, `thermal`, `weather` 중 하나다.
- `meter_role`은 `consumption`, `production`, `thermal_flow`, `weather` 중 하나다.
- Redundancy pair는 12개다.
- 모든 redundancy pair의 양쪽 `meter_urn`은 meter registry에 존재해야 한다.
- Vector DB chunk는 이 문서의 section과 source path를 metadata로 보존해야 한다.
