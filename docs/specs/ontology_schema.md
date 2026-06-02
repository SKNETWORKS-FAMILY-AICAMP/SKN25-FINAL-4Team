# CMS Ontology Schema 명세

**갱신일:** 2026-06-02
**범위:** Honda R&D Europe source dataset에서 출발한 계량기 metadata, measurement vocabulary, 설비 group, 건물, 역할, redundancy, QA/source provenance를 CMS active contract로 해석하기 위한 ontology 기준.

## 1. 목적

이 문서는 CMS ontology artifact의 class, property, relationship, constraint 후보를 정의한다. 시계열 measurement row는 PostgreSQL `canonical`/`reference`/`qa` layer에서 관리하고, ontology는 metadata, source provenance, 계량기 관계, anomaly explanation grounding을 담당한다.

현재 generated RDF artifact는 legacy namespace와 파일명 `ems.ttl`을 유지한다. 이는 source dataset/legacy artifact 호환을 위한 이름이며, active architecture prose와 runtime package는 `CMS`/`cms`를 기준으로 한다.

## 2. 기준 원천

| Tier | 원천 | 역할 | 현재 상태 |
|---|---|---|---|
| Tier 0 | Nature Scientific Data DOI `10.1038/s41597-025-05186-3` | Honda R&D Europe dataset 원문, meter/measurement/issue/cadence provenance | 기준 원문 |
| Tier 0 | Dryad DOI `10.5061/dryad.73n5tb363` | compressed source archive, file layout, license, checksum provenance | 기준 원천 저장소 |
| Tier 1 | `docs/reference/source_inventory.md` | CMS-only source tier와 archive 선별 기준 | 기준 문서 |
| Tier 1 | `docs/specs/meter_metadata.md` | 계량기 classification과 redundancy 기준 | 기준 문서 |
| Tier 1 | `docs/specs/meter_measurement_cadence_policy.md` | native cadence, expected_points, gap/null policy | 기준 문서 |
| Tier 1 | `docs/specs/data_contract.md` | source/candidate/QA/canonical/reference boundary | 기준 문서 |
| Tier 1 | `docs/ontology/ems.ttl` | Protégé와 rdflib에서 읽는 Turtle artifact | 자동 생성 대상 |
| Tier 1 | `docs/ontology/ems_shapes.ttl` | SHACL closed-world validation artifact | 자동 검증 대상 |
| Tier 1 | `docs/ontology/competency_questions.md` | ontology helper가 답해야 하는 질문 | 검증 기준 |
| Tier 1 | `scripts/ontology/query_ontology.py` | SPARQL query smoke test | 검증 스크립트 |

Benchmark/model reports, champion model 결과, Huang-style 실험은 ontology source truth에서 제외한다.

## 3. Class 정의

| Class | 의미 | 현재 instance/source |
|---|---|---|
| `Meter` | Honda source dataset의 계량기 URN | `meter_metadata.md`, Nature Tables 2-3 |
| `ElectricityMeter` | 전기 계량기 | `meter_domain=electricity` |
| `ThermalMeter` | 열 계량기 | `meter_domain=thermal` |
| `WeatherMeter` | 기상 계량기 | `meter_domain=weather` |
| `Measurement` | `P`, `W`, `U1`, `I1`, `Ta` 같은 measurement code vocabulary | Nature Tables 4-6 |
| `MeasurementFamily` | power, energy, voltage, current, power_factor, temperature, weather 등 aggregation policy family | source inventory + domain concepts |
| `NativeCadencePolicy` | meter/measurement/source layer별 native interval과 expected_points policy | cadence policy |
| `EquipmentGroup` | 설비 또는 계통 group | `meter_metadata.md` |
| `Building` | meter URN prefix 기준 건물 또는 zone | meter metadata |
| `MeterRole` | consumption, production, thermal_flow, weather 역할 | meter metadata |
| `RedundancyPair` | primary/redundant pair | redundancy table |
| `HardwareModel` | 계량기 하드웨어 모델 | Nature Tables 2-3, generated TTL |
| `DataLayer` | archive, staging, reference, canonical, qa, ops, mart | data/database contract |
| `QAEvidence` | gap/null/coverage/mask/provenance evidence | QA contract |
| `FeatureRule` | feature 생성·포함·제외 규칙 | feature spec |
| `PromptContract` | SQLLM, QA review, anomaly explanation 등 LLM prompt boundary | `llm_pipeline_contract.md` |
| `MetadataDocument` | ontology/source 기준 문서 | specs/reference/ontology docs |

## 4. Object property 정의

