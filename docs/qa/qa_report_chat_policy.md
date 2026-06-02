# CMS QA / Report / Chat Policy 최소 Contract

## 1. 목적 및 범위

본 문서는 CMS(Facility Energy Management System)의 데이터 QA, 리포트 생성, 채팅 응답 경로를 연결하는 최소 계약(contract)을 정의합니다. 구현체는 본 문서의 필수 항목을 만족해야 하며, 불확실하거나 검증되지 않은 데이터는 모델 입력, 리포트 근거, 사용자 답변에 직접 사용하지 않아야 합니다.

## 2. 핵심 원칙

- 모든 모델 입력 전에는 pre-model QA를 수행합니다.
- QA 실패 데이터는 원본을 보존하되 격리(quarantine)하고, 격리 사유를 추적 가능하게 기록합니다.
- 리포트와 채팅 답변은 사용한 증거(evidence)를 함께 추적할 수 있어야 합니다.
- 빠른 답변과 근거 기반 답변을 구분하며, 비동기 작업 또는 승인 필요 상황을 명시적으로 라우팅합니다.
- 자동화는 데이터 수정, 운영 제어, 비용 확정, 외부 전송을 임의로 수행하지 않습니다.

## 3. Pre-model QA Matrix

| QA 항목 | 목적 | 최소 검사 기준 | 실패 시 처리 | 필수 메타데이터 |
|---|---|---|---|---|
| schema | 입력 필드와 타입의 계약 준수 확인 | 필수 필드 존재, 타입 일치, enum/단위 허용값 준수 | `schema_error`로 quarantine, 모델 입력 제외 | `dataset_id`, `record_id`, `field`, `expected`, `actual` |
| timestamp | 시간 정합성 확인 | 파싱 가능, timezone 명시 또는 기본 timezone 적용 가능, 미래/과거 허용 범위 내 | `timestamp_error`로 quarantine, 보정 불가 시 제외 | `event_time`, `ingested_at`, `timezone`, `allowed_window` |
| value | 값 범위 및 물리적 타당성 확인 | 센서/설비별 min/max, 음수 허용 여부, 단위 변환 가능 여부 | `value_error`로 quarantine 또는 soft flag | `metric`, `value`, `unit`, `valid_range`, `rule_id` |
| duplicate | 중복 레코드 확인 | 동일 key 또는 동일 시간·설비·metric 조합 중복 여부 | canonical 1건만 유지, 나머지는 `duplicate`로 quarantine | `dedupe_key`, `canonical_record_id`, `duplicate_record_ids` |
| coverage | 기간·설비·metric 커버리지 확인 | 요청 범위 대비 최소 커버리지 임계값 충족 | `coverage_gap` flag, 임계 미달 시 리포트/모델 차단 가능 | `requested_window`, `observed_window`, `coverage_ratio`, `missing_slots` |
| late | 지연 도착 데이터 확인 | watermark 이후 도착 여부, 리포트 확정 이후 도착 여부 | `late_arrival`로 표시, 확정 리포트에는 자동 반영 금지 | `watermark`, `arrival_time`, `event_time`, `affected_report_id` |

### 3.1 QA 결과 상태

- `pass`: 모델 입력 및 리포트 근거로 사용 가능합니다.
- `warn`: 사용 가능하나 evidence packet에 경고를 포함해야 합니다.
- `quarantined`: 모델 입력, 리포트 수치, 사용자 답변 근거로 사용할 수 없습니다.
- `blocked`: 요청 자체를 진행하지 않고 사용자 또는 운영자 조치가 필요합니다.

## 4. Quarantine Taxonomy

| 분류 | 코드 | 설명 | 기본 조치 |
|---|---|---|---|
| 스키마 오류 | `schema_error` | 필수 필드 누락, 타입 불일치, 알 수 없는 enum | 입력 제외, 원천 매핑 점검 요청 |
| 시간 오류 | `timestamp_error` | 파싱 불가, timezone 불명, 허용 범위 초과 | 입력 제외, 시간 소스 점검 요청 |
| 값 오류 | `value_error` | 물리적으로 불가능하거나 정책 범위 밖의 값 | 입력 제외 또는 severity에 따라 warn |
| 중복 | `duplicate` | 동일 이벤트/측정값이 여러 번 수집됨 | canonical 외 격리 |
| 커버리지 부족 | `coverage_gap` | 기간, 설비, metric의 데이터 누락 | 신뢰도 하향, 임계 미달 시 차단 |
| 지연 도착 | `late_arrival` | watermark 이후 도착한 데이터 | 재처리 후보로 분리, 확정 결과 자동 변경 금지 |
| 출처 불명 | `unknown_source` | source, tenant, facility 식별 불가 | 입력 제외, 접근권한/수집경로 점검 |
| 권한 불일치 | `auth_scope_mismatch` | 요청자가 접근할 수 없는 데이터 포함 | 즉시 차단 및 보안 로그 필요 |
| 파생값 불일치 | `derived_mismatch` | 집계/파생값과 원천값의 불일치 | 리포트 차단 또는 수동 검토 |

