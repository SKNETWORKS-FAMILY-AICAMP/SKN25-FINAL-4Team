# CMS Database Schema

**Updated:** 2026-06-01
**Status:** 현재 활성 database contract

## 1. 목적

이 문서는 현재 CMS database boundary를 정의한다. 기존의 활성 문구는 legacy `ems.*` analysis table을 현재 runtime contract처럼 다루었으며, 이 문서는 그 내용을 대체한다. Legacy `ems.full_measurement`와 `ems.cr_measurement_15min/1h`는 historical source 또는 analysis context로 계속 참조할 수 있지만, 새로운 live/replay, QA, serving, promotion 작업은 아래 CMS contract를 사용한다.

## 2. Database 및 schema layout

활성 PostgreSQL database 이름은 `cms`다. 활성 schema layout은 다음과 같다.

```text
cms
├── archive      # compressed source archive manifests and load lineage
├── staging      # temporary/historical batch staging before QA evidence
├── reference    # source-derived corrected_resampled reference data and lineage
├── canonical    # approved observed equal-interval measurement facts
├── qa           # QA checks, rejects, evidence packets, reconciliation summaries
├── ops          # job runs, report runs, promotion requests, approvals
└── mart         # read-optimized views and derived serving marts
```

Canonical table은 raw buffer나 live scratch space로 사용하지 않는다. 승인된 promotion의 controlled output이다.

## 3. Reference 및 canonical facts

### Reference corrected_resampled data

기존 gap/leap/zero-corrected/resampled product는 `reference` schema 아래에 둔다. 이 product는 audit, comparison, historical reproduction을 위해 external corrected/resampled lineage를 보존한다. production service/anomaly input contract로 사용하지 않는다.

```text
reference.corrected_resampled_1min
reference.corrected_resampled_15min
reference.corrected_resampled_1h
```

Reference row는 correction provenance, source file/run lineage, source-layer label을 유지해야 한다. canonical로 promotion하려면 corrected value가 어떻게 선택되었는지 기록하는 명시적 policy가 필요하다.

### Canonical observed measurement facts

현재 canonical facts는 다음과 같다.

```text
canonical.measurement_1min
canonical.measurement_15min
canonical.measurement_1h
```

Minimum column contract:

| Column | Meaning |
|---|---|
| `ts` | bucket timestamp |
| `meter_id` or `meter_urn` | 안정적인 meter identifier |
| `measurement` | measurement code 또는 family |
| `observed_value` or `value` | 승인된 observed numeric value; NULL은 유효한 gap marker |
| `unit` | 사용 가능한 경우 unit |
| `coverage_ratio` | 0과 1 사이의 bucket coverage |
| `gap_mask` | bucket의 missingness/gap mask를 나타내는 machine-readable 값 |
| `provenance` | source, interval logic, policy decision에 대한 JSON 또는 structured lineage |
| `quality_flag` | QA 이후 quality status |
| `source_run_id` | load/replay/batch run으로 이어지는 lineage |
| `promotion_id` | controlled promotion request identifier |
| `loaded_at` | canonical write timestamp |

이 문서는 활성 prose contract다. Scratch-test DDL은 `src/cms/data/scratch_ddl.py`에서 생성된다. production DDL은 reviewed migration 또는 scope가 명확한 SQL artifact로 도입해야 한다.

## 4. Candidate 및 promotion boundary

Live, replay, historical batch processor는 canonical write 전에 candidate data와 QA evidence를 생성한다. QA pass만으로 canonical facts가 write되지는 않는다. promotion path는 다음과 같다.

```text
candidate output
  -> QA evidence packet
  -> ops.promotion_request
  -> human or approved workflow decision
  -> controlled promotion role/procedure
  -> canonical.measurement_1min / canonical.measurement_15min / canonical.measurement_1h
```

이 boundary는 auditability, rollback, preview data와 approved facts의 분리를 보호한다.

## 5. Mart 및 model-input boundary

`mart` schema는 API, report, dashboard, anomaly, model-input surface를 위한 계획된 read-optimized boundary다. `mart.model_input`과 `mart.anomaly_input`은 champion model input contract가 확정되고 review될 때까지 planning label이다. Service/anomaly feature read는 canonical observed measurement table 또는 mask/provenance column이 포함된 명시적으로 승인된 mart input을 사용해야 한다.

## 6. Scratch 및 test schemas

Scratch DB test는 격리된 이름과 default-deny guard를 사용해야 한다. PostgreSQL scratch target은 다음 exact pattern을 따라야 한다.

```text
cms_scratch_<test_run_id>.measurement_1min
cms_scratch_<test_run_id>.measurement_5min
cms_scratch_<test_run_id>.measurement_15min
cms_scratch_<test_run_id>.measurement_1h
```

`live`, `prod`, `production`, `canonical`처럼 production처럼 보이는 run ID는 prefixed variant를 포함해 scratch write에 사용할 수 없다. MongoDB scratch write는 raw-only `test_measurement_raw_<test_run_id>` collection을 target으로 해야 한다.

## 7. Legacy compatibility note

repository에는 legacy report, ontology namespace, source description 안에 원래 `ems` schema에 대한 reference가 계속 남아 있을 수 있다. 이러한 reference는 `src/ems` 아래에 새 runtime code를 작성하거나 CMS promotion boundary를 우회하라는 instruction이 아니다. 활성 runtime code는 `src/cms` 아래에 둔다.
