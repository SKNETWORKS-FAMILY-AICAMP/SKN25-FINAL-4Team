# CMS Source Inventory

**Updated:** 2026-06-02
**Scope:** CMS ontology와 vector DB 기준 문서에 반영할 source tier. Graphify MCP/CLI graph는 `docs/specs` 전용으로 별도 제한한다. Benchmark나 paper-model 실험 결과는 원본 데이터 이해에 필요한 경우를 제외하고 제외한다.

## 1. 기준 원칙

- Active architecture와 문서 표기는 `CMS` / `cms`를 기준으로 한다.
- `EMS`는 Honda R&D Europe 원천 dataset wording, legacy DB/schema/ontology namespace, archive provenance에서만 사용한다.
- 전력 예측은 향후 CMS mart/model lane에 들어갈 수 있지만, 이 inventory의 기준 source는 forecasting benchmark가 아니라 Honda R&D Europe 원본 데이터 논문과 CMS data contract다.
- `_archive`의 모델 성능표, SVR/LSTM/XGBoost 실험, Huang-style benchmark, champion model report는 vector DB/ontology source truth에서 제외한다.

## 2. Tier 0: 원문 데이터 source

| Source | Provenance | CMS에서의 용도 |
|---|---|---|
| Nature Scientific Data article | DOI `10.1038/s41597-025-05186-3` | dataset scope, facility, meter inventory, measurement dictionary, issue/gap semantics의 최상위 원문 |
| Honda RI PDF | `https://www.honda-ri.de/pubs/pdf/6282.pdf` | Nature article 원문 PDF text 확인용 |
| Dryad dataset | DOI `10.5061/dryad.73n5tb363` | archive manifest, data file layout, license, compressed source provenance |

Nature 원문 제목은 **A Real-World Energy Management Dataset from a Smart Company Building for Optimization and Machine Learning**이다. 대상은 Honda R&D Europe Offenbach facility의 2018-2023 실측 데이터이며, offices, workshops, server rooms, vehicle emissions lab, PV system, CHP, heating/ventilation/air conditioning, central cooling 설비를 포함한다.

원문은 72 energy meters, 9 heat meters, weather station을 설명한다. 현재 CMS active registry는 81 URN으로 관리한다. 두 count는 provenance가 다르므로 문서와 ontology에서 혼합하지 않는다.

## 3. Tier 1: active CMS source

| Source | 역할 |
|---|---|
| `README.md` | CMS active architecture, dataset summary, runtime convention |
| `docs/specs/data_contract.md` | source/candidate/QA/canonical/reference boundary |
| `docs/specs/database_schema.md` | PostgreSQL schema layer와 controlled promotion boundary |
| `docs/specs/meter_metadata.md` | 81 meter classification, role, redundancy, source metadata |
| `docs/specs/meter_measurement_cadence_policy.md` | native cadence와 expected/coverage policy |
| `docs/specs/ontology_schema.md` | ontology class/property/source coverage 기준 |
| `docs/ontology/ems.ttl`, `ems_shapes.ttl` | legacy namespace를 쓰는 generated RDF/SHACL artifact |
| `src/cms/ontology/ontology.py` | import-safe ontology helper lane |

## 4. Tier 1 보조: HRI-EU GitHub

HRI-EU `MonitoringDatasetAnalysis` repository는 원문 dataset validation code와 issue/meter metadata 보조 source다. 권장 활용 대상은 다음이다.

- `README.md`: validation/reduced dataset/downsampling code provenance
- `meters.yaml`: meter category grouping
- `issue_template.yml`: issue/event metadata schema
- `src/ReadFiles.py`: data file naming/loading convention reference

이 repository는 원문 논문/Dryad보다 우선하지 않는다.

## 5. 원문에서 CMS ontology에 반영할 facts

### Meter / hardware / category

- Tables 2-3은 meter URN, hardware type, description의 primary source다.
- `(E)` 표기는 같은 line을 측정하는 distinct URN 2개가 설치됨을 뜻한다.
- `ZE` meters는 2023년 독일 calibration legislation 대응 설치 맥락이다.
- hardware model vocabulary 후보: `ABB-B24`, `Janitza UMG 96 RM-E`, `Janitza UMG 96 PA MID+`, `Socomec I35/DIRIS I35`, `SensorStar 2C`, `SensorStar 2/2U`, `Lufft WS501-UMB`.

