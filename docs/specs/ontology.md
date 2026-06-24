# CMS Ontology Schema 명세

**갱신일:** 2026-06-16
**범위:** Honda R&D Europe source dataset에서 출발한 계량기 metadata, measurement vocabulary, 설비 group, 건물, 역할, redundancy, QA/source provenance를 CMS active contract로 해석하기 위한 ontology 기준.

## 1. 목적

이 문서는 CMS ontology artifact의 class, property, relationship, constraint 후보를 정의한다. 시계열 measurement row는 PostgreSQL `canonical`/`reference`/`qa` layer에서 관리하고, ontology는 metadata, source provenance, 계량기 관계, anomaly explanation grounding을 담당한다.

현재 generated RDF artifact는 `schema.ttl`, `shapes.ttl`, `protege.owl` 파일명을 사용한다. CMS는 Condition Monitoring System을 뜻하며 ontology namespace도 CMS 기준이다.

## 2. 기준 원천

| Tier | 원천 | 역할 | 현재 상태 |
|---|---|---|---|
| Tier 0 | Nature Scientific Data DOI `10.1038/s41597-025-05186-3` | Honda R&D Europe dataset 원문, meter/measurement/issue/cadence provenance | 기준 원문 |
| Tier 0 | Dryad DOI `10.5061/dryad.73n5tb363` | compressed source archive, file layout, license, checksum provenance | 기준 원천 저장소 |
| Tier 1 | `docs/reference/source_inventory.md` | CMS-only source tier와 archive 선별 기준 | 기준 문서 |
| Tier 1 | `docs/specs/meter_metadata.md` | 계량기 classification과 redundancy 기준 | 기준 문서 |
| Tier 1 | `docs/specs/measurement_processing_policy.md` | native cadence, expected_points, NULL/state-hold, measurement processing policy | 기준 문서 |
| Tier 1 | `docs/specs/data_platform.md` | source/candidate/QA/canonical/reference boundary | 기준 문서 |
| Tier 1 | `live.measurement_policy` | observed `(meter_urn, measurement)` cadence/policy inventory seed | 서버 DB 적재 확인 |
| Tier 1 | `docs/ontology/schema.ttl` | Protégé와 rdflib에서 읽는 Turtle artifact | 자동 생성 대상 |
| Tier 1 | `docs/ontology/shapes.ttl` | SHACL closed-world validation artifact | 자동 검증 대상 |
| Tier 1 | `docs/specs/ontology.md` | ontology helper가 답해야 하는 질문 | 검증 기준 |
| Tier 1 | `scripts/ontology/query.py` | SPARQL query smoke test | 검증 스크립트 |

Benchmark/model outputs, champion model 결과, Huang-style 실험은 ontology source truth에서 제외한다.

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
| `PromptContract` | SQLLM, QA review, anomaly explanation 등 LLM prompt boundary | `llm_contract.md` |
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
| `MeasurementCode` | 40 |
| `MeterMeasurement` | 1603 |
| RDF triple projection | 3006 |

추가 mapping coverage:

1. 모든 meter는 `belongsToGroup`, `locatedInBuilding`, `hasRole`, `meterDomain`, `meterUrn`, `noteFile`, `signConvention`, `anomalyPriority` 값을 가진다.
2. 모든 equipment group은 `meterCount`, `primaryView`, `anomalyPriority`, `noteFile` 값을 가진다.
3. Redundancy pair는 `hasPrimaryMeter`, `hasRedundantMeter`, `hasGroup`을 각각 하나씩 가진다.
4. `redundantWith`는 각 redundancy pair에 대해 양방향으로 materialize한다.
5. 모든 meter는 Nature Tables 2-3 기반 `hasHardwareModel`, `hardwareModelCode`, `sourceName`, `sourceTable`, `sourceDescription` 값을 가진다.
6. Measurement vocabulary는 Nature Tables 4-6과 `docs/reference/measurement_glossary.md`를 함께 참조한다.

## 8. PostgreSQL relational projection

SLLM/db-helper가 RDF/SPARQL 없이 안정적으로 조회할 수 있도록 서버 PostgreSQL에는 `ontology` schema의 relational projection을 둔다. 이 projection은 operational time-series row를 복제하지 않고, 계량기·건물·설비군·measurement vocabulary·redundancy·hardware·policy 관계만 보관한다.

현재 서버 DB에서 확인된 object와 row count는 다음과 같다.

| Object | 역할 | 확인 count |
|---|---|---:|
| `ontology.building` | building/zone vocabulary | 6 |
| `ontology.equipment_group` | 설비군 vocabulary | 17 |
| `ontology.meter_role` | meter role vocabulary | 4 |
| `ontology.hardware_model` | hardware model vocabulary | 6 |
| `ontology.meter` | 81개 source meter metadata | 81 |
| `ontology.redundancy_pair` | primary/redundant pair | 12 |
| `ontology.measurement_code` | active archive measurement code vocabulary | 40 |
| `ontology.meter_measurement` | `live.measurement_policy` 기반 observed meter/measurement policy projection | 1603 |
| `ontology.triple` | `docs/ontology/schema.ttl`에서 투영한 generic triples | 3006 |
| `ontology.meter_context` | meter -> group/building/role/hardware/redundancy helper view | view |
| `ontology.meter_measurement_context` | meter -> measurement -> cadence/policy helper view | view |

`ontology.meter_measurement`는 `live.measurement_policy`의 1605개 distinct `(meter_urn, measurement)` 중 ontology meter와 measurement vocabulary에 join되는 1603개만 적재한다. `SMOKE.T1/P`, `SMOKE.T3/P`는 테스트 row이므로 ontology meter로 승격하지 않는다.

