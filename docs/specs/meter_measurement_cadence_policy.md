# Meter / Measurement Cadence Policy

**Updated:** 2026-06-01
**Status:** heterogeneous source intervals에 대한 clarification 제안
**Scope:** harmonized observed input -> equal-interval candidate/canonical measurement facts. 이 문서는 policy/spec artifact일 뿐이며 DB write나 canonical promotion을 승인하지 않는다.

## 1. Repository evidence

현재 활성 docs와 code는 이미 observed-data boundary를 설정하고 있지만, heterogeneous sources에 대한 cadence assumption은 아직 불완전하다:

- `docs/specs/data_contract.md`는 활성 path를 `harmonized observed stream -> equal-interval processor with gap/null, coverage, mask, provenance -> canonical observed 1min/15min/1h facts`로 정의하며, 현재 1min path는 missing buckets를 `NULL` gaps로 emit한다고 설명한다.
- `docs/qa/anomaly_service_data_qa_contract.md`는 harmonized observed truth, imputation/corrected-resampled substitution 금지, coverage fields(`expected_points`, `observed_points`, `gap_points`, `coverage_ratio`)를 요구한다. 이 문서의 aggregate rule은 `15min expected points = 15 for 1min-derived series`라고 명시하는데, 이는 1min-derived series에 대해서만 올바르다.
- `docs/specs/database_schema.md`는 활성 canonical tables를 `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h`로 나열한다. scratch DDL에는 `measurement_5min` table도 있지만, 5min은 현재 활성 canonical table이 아니다.
- `scripts/live/dry_run_live_stream.py`는 이미 source cadence evidence(`min_interval_seconds`, `median_interval_seconds`, `top_interval_seconds`, `native_interval_seconds`)를 발견하고 `harmonized`, `harmonized_15min`, corrected-resampled reference layers를 구분한다.
- `src/cms/data/live_equalization_processor.py`는 현재 in-memory 1min policy를 hard-code한다. 모든 target minute는 `expected_points = 1`이고, gaps는 `observed_points = 0`이며, aggregates는 이러한 1min counts를 합산한다.
- `src/cms/data/live_equalization_plan.py`는 현재 1min에 대해 `measurement_series_count * window_minutes`, derived outputs에 대해 `/5`, `/15`, `/60`으로 counts를 계획한다. 이는 모든 series가 동일한 1min canonical grid를 가진다고 가정한다.

결론: 현재 1min null-bucket 동작은 native 1min 또는 의도적으로 1min-derived된 observed series에는 유효하지만, native cadence가 5min, 15min, 1h, 또는 sub-minute인 meter/measurement series에 보편적으로 적용되어서는 안 된다.

## 2. Required cadence inventory

Equalization 또는 count planning 전에 다음 key로 cadence inventory를 구축한다:

```text
(meter_urn, measurement, source_layer, source_file/run_id)
```

최소 inventory fields:

| Field | 의미 |
|---|---|
| `source_native_interval_seconds` | source timestamps로 입증된 native cadence. 입증되지 않으면 null. |
| `cadence_confidence` | `proven`, `mismatch`, `single_row_or_empty`, 또는 `unknown`. |
| `min_interval_seconds`, `median_interval_seconds`, `top_interval_seconds`, `top_interval_count`, `max_interval_seconds` | stable cadence와 mixed cadence를 구분하기 위해 필요한 evidence. |
| `source_layer` / `lane` | 예: `harmonized`, `harmonized_15min`, `reference.corrected_resampled_*`; reference layers는 audit/comparison 전용으로 유지한다. |
| `target_resolution_policy` | 선택된 canonical/candidate grid와 이유. |
| `aggregation_policy` | 각 target bucket 안의 values에 대한 measurement-family rule. |
| `expected_points_policy` | target bucket당 expected source points를 계산하는 데 사용한 formula 또는 lookup. |
| `cadence_inventory_ref` | inventory artifact 또는 QA evidence row에 대한 stable reference. |

Cadence가 `mismatch` 또는 `unknown`이면 reviewer가 명시적인 cadence policy를 승인할 때까지 해당 series의 canonical promotion을 차단한다.

## 3. Canonical grid selection

Universal 1min grid가 아니라 per-series native cadence를 사용한다.

| Native/source cadence | Preferred observed grid | Expected-points rule | Null/gap semantics |
|---:|---|---|---|
| `< 60s` (예: 1s) | aggregation 이후 `canonical.measurement_1min` | cadence/schedule에 따른 분당 expected source ticks. 예: 1s cadence는 1이 아니라 60. | 정책 기준보다 expected native ticks가 부족할 때만 해당 minute가 gap/low-coverage bucket이다. |
| `60s` | `canonical.measurement_1min` | minute당 expected source point 1개. | 현재 1min null-bucket policy를 적용한다. |
| `> 60s` and `< 900s` (예: 5min) | Candidate/diagnostic native grid; aligned되고 승인되면 `canonical.measurement_15min`으로 aggregate. Non-native minutes를 missing으로 취급하지 않는다. | 5min에서 15min으로 만들 때: 15min bucket당 expected source points 3개. | Missing native buckets는 gaps다. 사이의 1min slots는 구조적으로 기대되지 않으므로 observed gaps로 materialize해서는 안 된다. |
| `900s` | `canonical.measurement_15min` | 15min bucket당 expected source point 1개; 1h aggregate당 4개. | Missing 15min buckets는 15min에서 null/gap rows가 되며, synthetic 1min gaps 14개가 아니다. |
| `3600s` | `canonical.measurement_1h` | 1h bucket당 expected source point 1개. | Missing 1h buckets는 1h에서 null/gap rows가 된다. |
| Other or irregular | 명시적 policy 전에는 canonical promotion 없음. | Policy-specific. | Candidate/QA evidence로만 보존한다. |

