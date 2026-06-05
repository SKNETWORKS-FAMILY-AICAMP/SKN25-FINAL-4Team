# QA Contract

**갱신일:** 2026-06-02  
**상태:** 통합 QA 기준  
**범위:** 이 문서는 data QA, anomaly service gate, report/chat evidence, live/replay readiness와 latency 기준을 정의한다.

## 1. 목적

QA의 목적은 observed fact, corrected/reference value, candidate evidence, canonical fact를 혼동하지 않도록 막는 것이다. 모든 QA 결과는 promotion 여부와 관계없이 재검토 가능한 evidence로 남아야 한다.

## 2. 핵심 원칙

1. `canonical` write는 QA pass만으로 실행하지 않는다. 별도 approval과 controlled promotion 절차가 필요하다.
2. `corrected_resampled_*`는 reference/comparison lane이며 observed service truth로 사용하지 않는다.
3. `NULL`은 관측 없음이고 `0`은 실제 관측값일 수 있다. 둘을 대체하지 않는다.
4. `coverage_ratio`는 hard-code하지 않고 `observed_points / expected_points`로 계산한다.
5. `gap_points`라는 기존 DDL 컬럼은 문서와 보고에서 `missing_points`로 해석한다.
6. Live/replay 결과는 evidence level을 붙여야 한다.
7. Peak prediction feature는 `mart` branch로 분리하며, `live.measurement_15min/1h.value` 또는 `canonical.measurement_15min/1h.value`로 승격하지 않는다.

## 3. QA 상태와 severity

| 상태 | 의미 | 기본 처리 |
|---|---|---|
| `pass` | 정책을 만족한다 | candidate 또는 promotion review 가능 |
| `warn` | 값은 보존하지만 검토가 필요하다 | evidence에 남기고 downstream threshold 적용 |
| `block` | canonical promotion 조건을 만족하지 않는다 | staging/candidate에 보존하고 promotion 차단 |
| `quarantine` | source 또는 processing 경계가 불명확하다 | 별도 review 전까지 service truth에서 제외 |

| Severity | 사용 기준 | 처리 |
|---|---|---|
| `critical` | leakage, lineage missing, canonical 오염 위험 | 즉시 block |
| `high` | coverage arithmetic 오류, target grid 오류 | block 또는 quarantine |
| `medium` | suspicious zero, flatline, low coverage | warn 또는 candidate review |
| `low` | 설명/metadata 보강 필요 | evidence note |

## 4. Data QA matrix

| Rule ID | Check | Pass condition | Failure action |
|---|---|---|---|
| `QA-NULL-001` | Periodic missing bucket | expected source point가 없으면 value는 `NULL`, `quality_code = null_observation`, `observed_points = 0`이다. | promotion block |
| `QA-COV-001` | Coverage arithmetic | `coverage_ratio = observed_points / expected_points`, 범위는 `[0, 1]`이다. | arithmetic mismatch block |
| `QA-COV-002` | Aggregate coverage | aggregate row는 child/source의 expected/observed/missing count와 quality summary를 보존한다. | warn 또는 block |
| `QA-ZERO-001` | Observed zero | 실제 관측 `0`은 보존한다. 물리적으로 이상하면 `suspicious_zero_*`를 붙인다. | candidate flag |
| `QA-LEAP-001` | Leap/spike | 큰 delta 또는 robust z-score outlier는 previous/next observed context와 함께 flag한다. | candidate flag |
| `QA-FLAT-001` | Flatline/stale | configured duration 동안 반복되는 동일값을 source semantics와 함께 평가한다. | candidate flag 또는 stale block |
| `QA-LIN-001` | Lineage | 모든 row는 source run ID, source event ID 또는 empty lineage, interval policy, evidence level을 가진다. | lineage missing block |
| `QA-REF-001` | Reference isolation | corrected/reference data는 unlabelled service truth로 사용하지 않는다. | leakage block |
| `QA-API-001` | Side-effect boundary | dry-run/API route는 `writes_allowed = false`와 side-effect-free evidence를 노출한다. | route block |
| `QA-PEAK-001` | Peak/canonical separation | peak feature row는 `mart.peak_feature_15min` 또는 `mart.peak_input_15min`에만 존재하고 canonical promotion 대상이 아니다. | leakage block |