Projection 적재 기준은 다음과 같다.

1. `ontology.meter`, `ontology.building`, `ontology.equipment_group`, `ontology.meter_role`, `ontology.redundancy_pair`는 `src/cms/knowledge/meter_metadata.json`을 기준으로 한다.
2. `ontology.hardware_model`과 `ontology.triple`은 `docs/ontology/schema.ttl`에서 seed를 생성해 투영한다.
3. `ontology.measurement_code`는 active harmonized archive에 존재하는 40개 code를 기준으로 한다. 논문 vocabulary에 등장하는 `Pa`는 현재 active archive inventory에는 없으므로 초기 projection에서 제외한다.
4. `ontology.meter_measurement`는 `live.measurement_policy`를 source table로 삼고, `source_policy_id`, `policy_version`, `source_update_mode`, `cadence_group`, `source_native_interval_seconds`, `target_resolution_policy`, `aggregation_policy`, `expected_points_policy`, `canonical_eligible`을 함께 보존한다.
5. Helper role은 `ontology` schema에 대해 SELECT-only 권한만 가진다. `INSERT`, `UPDATE`, `DELETE`는 부여하지 않는다.

검증 gate는 다음을 모두 만족해야 한다.

```text
orphan meter/group/building/role/hardware = 0
orphan meter_measurement meter/measurement = 0
duplicate meter_measurement pair = 0
helper role SELECT = true
helper role INSERT/UPDATE/DELETE = false
```

## 9. CMS 분석에서의 사용

### 9.1 Feature 설계

Ontology는 feature 생성 시 포함·제외 기준을 제공한다.

```text
equipment_group = server_power
meter_domain = electricity
meter_role = consumption
redundant pair 제외 또는 primary 우선
weather meter는 energy aggregate에서 제외
```

### 9.2 Live/replay anomaly 설명

Anomaly event가 meter 단위로 발생하면 다음 context를 결합한다.

```text
meter -> measurement -> equipment group -> building -> role -> redundancy pair -> hardware model -> QA evidence
```

설명은 설비 고장 확정이 아니라 점검 후보로 표현한다. alarm log, BMS state, setpoint, physical wiring은 source evidence가 없으면 추정하지 않는다.

### 9.3 LLM grounding

LLM retrieval은 `docs/ontology/schema.ttl`, `docs/specs/ontology.md`, `scripts/ontology/query.py`, `docs/reference/source_inventory.md`, `ontology.meter_context`, `ontology.meter_measurement_context`를 우선 근거로 사용한다. Graphify artifact는 후보 관계 탐색에만 사용하고, 중요한 관계는 source artifact와 active spec/DB projection으로 재확인한다.

## 10. Protégé 사용 기준

Protégé는 `docs/ontology/schema.ttl` 또는 `docs/ontology/protege.owl`을 열어 class hierarchy, object property, data property, individual 관계를 검토하는 데 사용한다. `protege.owl`은 Protégé GUI 확인을 위한 RDF/XML artifact다. 수동 편집으로 canonical source를 변경하지 않는다. 변경 필요 시 `meter_metadata.md`, source inventory, 또는 향후 DB metadata table을 수정한 뒤 artifact를 재생성한다.

## 11. 제외 범위

1. 6년치 measurement row를 RDF로 변환하지 않는다.
2. 물리 배선도, 차단기 구성, 제어 로직을 확정하지 않는다.
3. Benchmark/model performance를 ontology truth로 쓰지 않는다.
4. Neo4j, Fuseki, GraphDB 같은 server 기반 graph 저장소는 현 단계에서 도입하지 않는다. Graphify artifact와 MCP hook 후보를 사용한다.
5. `ontology` projection 외 schema 변경이나 AWS DDL 실행은 이 문서 범위가 아니라 별도 승인된 infra 작업이다.

## 12. 역량질문

Ontology는 다음 질문에 답할 수 있어야 한다.

| 영역 | 질문 | 기준 문서 |
|---|---|---|
| Meter context | 특정 meter의 type, role, location, redundancy group은 무엇인가 | `docs/specs/meter_metadata.md` |
| Aggregate meter set | 집계 meter와 하위 meter의 관계는 무엇인가 | `docs/specs/meter_metadata.md` |
| Production sign convention | 생산/소비 방향은 어떤 property로 표현되는가 | `docs/specs/measurement_processing_policy.md` |
| Weather exclusion | weather source가 energy aggregate에 섞이지 않았는가 | `docs/specs/measurement_processing_policy.md` |
| Redundancy anomaly | redundant meter 간 divergence를 어떻게 해석하는가 | `docs/specs/meter_metadata.md` |
| Native cadence and coverage | source cadence와 expected/observed coverage는 어디서 확인하는가 | `docs/specs/measurement_processing_policy.md` |
| Meter measurement lookup | 특정 meter가 제공하는 measurement와 cadence/policy는 무엇인가 | `ontology.meter_measurement_context`, `live.measurement_policy` |
| Corrected/reference leakage | corrected/reference source가 observed truth로 사용되지 않았는가 | `docs/qa/qa_contract.md` |
| SQLLM grounding | Text-to-SQL이 ontology와 schema boundary를 지키는가 | `docs/specs/llm_contract.md` |

역량질문은 ontology artifact의 acceptance criteria로 유지한다. Data QA와 runtime policy의 상세 기준은 각 대표 문서가 담당한다.