## 5. Severity 및 Blocking 정책

| Severity | 의미 | 예시 | Blocking 여부 | 응답 정책 |
|---|---|---|---|---|
| Severity | Meaning | Example | Blocking default | Action |
|---|---|---|---:|---|
| `fatal` | security/control/destructive risk or unusable data | unauthorized control action, massive timestamp failure | yes | require human approval or quarantine |
| `error` | result-distorting data quality failure | schema mismatch, invalid timestamp/value above threshold | yes | block promote/report until resolved |
| `warning` | limited impact but must be disclosed | partial coverage gap, small late arrival volume | conditional | allow with warning/evidence |
| `info` | informational state | passed QA, metadata note | no | include only if useful |

The current Python contract uses `CheckSeverity = info|warning|error|fatal`; do not introduce separate `S0`-`S4` enum values in runtime payloads. If a presentation layer wants S-level labels, map them outside the core contract.

## 6. Report Evidence Packet Schema

리포트 또는 근거 기반 채팅 답변은 최소한 다음 evidence packet을 생성하거나 참조해야 합니다.

```yaml
evidence_packet:
  packet_id: string
  created_at: ISO-8601 datetime
  request:
    request_id: string
    requester_id: string
    tenant_id: string
    facility_id: string
    time_window:
      start: ISO-8601 datetime
      end: ISO-8601 datetime
    intent: string
  data_sources:
    - source_id: string
      dataset_id: string
      version: string
      ingested_at: ISO-8601 datetime
      watermark: ISO-8601 datetime | null
  qa_summary:
    status: pass | warn | blocked
    checks:
      schema: pass | warn | fail
      timestamp: pass | warn | fail
      value: pass | warn | fail
      duplicate: pass | warn | fail
      coverage: pass | warn | fail
      late: pass | warn | fail
    quarantined_count: integer
    warnings:
      - code: string
        message: string
        severity: info | warning | error | fatal
  metrics:
    - name: string
      value: number | string | null
      unit: string | null
      aggregation: string | null
      source_refs:
        - source_id: string
          record_range: string | null
      confidence: high | medium | low | unavailable
  assumptions:
    - string
  limitations:
    - string
  approvals:
    required: boolean
    reason: string | null
    approved_by: string | null
    approved_at: ISO-8601 datetime | null
  output:
    report_id: string | null
    chat_message_id: string | null
    status: draft | final | blocked | needs_job | approval_required
```

### 6.1 Evidence Packet 규칙

- `packet_id`, `request_id`, `created_at`, `qa_summary.status`는 필수입니다.
- `qa_summary.status=blocked`이면 `output.status`도 `blocked` 또는 `approval_required`여야 합니다.
- `confidence=low` 또는 `unavailable`인 metric은 확정적 표현으로 설명하지 않습니다.
- `assumptions`와 `limitations`는 사용자에게 표시 가능한 문장이어야 합니다.
- 격리된 원본 데이터 자체를 사용자에게 노출하지 않고, 요약된 사유만 제공합니다.

## 7. Chat Route Decision Table