### Measurement dictionary

- Thermal: `P`, `W`, `Tvl`, `Trl`, `Tdiff`, `qv`, `V`.
- Electricity: `f`, `I1/I2/I3`, `U1/U2/U3`, `P1/P2/P3`, `W1/W2/W3`, `PF1/PF2/PF3`, `P`, `Q`, `PF`, `Win`, `Wout`, `WQin`, `WQout`, `W`, `WQ`.
- Weather: `AH`, `Dc`, `Dp`, `H`, `Igc`, `Igm`, `Pa`, `ρ`, `Sc`, `Ta`, `Ua`.

### Data records and cadence

- Full data file pattern: `URN_MEASUREMENT_raw.csv.gz`, `URN_MEASUREMENT_harmonized.csv.gz`, `URN_MEASUREMENT_corrected.csv.gz`, `URN_MEASUREMENT_corrected_resampled_{1min|15min|1h}.csv.gz`.
- Full data file columns: `datetime_utc`, `URN.measurement`.
- Janitza, Socomec, weather station은 change-of-value recording 성격이 있다.
- ABB-B24, SensorStar 계열은 periodic 1min sample resolution 성격이 있다.
- Tixi gateway는 timestamp jitter/drift evidence가 있어 cadence policy와 timestamp QA에 provenance로 연결해야 한다.

### Issue / QA semantics

- Manual issues: main cooling meter failure, powered-off design studio meters, wrong configuration, implausible PV readings, wrong conversion factor, interference near CHP wire 등.
- Automatic issues: zero measurement, leap/lasting leap, gap.
- change-of-value collection 때문에 gap detection은 meter-specific maximum expected time threshold가 필요하다.
- Corrected/resampled products는 CMS에서 `reference.corrected_resampled_*`로만 취급하고 canonical observed truth로 승격하지 않는다.

## 6. `_archive` 선별 기준

### 포함 후보

| Path | 사용 방식 |
|---|---|
| `_archive/.../a_clean_meter_hardware_models_from_paper.csv` | 원문 Tables 2-3 일부 hardware mapping sanity check. 완전 source가 아니므로 Tier 2 보조로만 사용 |
| `_archive/.../docs/specs/데이터베이스_구조.md` | legacy schema idea 확인. active schema source는 아님 |
| `_archive/.../docs/specs/피처_명세.md` | meter set/redundancy/weather exclusion 개념 확인. active `feature_spec.md`가 우선 |

### 제외

- `a_clean_huang2022_benchmark/*`
- `a_clean_autonomous_paper_models/*`
- `a_clean_champion_model/*`
- `scripts/modeling/*`
- LSTM/SVR/XGBoost/Huang2022 benchmark metrics

제외 이유: 사용자 요청의 기준은 원본 Honda R&D Nature dataset descriptor이며, model benchmark나 paper-adjacent experiment가 아니다.

## 7. Vector DB chunk 후보

| chunk_id | Source tier | 내용 |
|---|---|---|
| `paper.abstract_dataset_scope` | Tier 0 | title, DOI, facility, period, raw/processed/issues |
| `paper.facility_assets` | Tier 0 | PV, CHP, HVAC, central cooling, building function |
| `paper.data_acquisition` | Tier 0 | gateways, protocol, InfluxDB, change-of-value vs periodic sampling |
| `paper.tables_2_3_meter_inventory` | Tier 0 | meter URN, hardware, description, `(E)`, `ZE` notes |
| `paper.tables_4_6_measurement_dictionary` | Tier 0 | thermal/electric/weather measurement code dictionary |
| `paper.data_records_file_layout` | Tier 0 | raw/harmonized/corrected/corrected_resampled file naming |
| `paper.issue_detection` | Tier 0 | manual/automatic issues, gap/leap/zero semantics |
| `dryad.dataset_manifest` | Tier 0 | DOI, license, file list, SHA-256 |
| `github.issue_template_and_meters_yaml` | Tier 1 | issue schema와 meter grouping 보조 |
| `cms.active_data_contract` | Tier 1 | source/candidate/QA/canonical/reference boundary |
| `cms.ontology_meter_hardware_provenance` | Tier 1 | 81 meter, hardware model, source table provenance |
| `cms.legacy_benchmark_exclusion` | Tier 2 | benchmark/model artifacts exclusion rule |