## 5. Coverage와 target grid

Cadence와 expected points 산식은 `docs/specs/measurement_processing_policy.md`를 기준으로 한다.

```text
expected_points = count of native source timestamps expected within target bucket
observed_points = count of accepted observed source events assigned to target bucket
missing_points  = max(expected_points - observed_points, 0)
coverage_ratio  = observed_points / expected_points
```

예시는 다음과 같다.

| Source cadence | Target | expected rule |
|---|---|---|
| native 1min | 1min | `expected_points = 1` |
| native 1min | 15min | `expected_points = 15` |
| native 5min | 15min | `expected_points = 3` |
| native 15min | 15min | `expected_points = 1` |
| native sub-minute | 1min | source schedule 기준. 예: 1s이면 60 |

Native 5min/15min/1h source는 universal 1min grid 때문에 synthetic 1min NULL rows를 만들지 않는다.

## 6. Corrected/reference leakage guard

| 위험 | 차단 기준 |
|---|---|
| corrected value를 observed canonical처럼 사용 | source layer가 `reference.corrected_resampled_*`이면 service truth 사용 금지 |
| interpolation/forward-fill을 observed lane에 무표시 사용 | provenance 없이 promotion block |
| outage/issue correction value를 canonical fact로 승격 | corrected/reference provenance 없으면 block |
| COV state-hold와 corrected fill 혼동 | `state_hold_last`, `source_age_seconds`, source health evidence가 없으면 block |

## 7. Evidence packet schema

Report, chat, QA review, LangGraph review는 최소한 다음 fields를 가진 evidence packet을 사용한다.

```text
evidence_id
run_id
source_layer
source_refs
time_window
row_counts
qa_status
severity
block_reasons
coverage_summary
lineage_summary
artifact_refs
created_at
```

## 8. Report/chat route policy

| Request type | 허용 동작 | 금지 동작 |
|---|---|---|
| quick status | read-only status, artifact link, last run summary | bulk ETL 실행, canonical write |
| QA report | evidence packet 조회, report 생성 | evidence 없는 성공 주장 |
| replay request | job registration, dry-run/staging execution | approval 없는 production promotion |
| Text-to-SQL | read-only query, SQL preview | write/DDL/promotion query |
| approval review | approval request와 recommendation 생성 | LangGraph가 직접 promotion 실행 |

## 9. Live/replay evidence level

| Evidence level | 의미 | 최소 검증 |
|---|---|---|
| `local_dry_run` | 파일 또는 in-memory 처리 검증 | input/output count와 policy label |
| `scratch_guard` | DB write guard와 target name 검증 | forbidden target rejection |
| `scratch_db_integration` | isolated PostgreSQL scratch write + read-back and Kafka envelope fixtures or approved local broker | object name, row count, offset/DLQ decision, cleanup command |
| `aws_staging_replay` | AWS isolated staging/candidate replay | Kafka/event count, PostgreSQL staging count, canonical untouched |
| `production_promotion` | controlled promotion 후 canonical write | approval id, promotion id, rollback/reconcile evidence |

## 10. Latency metrics

Live/replay readiness는 다음 latency를 분리해 측정한다.

```text
source_to_fastapi_sec
fastapi_to_kafka_sec
kafka_to_event_sec
event_to_1min_sec
event_to_queue_sec
one_min_to_15min_sec
one_min_to_1h_sec
one_min_to_peak_feature_sec
peak_feature_to_peak_input_sec
qa_eligibility_sec
promotion_ready_sec
end_to_end_sec
```

Latency report는 window, source count, event count, output row count, hardware/container context, evidence level을 함께 기록한다.

## 11. Acceptance criteria

- QA rule별 pass/warn/block 결과가 재현 가능하다.
- `NULL`과 `0`이 구분된다.
- Coverage arithmetic이 native cadence 기준으로 계산된다.
- Reference/corrected leakage가 service truth로 들어가지 않는다.
- Live/replay 결과는 evidence level과 latency metric을 가진다.
- Canonical write는 approval 없이 발생하지 않는다.
- Peak prediction feature는 mart branch에만 남고 canonical/live observed value와 혼동되지 않는다.