| Property | Domain | Range | 의미 |
|---|---|---|---|
| `belongsToGroup` | `Meter` | `EquipmentGroup` | meter가 equipment group에 속함 |
| `locatedInBuilding` | `Meter` | `Building` | meter가 building 또는 zone에 속함 |
| `hasRole` | `Meter` | `MeterRole` | meter의 분석 역할 |
| `redundantWith` | `Meter` | `Meter` | 중복 계량 관계 |
| `hasPrimaryMeter` | `RedundancyPair` | `Meter` | redundancy pair의 primary meter |
| `hasRedundantMeter` | `RedundancyPair` | `Meter` | redundancy pair의 redundant meter |
| `hasGroup` | `RedundancyPair` | `EquipmentGroup` | redundancy pair가 속한 group |
| `hasHardwareModel` | `Meter` | `HardwareModel` | meter의 하드웨어 모델 |
| `measuresMeasurement` | `Meter` | `Measurement` | meter가 제공하는 measurement code |
| `hasMeasurementFamily` | `Measurement` | `MeasurementFamily` | measurement의 aggregation family |
| `hasNativeCadencePolicy` | `Meter` 또는 `Measurement` | `NativeCadencePolicy` | expected_points/gap policy |
| `belongsToDataLayer` | ontology entity | `DataLayer` | source/reference/canonical 등 layer 연결 |
| `hasQAEvidence` | candidate/canonical artifact | `QAEvidence` | QA evidence 연결 |
| `usesPromptContract` | LLM route 또는 workflow | `PromptContract` | prompt boundary 연결 |
| `definedBy` | ontology entity | `MetadataDocument` | entity 기준 문서 |
| `visualizedBy` | `Meter` 또는 `EquipmentGroup` | visualization artifact | 관계를 확인할 수 있는 view |

## 5. Data property 정의

| Property | Domain | Range | 의미 |
|---|---|---|---|
| `meterUrn` | `Meter` | `xsd:string` | DB meter identifier |
| `meterDomain` | `Meter` | `xsd:string` | electricity, thermal, weather |
| `meterRoleCode` | `Meter` | `xsd:string` | consumption, production, thermal_flow, weather |
| `equipmentGroupCode` | `Meter` 또는 `EquipmentGroup` | `xsd:string` | group code |
| `equipmentName` | `Meter` 또는 `RedundancyPair` | `xsd:string` | 설비명 또는 보조 이름 |
| `equipmentLayer` | `Meter` | `xsd:string` | group 내부 계층 |
| `buildingCode` | `Meter` 또는 `Building` | `xsd:string` | building 또는 zone code |
| `measurementCode` | `Measurement` | `xsd:string` | `P`, `W`, `U1`, `Ta` 등 |
| `unit` | `Measurement` | `xsd:string` | source unit |
| `nativeIntervalSeconds` | `NativeCadencePolicy` | `xsd:integer` | source native interval |
| `cadenceConfidence` | `NativeCadencePolicy` | `xsd:string` | proven, mismatch, unknown 등 |
| `expectedPointsPolicy` | `NativeCadencePolicy` | `xsd:string` | target bucket expected_points rule |
| `signConvention` | `Meter` 또는 `MeterRole` | `xsd:string` | 부호 해석 규칙 |
| `anomalyPriority` | `Meter` 또는 `EquipmentGroup` | `xsd:integer` | 이상탐지 검토 우선순위 |
| `hardwareModelCode` | `Meter` 또는 `HardwareModel` | `xsd:string` | 계량기 하드웨어 모델 코드 |
| `manufacturer` | `HardwareModel` | `xsd:string` | 제조사 |
| `modelName` | `HardwareModel` | `xsd:string` | 모델명 |
| `sourceName` | ontology entity | `xsd:string` | metadata 출처명 |
| `sourceTable` | ontology entity | `xsd:string` | 원문 표 또는 문서명 |
| `sourceDescription` | ontology entity | `xsd:string` | 원문 description |
| `sourcePath` | `MetadataDocument` | `xsd:string` | project-relative source path |

## 6. Constraint 후보

