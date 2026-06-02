# CMS Anomaly Service Data QA Contract

**Updated:** 2026-06-01
**Status:** CMS gap/leap/zero anomaly data contract를 위한 K4 QA 기준

## 1. 범위와 validation standard

이 문서는 CMS anomaly service data path의 최소 Data QA standard를 정의한다. QA contract이며 production DDL, migration, champion model input contract로 사용하지 않는다.

Validated path:

```text
harmonized observed input
  -> observed equal-interval buckets
  -> gap/null, coverage, mask, provenance QA evidence
  -> candidate or canonical observed facts after approval
  -> anomaly service feature/read path
```

Reference-only path:

```text
reference.corrected_resampled_1min
reference.corrected_resampled_15min
reference.corrected_resampled_1h
```

Validation standard:

1. Service/anomaly truth는 corrected/resampled reference data가 아니라 harmonized 기반 observed data에서 시작한다.
2. missing 1min bucket은 observed gap이다. value는 NULL/NaN-equivalent이고, `coverage_ratio = 0`, `observed_points = 0`이며, `gap_mask` 또는 equivalent mask가 gap을 표시한다.
3. Interpolation, forward-fill, backfill, corrected/resampled substitution은 row가 별도 provenance를 가진 계획된 mart/model-input policy에 명시적으로 포함된 경우에만 canonical observed data에서 허용된다.
4. 15min 및 1h aggregate는 `expected_points`, `observed_points`, `gap_points`, `coverage_ratio`, quality summary, source/provenance metadata를 포함해야 한다.
5. Zero, leap/spike, stale/flatline, low-coverage outcome은 candidate 또는 QA signal이다. observed value를 조용히 대체하지 않는다.
6. Candidate, scratch, API dry-run output은 evidence-level label을 포함해야 하며 controlled promotion 또는 production canonical truth라고 주장하면 안 된다.

## 2. 필수 QA checks

| Rule ID | Check | Pass condition | Failure action |
|---|---|---|---|
| QA-GAP-001 | 1min gap/null behavior | 모든 expected 1min bucket이 존재한다. observed input이 없는 bucket은 NULL/NaN value, gap quality, `coverage_ratio = 0`, `observed_points = 0`, `gap_points = 1`, source event가 없음을 나타내는 provenance를 가진다. | canonical promotion을 block하고 gap candidate/evidence로 route한다. |
| QA-GAP-002 | Observed layer imputation 금지 | Observed canonical/candidate row는 interpolation, forward-fill, backfill, corrected/resampled substitution을 사용하지 않는다. | promotion을 block한다. service truth로 사용된 경우 leakage로 classify한다. |
| QA-COV-001 | Coverage arithmetic | `coverage_ratio = observed_points / expected_points`; 값은 `[0, 1]` 범위에 있어야 한다. expected가 observed와 같을 때를 제외하고 hard-coded `1.0`을 사용하지 않는다. | arithmetic mismatch가 있으면 promotion을 block한다. |
| QA-COV-002 | Aggregate coverage | Aggregate row는 expected/observed/gap count와 quality summary를 보존한다. 1min-derived series에서는 15min expected points = 15, 1h expected points = 60이다. Native 5min/15min/1h 또는 sub-minute series는 universal 1min count 대신 `docs/specs/meter_measurement_cadence_policy.md`의 meter/measurement cadence policy를 사용해야 한다. | threshold policy에 따라 warn 또는 fail 처리한다. missingness를 숨기지 않는다. |
| QA-COV-003 | Coverage threshold | production anomaly service read의 기본 minimum은 더 엄격한 meter-specific policy가 없는 한 aggregate bucket에 대해 `coverage_ratio >= 0.80`이다. threshold 미만은 evidence로 읽을 수 있지만 clean service feature는 아니다. | `coverage_fail` 또는 low-coverage candidate로 mark하고 clean feature lane에서 제외한다. |
| QA-ZERO-001 | Zero candidate | Exact zero 또는 near-zero value는 meter/measurement role에서 예상되지 않을 때 candidate다. zero가 운영상 정상인 meter에서는 valid하다. | meter/role context와 함께 candidate로 flag한다. auto-correct하지 않는다. |
| QA-LEAP-001 | Leap/spike candidate | 큰 point-to-point delta 또는 robust z-score outlier는 previous/next observed context, coverage, source event ID와 함께 flag한다. | candidate로 flag한다. observed canonical에서 smooth하거나 replace하지 않는다. |
| QA-FLAT-001 | Stale/flatline candidate | configured duration 동안 반복되는 동일 observed value를 flag한다. 알려진 quantized 또는 low-resolution source는 제외한다. | candidate로 flag하고 원래 observed value를 보존한다. |
| QA-LIN-001 | Lineage and provenance | 모든 row는 source run ID, source event ID 또는 gap에 대한 명시적 empty lineage, interval policy, evidence level, scratch/candidate/canonical status를 가진다. | lineage가 missing이면 promotion을 block한다. |
| QA-REF-001 | Reference isolation | `corrected_resampled_*` data는 reference로 명명하고 저장한다. audit/comparison 용도로만 사용할 수 있으며, unlabelled service truth로 사용하지 않는다. | production service path를 block하고 output을 rename/reclassify한다. |
| QA-API-001 | API/report dry-run boundary | API dry-run endpoint는 `dry_run`, `writes_allowed = false`, SMTP/network/DB side effect 없음, source/evidence label을 노출한다. | side-effect-free behavior가 asserted될 때까지 K3 route contract를 block한다. |

