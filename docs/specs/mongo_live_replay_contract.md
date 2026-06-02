# CMS Mongo Live/Replay Contract

**Updated:** 2026-06-01
**Status:** 현재 MongoDB live/replay contract

## 1. 목적

MongoDB는 CMS의 recent/live raw buffer이자 replay-read lane이다. Canonical store가 아니며 canonical promotion을 수행하지 않는다. MongoDB collections는 ingestion, cursoring, QA, reject inspection, candidate serving preview를 지원한다.

## 2. Active collection roles

| Collection role | 목적 |
|---|---|
| `measurement_raw` | raw live/replay events 또는 source-preserving row documents |
| `measurement_buffer` | QA review 전 processor-ready candidate buffer |
| `measurement_reject` | reason codes가 포함된 rejected 또는 invalid records |
| `measurement_cursor` | replay/live cursor 및 idempotency state |
| `measurement_read_cache` | API/model dry-run previews를 위한 선택적 read cache |

Scratch tests는 test가 isolated namespace에서 derived adapter behavior를 명시적으로 검증하는 경우를 제외하고 `test_measurement_raw_<test_run_id>`라는 raw-only collections를 사용해야 한다.

## 3. Event shape

최소 raw event fields:

| Field | 의미 |
|---|---|
| `event_id` | idempotent source event key |
| `ts` | source timestamp 또는 bucket timestamp |
| `meter_urn` | source meter identifier |
| `measurement` | measurement code 또는 family |
| `value` | raw 또는 source-preserved value |
| `source_file` | replay 시 source path 또는 archive manifest key |
| `run_id` | live/replay run identifier |
| `ingested_at` | MongoDB ingest time |

Raw staging은 timestamp/value strings를 보존하는 것이 가장 안전한 high-throughput path라면 이를 유지할 수 있다. Test contract가 conversion을 명시적으로 요구하지 않는 한 heavy normalization은 downstream batch/processor layers에 속한다.

## 4. Processor boundary

Processor는 MongoDB를 read하고 harmonized observed equal-interval rules를 적용한 뒤 gap/null, coverage, mask, provenance fields가 있는 candidate 또는 scratch outputs를 write한다. `qa_status=pass`인 record는 QA evidence review 및 promotion request creation 대상이 될 수 있다. 이는 canonical write가 아니다.

올바른 boundary:

```text
MongoDB raw/read cache
  -> processor
  -> candidate output + reject/cursor state
  -> QA evidence
  -> ops.promotion_request
  -> approval + controlled promotion role
  -> canonical PostgreSQL facts
```

## 5. Forbidden shortcuts

활성 architecture에서는 다음을 허용하지 않는다:

- MongoDB가 `canonical.measurement_1min`, `canonical.measurement_15min`, 또는 `canonical.measurement_1h`에 직접 write하는 것.
- `qa_status=pass`를 canonical commit으로 취급하는 것.
- `live`, `prod`, `production`, `canonical` 같은 production-looking scratch run IDs를 사용하는 것.
- `measurement_buffer`를 scratch raw collection으로 재사용하는 것.

## 6. Verification

Contract alignment는 다음 명령으로 확인한다:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
```

`src/cms/contracts/core.py`, `src/cms/contracts/measurement.py`, 또는 이 문서가 변경되면 세 가지를 함께 update한다.
