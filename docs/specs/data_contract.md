# CMS Data Contract

**Updated:** 2026-06-01
**Status:** 현재 활성 data contract

## 1. Contract 범위

이 contract는 CMS source, live/replay, candidate, QA evidence, canonical data를 어떻게 분리하는지 정의한다. 기존 분석 schema 이름과 현재 runtime contract를 명확히 구분한다.

## 2. Data layers

```text
source archive / live input
  -> raw buffer or staging
  -> harmonized observed stream
  -> equal-interval processor with gap/null, coverage, mask, provenance
  -> candidate output
  -> QA evidence
  -> approval and controlled promotion
  -> canonical observed 1min/15min/1h facts
  -> mart / API / report / model read paths
```

### Source archive

Source archive는 압축된 source product와 manifest를 보관한다. 재현 가능한 historical load와 lineage에 사용된다. archive에서 loading할 때는 canonical promotion 전에 staging record와 QA evidence를 생성해야 한다.

### Raw buffer and staging

MongoDB는 recent/live raw buffer 또는 read cache로 사용할 수 있다. PostgreSQL staging은 historical batch load에 사용할 수 있다. 이 layer들은 canonical이 아니다.

### Harmonized observed equal-interval path

Service와 anomaly path는 harmonized observed measurement에서 시작한다. equal-interval processor는 먼저 1min bucket을 방출하고, policy가 aggregation을 허용할 때 derived 15min 및 1h bucket을 생성한다.

1min path에서 missing bucket은 `NULL` observed value를 가진 gap으로 표현된다. row는 coverage, mask, provenance metadata를 포함하므로 downstream consumer가 이를 제외할지, impute할지, reference/mart/model policy로 route할지 결정할 수 있다. Correction과 imputation은 `reference` schema, 계획된 `mart` policy, 또는 model-specific input contract로 미룬다.

Corrected/resampled source product는 `reference.corrected_resampled_*`에 속한다. audit, comparison, historical reproduction 용도로 사용할 수 있다.

### Candidate output

Candidate output은 approval 전 live/replay/batch processing의 결과다. preview, dry-run, QA review에 입력으로 사용할 수 있다. canonical로 설명하면 안 된다.

### QA evidence

QA evidence는 row count, rejected row, gap/null decision, coverage distribution, mask/provenance check, latency, lineage, reconciliation summary를 기록한다. QA pass는 candidate가 promotion request review 대상이 될 수 있음을 의미하며, canonical write가 이미 완료되었다는 뜻은 아니다.

### Canonical facts

Canonical facts는 `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h`에 있는 승인된 observed record다. write에는 controlled promotion role 또는 procedure가 필요하다.

## 3. Identifier contract

| Field | Rule |
|---|---|
| `run_id` | 짧은 snake_case 또는 timestamp가 포함된 lowercase ID; production처럼 보이는 scratch ID 금지 |
| `test_run_id` | 격리된 scratch identifier, `live`, `prod`, `production`, `canonical` 사용 금지 |
| `meter_urn` / `meter_id` | 안정적인 meter/source identifier |
| `measurement` | 승인된 dictionary의 measurement code 또는 family |
| `resolution` | stage에 따라 `1min`, `5min`, `15min`, `1h` 중 하나 |
| `source_file` | archive-relative source path 또는 manifest key |

## 4. Live/replay output contract

Live/replay processor는 다음 항목을 방출할 수 있다.

```text
measurement_raw or measurement_read_cache
measurement_buffer or candidate table/object
measurement_reject
measurement_cursor
QA evidence packet
```

정확한 MongoDB collection 이름은 `docs/specs/mongo_live_replay_contract.md`에 정의되어 있다. PostgreSQL scratch 및 canonical table 이름은 `docs/specs/database_schema.md`와 `src/cms/contracts/measurement.py`에 정의되어 있다.

## 5. Report and artifact contract

활성 human-facing report package는 다음 위치에 둔다.

```text
reports/cms_md_reports_20260601/
```

구체적인 run artifact는 local execution 동안 ignored `outputs/` 아래에 둔다. 지속적으로 보존할 source-verified knowledge는 다시 생성되는 project-root graph/cache folder가 아니라 `/home/viowlet/wiki`에 둔다.

## 6. Verification

이 contract를 변경한 뒤 다음을 실행한다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
```