## 3. K1 최소 regression tests와 assertions

K1은 implementation change 전 또는 함께 다음 test behavior를 포함해야 한다.

1. `equalize_to_1min` missing bucket test
   - Input: `00:00`과 `00:02`의 observed event.
   - Assert: bucket `00:01`은 NULL/NaN value, quality `gap` 또는 equivalent, `coverage_ratio = 0`, `expected_points = 1`, `observed_points = 0`, `gap_points = 1`, source event ID 없음 상태를 가진다.
   - Assert: interpolation, backfill, forward-fill 없음.

2. Long-gap non-weather test
   - Input: 5분보다 긴 gap이 있는 non-weather event.
   - Assert: 모든 missing 1min bucket은 gap/null로 유지된다. observed canonical/candidate row에 대해 `forward_fill_long_gap` value를 emit하지 않는다.

3. Aggregate coverage test
   - Input: observed 12개와 gap 3개를 포함한 expected 1min bucket 15개.
   - Assert: 15min aggregate는 `expected_points = 15`, `observed_points = 12`, `gap_points = 3`, `coverage_ratio = 0.8`을 가지며, quality summary에는 gap count가 포함된다.

4. Scratch payload provenance test
   - Assert: 각 scratch row는 coverage와 provenance에 필요한 DDL/common field인 `expected_points`, `observed_points`, `gap_points`, `coverage_ratio`, `gap_mask` 또는 equivalent mask, `source_event_ids`, `lineage_key`, evidence-level/status label을 포함한다.
   - Assert: `coverage_ratio`는 hard-coded `1.0`이 아니라 row data에서 derive된다.

5. Corrected/resampled leakage guard
   - harmonized observed value와 다른 fake `corrected_resampled_*` value를 inject한다.
   - Assert: adapter/processor output은 harmonized observed input에만 기반한다.
   - Assert: corrected/resampled repository method는 service truth path에서 호출되지 않는다.

6. DDL/contract assertion
   - Assert: scratch measurement table DDL은 gap을 위한 NULL value를 허용하고 coverage/provenance field를 포함한다.
   - Assert: guard는 scratch write에 대해 `canonical`, `public`, `qa`, `ops`, production처럼 보이는 run ID를 reject한다.

7. Import-side-effect assertion
   - Assert: K1 module은 DB/network client(`psycopg`, `pymongo`, `sqlalchemy`, `socket`, `requests`, `urllib`)를 import하지 않고 import time에 write를 실행하지 않는다.

Current locator notes for K1 review after implementation:

- `src/cms/data/live_equalization_processor.py`는 이제 observed-only 1min lane을 설명하고 구현한다. missing bucket은 zero observed coverage를 가진 gap/null로 유지된다.
- `src/cms/data/scratch_db_adapter.py`는 hard-coded full coverage 대신 row-derived `expected_points`, `observed_points`, `gap_points`, `coverage_ratio`, `mask_code`, `evidence_level`, `quality_summary`, `source_event_ids`를 emit한다.
- `src/cms/data/scratch_ddl.py`는 scratch measurement/latency/QA table을 위한 coverage, mask, evidence, quality summary, lineage column을 포함한다.
- `src/cms/data/live_equalization_plan.py`는 corrected/resampled reference를 계속 언급할 수 있지만, 해당 reference는 validation/reference comparison input일 뿐 service truth 또는 canonical observed input이 아니다.

## 4. K3 최소 regression tests와 assertions

K3는 endpoint change 전 또는 함께 다음 test behavior를 포함해야 한다.

1. Import-safe fallback
   - Assert: `cms.service.api` import는 FastAPI, SMTP, DB, network library를 require하지 않는다.
   - Assert: FastAPI가 없으면 `create_app()`이 `ApiSkeleton`을 반환하고 route path를 계속 inspect할 수 있다.

2. Latency probe endpoint/function
   - Assert: latency probe route/function은 supplied marker 또는 dry-run marker에서 `mongo_to_1min_sec`, `mongo_to_15min_sec`, `mongo_to_1h_sec`, `end_to_end_sec`, `qa_packet_sec` 같은 measured field를 반환한다.
   - Assert: response에는 `dry_run = true`, `writes_allowed = false`, `side_effects_executed = false`, evidence level이 포함된다.

3. Report/email dry-run endpoint/function
   - Assert: recipient/subject/body validation은 SMTP import 또는 send 없이 수행된다.
   - Assert: invalid recipient 또는 empty subject/body는 validation error를 반환한다.
   - Assert: valid payload는 network side effect 없이 queued/dry-run metadata를 반환한다.

4. Data-source boundary in API contract
   - Assert: `/contracts` 또는 equivalent payload는 production anomaly service가 canonical observed data 또는 계획된 `mart.anomaly_input`을 read하고, `reference.corrected_resampled_*`는 audit/reference only라고 명시한다.