| Route | 사용 조건 | 필요한 QA/Evidence | 사용자 응답 형태 | 금지 사항 |
|---|---|---|---|---|
| `quick_answer` | 일반 설명, 정책 안내, 데이터 조회가 필요 없는 질문 | evidence packet 선택 사항 | 간단한 설명 또는 절차 안내 | 실제 수치처럼 단정 금지 |
| `evidence_answer` | 특정 기간/설비/metric 기반 답변 가능, QA 통과 또는 warn | evidence packet 필수 | 수치, 근거, 한계, 신뢰도 포함 | QA 실패 데이터를 근거로 사용 금지 |
| `needs_job` | 장시간 집계, 재처리, 리포트 생성, 대량 데이터 검증 필요 | job 요청 metadata와 예상 산출물 정의 | 작업 접수/필요 입력/예상 결과 안내 | 즉석 결과를 꾸며내기 금지 |
| `approval_required` | 운영 제어, 외부 제출, 비용 확정, 정책 변경, 권한 상승 필요 | 승인 사유와 승인자 범위 기록 | 승인 필요 사유와 다음 단계 안내 | 승인 없이 실행 또는 확정 금지 |
| `report_shell` | 데이터 부족 또는 QA 차단 상태이나 리포트 구조만 제공 가능 | 차단 사유와 비어 있는 evidence packet | 목차, 필요 데이터, 미충족 조건 제공 | 결론/수치/권고를 확정 형태로 작성 금지 |

### 7.1 Route 선택 우선순위

1. 보안·권한·승인 조건이 있으면 `approval_required`를 우선합니다.
2. QA blocking 조건이 있으면 `report_shell` 또는 차단 응답을 사용합니다.
3. 장시간 처리나 비동기 검증이 필요하면 `needs_job`을 사용합니다.
4. 검증된 근거로 답할 수 있으면 `evidence_answer`를 사용합니다.
5. 데이터가 필요 없는 일반 질문이면 `quick_answer`를 사용합니다.

## 8. Acceptance Criteria

구현 또는 문서 산출물은 다음 기준을 만족해야 합니다.

- pre-model QA matrix가 `schema`, `timestamp`, `value`, `duplicate`, `coverage`, `late` 항목을 모두 포함합니다.
- 각 QA 항목은 실패 시 처리 방식과 추적 metadata를 정의합니다.
- quarantine taxonomy가 격리 코드, 설명, 기본 조치를 포함합니다.
- severity 체계가 blocking 여부와 사용자 응답 정책을 포함합니다.
- report evidence packet schema가 요청, 데이터 출처, QA 요약, metric, 가정, 한계, 승인, 출력 상태를 포함합니다.
- chat route decision table이 `quick_answer`, `evidence_answer`, `needs_job`, `approval_required`, `report_shell`을 모두 포함하고 current shell route로 mapping합니다.
- blocking 조건에서 보안/권한, 핵심 데이터 품질, coverage, late arrival, 승인 필요 행위를 다룹니다.
- forbidden actions가 명시되어 있으며 자동화가 수행하면 안 되는 행위를 구분합니다.
- 문서는 구현 세부 기술에 종속되지 않는 최소 contract 형식으로 작성되어 있습니다.

## 9. Forbidden Actions

다음 행위는 명시적 승인 및 별도 정책 없이 자동 수행해서는 안 됩니다.

- QA를 통과하지 못한 데이터를 모델 입력, 리포트 결론, 사용자 확정 답변의 근거로 사용하는 행위
- 격리된 데이터를 조용히 삭제하거나 원본 없이 덮어쓰는 행위
- timezone, 단위, 누락값을 근거 없이 임의 보정하는 행위
- coverage 부족 또는 late arrival을 숨기고 정상 리포트처럼 제공하는 행위
- confidence가 낮은 metric을 확정적 수치 또는 절감액으로 표현하는 행위
- 사용자 권한 밖의 tenant, facility, sensor, report 정보를 조회하거나 노출하는 행위
- 승인 없이 설비 제어, 정책 변경, 스케줄 변경, 외부 제출, 비용 확정을 수행하는 행위
- evidence packet 없이 근거 기반 리포트 또는 수치 답변을 생성하는 행위
- 실패한 job, 차단된 QA, quarantine 사유를 성공처럼 표시하는 행위
- 사용자에게 내부 원본 로그, 민감 식별자, 보안 관련 세부값을 그대로 노출하는 행위

## 10. 최소 응답 문구 요구사항

QA 경고 또는 차단이 있는 응답은 다음 정보를 포함해야 합니다.

- 사용한 route
- QA 상태(`pass`, `warn`, `blocked` 중 하나)
- 핵심 근거 또는 근거 부족 사유
- 사용자에게 필요한 다음 조치
- 리포트 또는 답변의 한계

예시 문구:

> 요청하신 기간의 전력 사용량 분석은 현재 `coverage_gap`으로 인해 확정 리포트로 제공할 수 없습니다. `report_shell` 형태로 필요한 데이터 항목과 미충족 조건을 먼저 안내드리겠습니다.