| Constraint | 현재 적용 방식 | 향후 formalization 후보 |
|---|---|---|
| 모든 meter는 하나의 equipment group을 가진다 | generation validation 및 SHACL validation | OWL cardinality 1 |
| 모든 meter는 하나의 building code를 가진다 | generation validation 및 SHACL validation | OWL cardinality 1 |
| 모든 meter는 하나의 role을 가진다 | generation validation 및 SHACL validation | OWL cardinality 1 |
| redundancy pair는 primary와 redundant meter를 각각 하나씩 가진다 | generation validation 및 SHACL validation | OWL cardinality 1 |
| weather meter는 energy aggregate에 포함하지 않는다 | feature contract | SHACL 또는 rule 문서 |
| production meter의 음수 value는 production/outflow 후보로 보존한다 | meter metadata | rule 문서 |
| redundant pair는 기본 aggregate에서 중복 합산하지 않는다 | feature contract | rule 문서 |
| native cadence가 unknown/mismatch이면 canonical promotion을 차단한다 | cadence policy | SHACL 또는 QA rule |
| corrected/resampled reference는 canonical observed truth가 될 수 없다 | data/QA contract | layer constraint |
| SQLLM은 SELECT-only whitelist 밖 schema를 조회하지 않는다 | LLM contract | prompt contract 검증 |

## 7. Artifact coverage 기준

현재 generated artifact는 다음 coverage를 기준으로 검증한다. Class/property 확장안은 문서 기준이며, TTL 재생성 전까지 generated count와 다를 수 있다.

| 항목 | 기준 |
|---|---:|
| `Meter` | 81 |
| `EquipmentGroup` | 17 |
| `Building` | 6 |
| `MeterRole` | 4 |
| `RedundancyPair` | 12 |
| `HardwareModel` | 6 |

추가 mapping coverage:

1. 모든 meter는 `belongsToGroup`, `locatedInBuilding`, `hasRole`, `meterDomain`, `meterUrn`, `noteFile`, `signConvention`, `anomalyPriority` 값을 가진다.
2. 모든 equipment group은 `meterCount`, `primaryView`, `anomalyPriority`, `noteFile` 값을 가진다.
3. Redundancy pair는 `hasPrimaryMeter`, `hasRedundantMeter`, `hasGroup`을 각각 하나씩 가진다.
4. `redundantWith`는 각 redundancy pair에 대해 양방향으로 materialize한다.
5. 모든 meter는 Nature Tables 2-3 기반 `hasHardwareModel`, `hardwareModelCode`, `sourceName`, `sourceTable`, `sourceDescription` 값을 가진다.
6. Measurement vocabulary는 Nature Tables 4-6과 `docs/reference/domain_concepts.md`를 함께 참조한다.

## 8. CMS 분석에서의 사용

### 8.1 Feature 설계

Ontology는 feature 생성 시 포함·제외 기준을 제공한다.

```text
equipment_group = server_power
meter_domain = electricity
meter_role = consumption
redundant pair 제외 또는 primary 우선
weather meter는 energy aggregate에서 제외
```

### 8.2 Live/replay anomaly 설명

Anomaly event가 meter 단위로 발생하면 다음 context를 결합한다.

```text
meter -> measurement -> equipment group -> building -> role -> redundancy pair -> hardware model -> QA evidence
```

설명은 설비 고장 확정이 아니라 점검 후보로 표현한다. alarm log, BMS state, setpoint, physical wiring은 source evidence가 없으면 추정하지 않는다.

### 8.3 LLM grounding

LLM agent는 `docs/ontology/ems.ttl`, `docs/ontology/competency_questions.md`, `scripts/ontology/query_ontology.py`, `docs/reference/source_inventory.md`, 검토된 Wiki/Obsidian project note를 함께 확인한다. Graphify나 Obsidian 자동 생성 note는 후보 지식으로만 사용하고, 중요한 관계는 source artifact와 active spec으로 재확인한다.

## 9. Protégé 사용 기준

Protégé는 `docs/ontology/ems.ttl` 또는 `docs/ontology/ems_protege.owl`을 열어 class hierarchy, object property, data property, individual 관계를 검토하는 데 사용한다. `ems_protege.owl`은 Protégé GUI 확인을 위한 RDF/XML artifact다. 수동 편집으로 canonical source를 변경하지 않는다. 변경 필요 시 `meter_metadata.md`, source inventory, 또는 향후 DB metadata table을 수정한 뒤 artifact를 재생성한다.

## 10. 제외 범위

1. 6년치 measurement row를 RDF로 변환하지 않는다.
2. 물리 배선도, 차단기 구성, 제어 로직을 확정하지 않는다.
3. Benchmark/model performance를 ontology truth로 쓰지 않는다.
4. Neo4j, Fuseki, GraphDB 같은 server 기반 graph 저장소는 현 단계에서 도입하지 않는다. Graphify artifact와 MCP hook 후보를 사용한다.
5. DB schema 변경이나 AWS DDL 실행은 이 문서 범위가 아니라 별도 승인된 infra 작업이다.