5. Route set assertion
   - Assert: route registry에는 기존 health/contracts/live-replay plan과 K3 latency/report dry-run route가 포함된다.
   - Assert: LangGraph는 명시적으로 요청되지 않는 한 normal chat/API path 밖에 남아 있다.

Current locator notes for K3 review after implementation:

- `src/cms/service/api.py`는 현재 `/health`, `/contracts`, `/live-replay/plan`, `/latency/probe`, `/reports/email/dry-run`을 list한다.
- `/latency/probe`는 API dry-run latency metadata, `side_effects_executed = false`, `writes_allowed = false`, source boundary label을 반환한다.
- `/reports/email/dry-run`은 recipient/subject/body를 validate하고 SMTP/network send 없이 queue metadata를 반환한다.
- `/contracts`는 canonical observed measurements를 service/anomaly source로, `reference.corrected_resampled_*`를 audit/reference-only로 명시적으로 label한다.

## 5. Leakage risk checklist

Reject해야 할 high-risk pattern:

1. `*_corrected_resampled_15min.csv.gz` 또는 `*_corrected_resampled_1h.csv.gz`를 expected service truth로서 `canonical.measurement_15min` 또는 `canonical.measurement_1h`에 직접 map하는 runtime path.
2. corrected/resampled data를 reference/audit label 없이 `canonical`, `truth`, `service_input`, `production_feature`라고 부르는 variable 또는 function name.
3. sparse 또는 gapped input에서 생성된 row 안의 hard-coded full coverage(`coverage_ratio = 1.0`).
4. 별도 mart/model-input policy 없이 observed canonical/candidate row에 emit되는 interpolation/forward-fill/backfill quality.
5. evidence level, dry-run status, side-effect flag를 생략한 API dry-run response.
6. local dry-run/scratch result를 controlled promotion 또는 production integration처럼 다루는 docs 또는 code.

Current repository risk locators after implementation:

- `scripts/live/dry_run_live_stream.py`는 corrected/resampled file을 canonical service truth가 아니라 `reference.corrected_resampled_*`에 map한다.
- `src/cms/contracts/core.py`와 `src/cms/contracts/measurement.py`는 `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h`를 포함한다.
- `MeasurementBucketCandidate.value`는 계속 `float`다. observed gap policy는 현재 in-memory/scratch path에서 NaN을 null-equivalent로 사용한다. 나중에 production DDL이 도입되면 nullable SQL value semantics를 명시적으로 review해야 한다.

## 6. 권장 correction strategy

이 K4 lane에서는 direct data correction을 적용하지 않는다. 권장 implementation strategy는 다음과 같다.

1. gap/null 및 coverage/provenance contract에 대한 test를 먼저 update한다.
2. processor behavior를 interpolation/fill에서 observed bucket + explicit gap/null row로 변경한다.
3. aggregate row와 scratch payload 전반에 coverage/provenance를 전달한다.
4. corrected/resampled reference가 validation/reference artifact 용도로만 사용되도록 rename 또는 relabel한다.
5. champion model input contract가 확정될 때까지 mart/model input을 planned boundary로 유지한다.
6. K1/K3 change가 안정화된 뒤 targeted K1/K3 test와 CMS skeleton verifier를 다시 실행한다.

## 7. Re-check protocol

K1/K3 implementation 후 다음으로 re-check한다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/data/test_live_equalization_processor.py -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/data/test_scratch_db_adapter.py tests/data/test_scratch_ddl.py -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
```

변경 범위 밖의 spot check:

- active runtime docs/code에서 `corrected_resampled`를 search하고, 모든 hit가 service truth가 아니라 reference/audit/comparison으로 label되어 있는지 verify한다.
- `coverage_ratio = 1.0`을 search하고, fully observed input이 있는 test에서만 나타나는지 verify한다.
- `interpolation`, `forward_fill`, `backfill`을 search하고, observed canonical service behavior로 나타나지 않는지 verify한다.

## 8. QA summary

- Scope inspected: 2026-06-01 기준 working tree에서 사용 가능한 현재 CMS docs/specs, K1/K3 test, `src/cms/data/*`, `src/cms/contracts/*`, `src/cms/service/api.py`, live dry-run script.
- Validation standard used: gap/null, coverage, mask/provenance, corrected/resampled reference isolation을 갖춘 harmonized observed equal-interval service path.
- Error patterns originally found by K4: observed equalization의 interpolation/fill, hard-coded coverage, incomplete provenance field, corrected/resampled-to-canonical mapping risk, missing K3 latency/report dry-run route contract.
- Current implementation expectation: code/docs에서 해당 pattern이 fix되었으며 merge 전에 final review와 test gate로 recheck해야 한다.
- Re-checks performed for this K4 document: contract text만 update했다. production DB write, destructive command, data movement는 수행하지 않았다.
- Residual risk: 이 repository change에서는 production DDL/data migration이 실행되지 않는다. canonical에서 reference로 실제 DB table을 이동하려면 별도로 승인된 migration plan이 필요하다.
