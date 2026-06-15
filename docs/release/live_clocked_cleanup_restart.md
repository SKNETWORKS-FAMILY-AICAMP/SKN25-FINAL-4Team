# Live Clocked Cleanup and Restart Plan

## 목적

`live_20230101_from_start`로 유입된 smoke/오버런 데이터를 삭제하고, historical live simulation을 `live_20230101_clocked`로 다시 시작한다.

기준은 실제 2026 wall clock이 아니라 source event time 기준의 virtual live clock이다.

```text
source virtual start = 2023-01-01T00:00:00Z
KST 표시 = 2023-01-01T09:00:00+09:00
new run_id = live_20230101_clocked
old run_id = live_20230101_from_start
```

## 진행 원칙

- `live.measurement_policy`는 유지한다.
- source archive는 수정하지 않는다.
- cleanup은 old run footprint만 대상으로 한다.
- cleanup 전 canonical affected rows는 백업한다.
- Kafka old backlog가 재유입되지 않도록 consumer group offset을 latest로 맞춘 뒤 재시작한다.
- 재시작은 `scripts/live/run_live_stream_injector.py`만 사용한다.
- `file-order`는 금지하고 `event-time` merge만 사용한다.
- 계량기 1개 장기 run이면 즉시 중단한다.

## 1. Read-only footprint inventory

실제 DB catalog 기준으로 존재 테이블/컬럼을 먼저 확인한다.

Old footprint 기준:

```text
live.measurement_event.source_event_id LIKE 'live_20230101_from_start:%'
live.measurement_event.event_id LIKE '%live_20230101_from_start:%'
source_event_ids 배열 overlap
provenance/evidence/source_refs JSON contains 'live_20230101_from_start'
affected meter_urn + measurement + bucket_ts/window_ts/base_ts/forecast_origin_ts
```

Inventory 대상:

```text
live.measurement_event
live.measurement_1min
live.measurement_15min
live.measurement_1h
live.bucket_queue
live.promotion_check
qa.live_measurement_issue
live.promotion_run
mart.peak_feature_15min
mart.peak_input_15min if exists
mart.anomaly_feature_1h
mart.pmax_forecast_15min
mart.anomaly_warning_1h
ops.pmax_forecast_inference_log
ops.anomaly_warning_inference_log
ops.pipeline_latency_event
ops.pipeline_metric
ops.fastapi_ingest_metric
qa.model_serving_evidence_packet
qa.pmax_forecast_evaluation
qa.anomaly_warning_evaluation
canonical.measurement_15min
canonical.measurement_1h
```

## 2. Backup gate

삭제 전 affected canonical rows와 주요 mart/qa/ops affected rows를 backup schema에 보관한다.

```text
backup schema = cleanup_backup
backup tag = live_20230101_from_start_<UTC timestamp>
```

백업 후 row count와 delete candidate count가 맞는지 검증한다.

## 3. Cleanup execution

서비스가 모두 멈춘 상태에서만 진행한다.

삭제 순서:

```text
qa/ops/model evidence
model outputs
mart features
canonical affected rows
live.promotion_check
live.bucket_queue
live rollups: 1h, 15min, 1min
live.measurement_event
```

각 단계는 row count를 기록한다. cleanup 후 old footprint count가 0이어야 한다.

## 4. Kafka offset reset

Old run Kafka backlog 재유입을 막는다.

```text
topic = measurement_raw_v1
group = postgres-live-ingest
```

Reset 전후 partition별 `current-offset`, `log-end-offset`, `lag`를 기록한다.

조건:

```text
active member 없음
reset 후 committed offset == log end offset
DLQ offset/count 이상 없음
```

## 5. Virtual clock alignment

Virtual clock은 source event timestamp와 같은 기준으로 둔다.

```text
CMS_REPLAY_VIRTUAL_START_TS=2023-01-01T00:00:00Z
CMS_REPLAY_WALL_START_TS=<restart wall clock timestamp>
CMS_REPLAY_TIME_SCALE=1.0
```

KST 표시로는 `2023-01-01 09:00+09`부터 시작한다.

재시작 전 확인:

```text
first_event_ts
first bucket_ts
replay_virtual_now
claimable bucket count under cap
```

## 6. All-meter event-time dry-run and runtime start

Dry-run 명령은 runtime-post 없이 먼저 실행한다.

```bash
python3 scripts/live/run_live_stream_injector.py \
  --source-root /home/skn25/cms-stream-deploy/data/live_source/harmonized \
  --selection-mode all-meters \
  --merge-mode event-time \
  --replay-clock event-time \
  --time-scale 1.0 \
  --required-meters 81 \
  --start-ts 2023-01-01T00:00:00Z \
  --run-id live_20230101_clocked \
  --max-events 1000
```

Dry-run PASS 조건:

```text
mode=dry_run
source_root_authorized=true
selected_meter_count=81
selected_file_count ~= eligible source series count
selected_measurement_count ~= expected measurement count
merge_strategy != file-order
first_event_ts matches source start
```

Runtime start는 dry-run PASS 후 동일 옵션에 `--runtime-post`를 추가한다. 원본 gzip에서 2023 시작점까지 매번 스캔하지 않기 위해 PC1 source archive와 inventory에서 하루 단위 clocked cache를 만든 뒤 그 cache root를 source-root로 사용한다.

```text
cache source = PC1 original harmonized archive + live_20230101 inventory row slice
cache root = runtime/clocked_cache_YYYYMMDD
cache window = [YYYY-MM-DDT00:00:00Z, next day T00:00:00Z)
```

일일 cache runtime command에는 반드시 `--end-ts <next-day>T00:00:00Z`와 충분한 `--max-events >= cache written_rows`를 넣는다. injector는 시작 시 파일 목록을 한 번만 discover하므로, 다음 날짜는 실행 중 파일 추가가 아니라 다음 day cache build 후 day-by-day restart로 진행한다.

## 7. Stability verification and model loop

재시작 후 즉시 확인한다.

모델은 live stream과 별도 scheduler로 계속 돈다. 기준은 real 2026 시간이 아니라 virtual clock이다.

```text
P-Max:
  input table = mart.peak_feature_15min
  input grain = 15min
  required history = 288 windows = 72 hours
  readiness validation window = latest 96 windows = 24 hours
  forecast horizons = +15, +30, +45, +60 minutes
  startup mode = hybrid_warm_start / reference_backfill allowed until live_observed coverage accumulates

Anomaly:
  input table = mart.anomaly_feature_1h or reference.corrected_resampled_1h warm-start
  input grain = 1h
  required history = 343 hours
  forecast horizons = +1, +2, +3 hours
  startup mode = reference_backfill for cold start, live_observed after feature coverage exists
```

Hybrid model-serving scheduler는 먼저 켜도 된다. Canonical/anomaly-feature worker는 new live stream read-back 이후 켠다.

```text
new run rows > 0
old run rows = 0
new event_ts <= replay_virtual_now + small tolerance
recent distinct meter_urn > 1
recent distinct meter_urn|measurement > 1
selected_meter_count = 81
DLQ = 0
Kafka lag bounded
closed bucket rollups only
canonical promotion only for closed eligible buckets
model/report evidence only for virtual base_ts
```

Downstream 재시작 순서:

```text
producer
postgres-live-ingest consumer
live.bucket_queue worker
canonical promotion worker
anomaly feature worker
hybrid model-serving scheduler
```

각 단계는 read-back 확인 후 다음 단계로 진행한다.

## Fern review status

진행 전 fern 독립 리뷰를 수행한다. REQUEST_CHANGES가 나오면 보완 후 진행한다.