중요: `canonical.measurement_5min`은 활성 canonical contract에 포함되어 있지 않다. 기존 5min scratch outputs는 diagnostics 또는 candidate review에 사용할 수 있지만, 5min을 canonical로 만들려면 별도의 검토된 schema/contract 변경이 필요하다.

## 4. Expected/observed/coverage arithmetic

모든 target bucket에 대해 해당 meter/measurement policy의 expected native source points를 기준으로 coverage를 계산한다:

```text
expected_points = count of native source timestamps expected within target bucket
observed_points = count of accepted observed source events assigned to target bucket
gap_points      = max(expected_points - observed_points, 0)
coverage_ratio  = observed_points / expected_points, bounded [0, 1]
```

Derived aggregates(15min, 1h)의 expected/observed/gap counts는 기여하는 child buckets 또는 source events의 native-policy counts를 합산해야 한다. 기여 policy가 실제로 1min-derived인 경우를 제외하고 15min당 15 또는 1h당 60으로 hard-code해서는 안 된다.

Examples:

- Native 1s -> 1min bucket: `expected_points = 60`; observed ticks 54개이면 `coverage_ratio = 0.90`.
- Native 1min -> 15min aggregate: `expected_points = 15`.
- Native 5min -> 15min aggregate: `expected_points = 3`.
- Native 15min -> 15min bucket: `expected_points = 1`; 1h aggregate는 `expected_points = 4`.

## 5. Aggregation rules

Aggregation은 measurement-family를 인지해야 하며 provenance에 기록되어야 한다.

| Measurement family | Default observed aggregation |
|---|---|
| Non-cumulative gauge/instantaneous values | Observed native points의 arithmetic mean; 가능하면 count/min/max를 유지한다. 이는 1min-derived rows에 대한 현재 `mean_non_cumulative` 구현과 일치한다. |
| Cumulative counter / energy register | 명시적인 counter policy(`delta`, `last`, 또는 승인된 rollup)를 사용한다. 기본적으로 counters를 average하지 않는다. |
| Status/quality/code values | `mode`, `worst`, 또는 명시적 priority policy. |
| Unknown family | Metadata가 aggregation policy를 정의할 때까지 candidate 전용. |

Observed canonical/candidate rows에는 interpolation, forward-fill, backfill, corrected-resampled substitution이 허용되지 않는다. 이러한 transformations는 별도 provenance를 가진 reference, mart, 또는 model-input policies에 속한다.

## 6. Gap and null semantics

- null/gap row는 해당 series policy에서 기대되는 source point 또는 bucket이 missing임을 의미한다.
- Non-native finer interval은 gap이 아니다. 예를 들어 native 5min series는 `canonical.measurement_1min`이 존재한다는 이유만으로 유효한 native observations 사이에 네 개의 `gap` 1min rows를 emit해서는 안 된다.
- 구현에서 fine-grained diagnostic grid가 필요하다면 `structural_not_expected` 같은 별도 mask를 사용하거나 해당 rows를 materialize하지 않는다. Structural non-expectation을 data loss gaps와 섞어서는 안 된다.
- Low-coverage buckets는 masks/provenance를 가진 evidence로 읽을 수 있어야 하지만, clean service/anomaly feature lanes는 더 엄격한 meter-specific policy가 없는 한 기존 default인 `coverage_ratio >= 0.80` 같은 coverage thresholds를 적용해야 한다.

## 7. Required provenance fields

Candidate, scratch, canonical rows는 cadence decisions를 재구성하기에 충분한 provenance를 포함해야 한다:

```text
source_native_interval_seconds
source_layer / lane
cadence_confidence
cadence_inventory_ref
cadence_policy_id
target_resolution
aggregation_policy
expected_points_policy
expected_points / observed_points / gap_points / coverage_ratio
mask_code / quality_code / quality_summary
source_event_ids
source_file or source_ref
source_run_id / run_id
evidence_level
promotion_id when promoted
```

## 8. Implementation impact

다음 implementation pass에서는 현재 1min assumptions를 parameterize해야 한다:

1. `src/cms/data/live_equalization_processor.py`: per-series cadence/target policy를 수용한다. 모든 series에 대해 항상 매 minute를 emit하지 말고 native cadence에서 `expected_points`를 계산한다.
2. `src/cms/data/live_equalization_plan.py`: global `measurement_series_count * window_minutes` count planning을 per-series target-grid planning과 native expected-point counts로 대체한다.
3. `docs/qa/anomaly_service_data_qa_contract.md`: `15min expected points = 15`가 1min-derived series에만 적용됨을 명확히 한다. native 5min/15min series는 각각 3/1을 사용한다.
4. `scripts/live/dry_run_live_stream.py`: native cadence evidence를 유지하고 dry-run을 넘어 promotion할 때 cadence inventory artifact를 추가한다.
5. 모든 production DDL/migration: heterogeneous-cadence series를 promotion하기 전에 provenance가 cadence policy fields를 저장할 수 있는지 확인한다.

## 9. Policy decision

현재 1min null-bucket policy를 meter/measurement별로 조정한다. Native 1min 및 의도적으로 1min으로 aggregate되는 sub-minute sources에는 유지한다. Native 5min/15min/1h sources에는 적용하지 않는다. 해당 sources에서는 선택된 canonical grid에서 gaps와 `expected_points`를 계산한다. 이는 잘못된 low-coverage signals를 방지하고, high-frequency sources가 minute당 `expected_points = 1`로 과대 인정되는 것을 막는다.
