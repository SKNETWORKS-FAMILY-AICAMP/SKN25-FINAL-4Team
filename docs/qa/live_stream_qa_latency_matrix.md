# CMS Live Stream QA 및 Latency Matrix

**Updated:** 2026-06-01
**Status:** 현재 live/replay QA matrix

## 1. 목적

이 matrix는 candidate preview output을 canonical data와 혼동하지 않도록 live/replay readiness를 평가하는 방법을 정의한다. 실제 live-streaming test target은 다음과 같다:

```text
MongoDB live/raw input
  -> processor reads MongoDB
  -> equal-interval logic and gap/correction decisions
  -> PostgreSQL scratch or candidate outputs at 15min/1h
  -> QA evidence and latency report
```

Canonical write는 일반 live latency path에 포함되지 않는다. Canonical promotion은 QA evidence와 승인 이후에 수행되는 별도의 통제 절차다.

## 2. Evidence levels

| Level | 의미 | Canonical claim 허용 여부 |
|---|---|---|
| local compressed-file dry-run | source files를 DB write 없이 replay한다 | no |
| in-memory unit | adapter 없이 processor logic을 테스트한다 | no |
| mocked adapter integration | DB adapter interface를 mock으로 테스트한다 | no |
| scratch DB integration | 격리된 MongoDB/PostgreSQL scratch object에 write하고 다시 read-back한다 | no |
| candidate serving preview | API/model dry-run을 위한 preview output을 사용할 수 있다 | no |
| controlled promotion | 승인된 candidate를 controlled promotion role이 write한다 | yes |

Report는 readiness를 설명하기 전에 반드시 evidence level을 명시해야 한다.

## 3. Latency metrics

필수 latency metrics는 다음과 같다:

| Metric | 정의 |
|---|---|
| `mongo_to_1min_sec` | MongoDB raw visibility부터 1min processor output까지 걸린 시간 |
| `mongo_to_15min_sec` | MongoDB raw visibility부터 15min output availability까지 걸린 시간 |
| `mongo_to_1h_sec` | MongoDB raw visibility부터 1h output availability까지 걸린 시간 |
| `end_to_end_sec` | 테스트한 lane의 전체 측정 시간 |
| `qa_packet_sec` | QA evidence packet 생성에 걸린 시간 |

Canonical promotion을 테스트하는 경우 이를 `promotion_sec`로 별도 측정하고 live latency가 아니라 `controlled promotion`으로 label해야 한다.

## 4. Test cases

| ID | Scope | 최소 evidence |
|---|---|---|
| TC0 | source inventory 및 count planner | meter/source/measurement mapping, file count, corrected reference count |
| TC1 | in-memory equalization | expected buckets, gap decisions, reject rows |
| TC2 | scratch guard | 금지된 run IDs가 reject되고 target names default-deny가 검증됨 |
| TC3 | mocked adapter integration | 계획된 Mongo/PostgreSQL payloads 및 target validation |
| TC4 | scratch DB integration | MongoDB 및 PostgreSQL에서 실제 격리 write/read-back counts |
| TC5 | candidate serving preview | API/model dry-run이 명시적 label이 있는 candidate 또는 preview output을 read 가능 |
| TC6 | controlled promotion dry-run | approval request 및 promotion role/procedure를 별도로 검증 |

## 5. Pass criteria

live/replay test는 실제로 도달한 evidence level에 대해서만 `tested`라고 부를 수 있다. Local dry-run evidence를 AWS/live DB integration으로 보고해서는 안 된다. scratch DB integration pass에는 object names, row counts, time windows, read-back queries, latency metrics, artifact paths, cleanup commands가 필요하다.

## 6. Output contract

Local outputs는 무시되는 runtime artifacts이며 일반적으로 실행 중에만 `outputs/` 아래에 있어야 한다. 지속 보관되는 narrative reports는 다음 위치에 둔다:

```text
reports/cms_md_reports_20260601/
```

Team sharing을 위해 생성되는 모든 report는 diagram이 필요할 때 `.md`, Mermaid `.mmd` source, rendered SVG diagrams를 포함해야 한다.
