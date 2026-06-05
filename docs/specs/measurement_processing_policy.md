# Measurement Processing Policy

**갱신일:** 2026-06-02
**상태:** Vector DB 적재 기준 정책
**범위:** `*_harmonized.csv.gz` observed input과 live sensor event를 Kafka raw event contract, PostgreSQL live/staging/candidate, canonical eligibility로 나누는 기준을 정의한다. 이 문서는 DB write, canonical promotion, DDL 변경을 승인하지 않는다.

## 1. 목적과 경계

이 문서는 observed live/replay lane의 단일 기준 문서다.

```text
harmonized observed input or live sensor event
-> Kafka measurement_raw_v1 sparse raw event contract
-> equal-interval candidate with NULL/state-hold/coverage/provenance
-> promotion eligibility check
-> controlled canonical promotion
```

이 문서가 다루는 범위는 다음과 같다.

| 영역 | 포함 | 제외 |
|---|---|---|
| 원천 | `*_harmonized.csv.gz` observed input | `*_corrected_resampled_*`를 observed truth로 대체하는 작업 |
| Kafka raw event | sparse observed raw event stream | NULL bucket 생성, interpolation, canonical 판단 |
| PostgreSQL staging/candidate | bucketization, NULL/state-hold, coverage, QA, block reason | 승인 없는 canonical write |
| canonical eligibility | canonical eligibility 판정 | production promotion 실행 승인 |
| 문서 정책 | cadence, expected points, measurement family, paper rule | DDL migration 자체 |

기본 원칙은 다음과 같다.

1. 문서와 service 표기는 `gap` 대신 `NULL`, `missing observation`, `missing_points`를 사용한다.
2. 기존 코드/DDL 컬럼 `gap_points`는 구현 정리 전까지 `missing_points`와 같은 의미로 해석한다.
3. `0`은 실제 관측값이면 보존한다. 결측 대체값으로 해석하지 않는다.
4. periodic sample source의 missing bucket은 `NULL`이다.
5. change-of-value source의 no-change interval은 source semantics와 health evidence가 검증되면 `state_hold_last`로 materialize할 수 있다.
6. outage/issue correction용 forward-fill, clone filling, weekly copy, corrected-resampled substitution은 observed canonical fact로 승격하지 않는다.
7. canonical promotion은 별도 승인과 controlled promotion 절차 없이는 실행하지 않는다.

## 2. 확인한 원천

| Source | 확인 내용 |
|---|---|
| Nature Scientific Data DOI `10.1038/s41597-025-05186-3` | data acquisition, issue correction, resampling, Tables 4-6 measurement dictionary |
| Dryad DOI `10.5061/dryad.73n5tb363` | compressed source archive와 실제 `*_harmonized.csv.gz` measurement code |
| `docs/reference/source_inventory.md` | source tier, hardware/acquisition 요약 |
| Archive inventory | `*_harmonized.csv.gz` source에 존재하는 40개 measurement code |
| `docs/specs/data_platform_contract.md` | observed/candidate/canonical/reference boundary |
| `docs/qa/qa_contract.md` | coverage fields와 promotion block QA |
| `src/cms/data/live_equalization_processor.py` | per-series cadence policy 적용 대상 |
| `src/cms/data/live_equalization_plan.py` | per-series target-grid planning 적용 대상 |

논문에서 직접 확인한 핵심 문장은 다음과 같다.

1. Janitza, Socomec, weather station은 measured value가 threshold 이상 변할 때만 기록되는 change-of-value 방식이다.
2. ABB-B24와 SensorStar는 1분 sample resolution의 periodic recording으로 설명된다. 단, 논문 후반의 ABB-B24 `W_in/W_out` 설명에는 change-of-value라고 표현된 부분이 있어, 실제 promotion에서는 meter/series별 cadence evidence를 함께 확인한다.
3. Tixi gateway는 1분마다 buffer를 전송하므로 0-59초 timestamp jitter가 발생할 수 있다.
4. processed data는 1분 등간격으로 resampling된다. 일반적으로 linear interpolation을 사용하되, nearest measurement와의 간격이 5분을 넘으면 non-weather는 last known measurement forward-fill을 사용한다.
5. weather station measurement는 long gap을 forward-fill하지 않고 NaN으로 둔다.
6. 15분 및 1시간 data는 1분 equidistant series에서 downsampling된다. non-cumulative measurement는 mean, cumulative measurement, 즉 energy와 flow volume은 linear interpolation을 사용한다.
7. ABB-B24는 direct `W`/`WQ` 없이 `W_in/WQ_in`과 `W_out/WQ_out`만 있는 경우가 있으며, 처리 후 `W = W_in - W_out`, `WQ = WQ_in - WQ_out`으로 계산한다. PV meter `V.Z84`는 `W = W_out` 특수 규칙이 있다.
8. zero value in a cumulative measurement right after a non-zero value는 issue로 본다.

## 3. Cadence inventory와 source mode

Equalization 또는 count planning 전에 다음 key로 cadence inventory를 구축한다.

```text
(meter_urn, measurement, source_layer, source_file/run_id)
```

최소 inventory fields는 다음과 같다.

| Field | 의미 |
|---|---|
| `source_update_mode` | `periodic_sample`, `change_of_value_state`, `unknown` |
| `cadence_group` | `native_1min`, `native_subminute`, `cov_state`, `native_5min_or_sparse`, `unknown_or_mismatch` |
| `source_native_interval_seconds` | source timestamps로 입증된 native cadence. 입증되지 않으면 null. |
| `cadence_confidence` | `proven`, `mismatch`, `single_row_or_empty`, 또는 `unknown`. |
| `min_interval_seconds`, `median_interval_seconds`, `top_interval_seconds`, `top_interval_count`, `max_interval_seconds` | stable cadence와 mixed cadence를 구분하기 위한 evidence. |
| `source_layer` / `lane` | 예: `harmonized`, `reference.corrected_resampled_*`; reference layers는 audit/comparison 전용으로 유지한다. |
| `target_resolution_policy` | 선택된 canonical/candidate grid와 이유. |
| `aggregation_policy` | 각 target bucket 안의 values에 대한 measurement-family rule. |
| `expected_points_policy` | target bucket당 expected source points를 계산하는 formula 또는 lookup. |
| `cadence_inventory_ref` | inventory artifact 또는 QA evidence row에 대한 stable reference. |

Cadence가 `mismatch` 또는 `unknown`이면 reviewer가 명시적인 cadence policy를 승인할 때까지 해당 series의 canonical promotion을 차단한다.

| source_update_mode | cadence_group | 논문/데이터 근거 | 기본 target | bucket 처리 | canonical eligibility |
|---|---|---|---|---|---|
| `periodic_sample` | `native_1min` | ABB-B24, SensorStar 1분 periodic 설명 및 empirical cadence | 1min | bucket 안 대표 관측값, 없으면 `NULL` | 가능 |
| `periodic_sample` | `native_subminute` | archive sample에서 2s/10s/12s/30s 계열 관측 | 1min candidate | 1분 안 mean/nearest 등 policy 필요 | 보류 |
| `change_of_value_state` | `cov_state` | Janitza/Socomec/weather station change-of-value 설명 | 1min candidate | `state_hold_last` + `source_age_seconds`; stale/outage evidence 있으면 NULL/block | source semantics와 health/continuity 검증 시 조건부 가능 |
| `periodic_sample` | `native_5min_or_sparse` | archive sample에서 5-6분/sparse 계열 관측 | 15min candidate | 1분 NULL을 만들지 않고 native target에서 판단 | 보류 |
| `unknown` | `unknown_or_mismatch` | mixed cadence, header-only, insufficient evidence | candidate only | policy 승인 전 canonical 금지 | 금지 |

## 4. Canonical grid와 expected/observed arithmetic

Universal 1min grid가 아니라 per-series native cadence를 사용한다.

| Native/source cadence | Preferred observed grid | Expected-points rule | NULL/missing semantics |
|---:|---|---|---|
| `< 60s` | aggregation 이후 `canonical.measurement_1min` 후보 | cadence/schedule에 따른 분당 expected source ticks. 예: 1s cadence는 1이 아니라 60. | 정책 기준보다 expected native ticks가 부족할 때만 해당 minute가 low-coverage/null bucket이다. |
| `60s` | `canonical.measurement_1min` 후보 | minute당 expected source point 1개. | 1min null-bucket policy를 적용한다. |
| `> 60s` and `< 900s` | candidate/diagnostic native grid; aligned되고 승인되면 `canonical.measurement_15min`으로 aggregate | 5min에서 15min으로 만들 때 expected source points 3개. | Missing native buckets만 missing이다. 사이의 1min slots는 구조적으로 기대되지 않는다. |
| `900s` | `canonical.measurement_15min` 후보 | 15min bucket당 expected source point 1개; 1h aggregate당 4개. | Missing 15min buckets는 15min에서 NULL row가 되며 synthetic 1min NULL 14개가 아니다. |
| `3600s` | `canonical.measurement_1h` 후보 | 1h bucket당 expected source point 1개. | Missing 1h buckets는 1h에서 NULL row가 된다. |
| Other or irregular | 명시적 policy 전에는 canonical promotion 없음 | Policy-specific. | Candidate/QA evidence로만 보존한다. |

`canonical.measurement_5min`은 현재 활성 canonical contract에 포함되어 있지 않다. 기존 5min scratch outputs는 diagnostics 또는 candidate review에 사용할 수 있지만, 5min을 canonical로 만들려면 별도의 검토된 schema/contract 변경이 필요하다.

모든 target bucket에 대해 해당 meter/measurement policy의 expected native source points를 기준으로 coverage를 계산한다.

```text
expected_points = count of native source timestamps expected within target bucket
observed_points = count of accepted observed source events assigned to target bucket
missing_points  = max(expected_points - observed_points, 0)
coverage_ratio  = observed_points / expected_points, bounded [0, 1]
```

DDL 호환을 위해 `gap_points` 컬럼을 사용하는 경우에도 `missing_points`로 해석한다.

Examples:

- Native 1s -> 1min bucket: `expected_points = 60`; observed ticks 54개이면 `coverage_ratio = 0.90`.
- Native 1min -> 15min aggregate: `expected_points = 15`.
- Native 5min -> 15min aggregate: `expected_points = 3`.
- Native 15min -> 15min bucket: `expected_points = 1`; 1h aggregate는 `expected_points = 4`.

Derived aggregates(15min, 1h)의 expected/observed/missing counts는 기여하는 child buckets 또는 source events의 native-policy counts를 합산해야 한다. 기여 policy가 실제로 1min-derived인 경우를 제외하고 15min당 15 또는 1h당 60으로 hard-code해서는 안 된다.

## 5. Bucket/value policy

### 5.1 Periodic source

```text
target bucket = [T, T + grain)
관측값 있음: 실제 observed value 사용
여러 관측값 있음: measurement별 representative policy 사용
관측값 없음: NULL
```

1분 periodic source의 기본값은 다음과 같다.

```text
expected_points = 1
observed_points = 1 or 0
missing_points = expected_points - observed_points
coverage_ratio = observed_points / expected_points
```

### 5.2 Change-of-value source

Change-of-value source는 새 event가 없다는 사실만으로 값이 사라졌다고 볼 수 없다. 직전 event가 현재 state를 의미할 수 있다.

```text
bucket에 새 event 있음: 새 event 사용
bucket에 새 event 없음 + source_age_seconds <= max_state_hold_age: 직전 event value 유지
bucket에 새 event 없음 + source_age_seconds > max_state_hold_age: stale_state 또는 NULL 처리
```

`max_state_hold_age`는 단순 고정 상수가 아니라 source별 health evidence와 논문 처리 규칙으로 정한다. 5분은 논문 resampling에서 `nearest measurement`가 5분을 넘을 때 interpolation 대신 다른 처리를 선택한 기준이므로, online replay에서는 우선 stale 후보 threshold로 사용한다. 최종 canonical에서는 `no-change interval`과 `outage/issue gap`을 먼저 구분한다.

| source type | state-hold canonical rule | stale/outage 처리 |
|---|---|---|
| weather | change-of-value source로 확인되고 long missing/outage가 아니면 `state_hold_last` 가능 | 논문 기준 weather long missing은 채우지 않고 `NULL` + `weather_long_missing` |
| non-weather gauge/state | change-of-value source로 확인되고 acquisition health 또는 next-event continuity가 있으면 `state_hold_last` canonical 후보 | outage/issue interval이면 corrected/reference lane으로 분리, provenance 없이 observed canonical 금지 |
| cumulative energy/volume | raw cumulative source value는 candidate 보존; derived/delta policy 승인 후 별도 처리 | zero reset/leap/outage correction은 corrected/reference provenance 필요 |

따라서 `채워지는 값`은 두 종류로 분리한다. source 설계상 change-of-value no-change interval을 `state_hold_last`로 복원하는 것은 계량기 semantics에 맞는 materialization이며, canonical 후보가 될 수 있다. 반면 논문 corrected path의 clone gap filling, weekly copy, outage 이후 long-missing forward-fill은 issue correction/reference 정책이므로 provenance 없이 observed canonical에 그대로 승격하지 않는다.

### 5.3 NULL, 0, structural non-expectation

- NULL row는 해당 series policy에서 기대되는 source point 또는 bucket이 missing임을 의미한다.
- Non-native finer interval은 missing observation이 아니다. 예를 들어 native 5min series는 `canonical.measurement_1min`이 존재한다는 이유만으로 유효한 native observations 사이에 네 개의 1min NULL rows를 emit해서는 안 된다.
- 구현에서 fine-grained diagnostic grid가 필요하다면 `structural_not_expected` 같은 별도 mask를 사용하거나 해당 rows를 materialize하지 않는다.
- 실제 관측 `0`은 유지한다. 물리적으로 이상한 0은 값을 NULL로 바꾸지 않고 QA flag를 붙인다.
- Low-coverage buckets는 masks/provenance를 가진 evidence로 읽을 수 있어야 하지만, clean service/anomaly feature lanes는 더 엄격한 meter-specific policy가 없는 한 `coverage_ratio >= 0.80` 같은 coverage thresholds를 적용해야 한다.

## 6. Aggregation과 measurement family

Aggregation은 measurement-family를 인지해야 하며 provenance에 기록되어야 한다.

| Measurement family | Default observed aggregation |
|---|---|
| Non-cumulative gauge/instantaneous values | Observed native points의 arithmetic mean; 가능하면 count/min/max를 유지한다. |
| Change-of-value state | `state_hold_last` materialization 이후 approved representative policy. `source_age_seconds`와 state-hold 비율을 보존한다. |
| Cumulative counter / energy register | 명시적인 counter policy(`delta`, `last`, 또는 승인된 rollup`)를 사용한다. 기본적으로 counters를 average하지 않는다. |
| Status/quality/code values | `mode`, `worst`, 또는 명시적 priority policy. |
| Circular direction | circular mean 구현 전 aggregate block. |
| Unknown family | Metadata가 aggregation policy를 정의할 때까지 candidate 전용. |

Observed canonical/candidate rows에는 interpolation, backfill, corrected-resampled substitution이 허용되지 않는다. Source-designed COV `state_hold_last`는 interpolation이 아니라 state materialization으로 분리해 기록한다.

## 7. Measurement dictionary와 처리 정책

아래 표는 AWS archive에서 관측된 40개 measurement code 전체를 포함한다. 논문 표기는 `Win`, `Wout`, `WQin`, `WQout`, `AH`, `ϱ`이지만 archive 파일명은 각각 `W_in`, `W_out`, `WQ_in`, `WQ_out`, `Ah`, `rho`를 사용한다. 논문 Table 6의 `Pa` ambient air pressure는 harmonized code inventory에서는 관측되지 않았다.

| measurement | 논문 의미/단위 | family | source update mode | 1min/candidate value policy | 15min/1h policy | missing/0/stale policy | canonical eligibility |
|---|---|---|---|---|---|---|---|
| `U1` | Voltage phase L1, V | electric_voltage_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 range QA | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `U2` | Voltage phase L2, V | electric_voltage_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 range QA | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `U3` | Voltage phase L3, V | electric_voltage_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 range QA | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `I1` | Electric current phase L1, A | electric_current_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `I2` | Electric current phase L2, A | electric_current_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `I3` | Electric current phase L3, A | electric_current_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `P` | electric total power 또는 thermal heating/cooling power, W | power_gauge | meter hardware 기준 | `nearest_in_bucket`; sub-minute는 `mean_in_bucket` candidate | mean | missing은 `NULL`; 0은 정상/idle 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `P1` | Electric power phase L1, W | power_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상/idle 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `P2` | Electric power phase L2, W | power_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상/idle 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `P3` | Electric power phase L3, W | power_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상/idle 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `Q` | Total reactive power, var | reactive_power_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `PF` | Total power factor | power_factor_gauge | meter hardware 기준 | `nearest_in_bucket` | mean with range QA | missing은 `NULL`; 0은 low power 상태와 함께 QA | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `PF1` | Power factor phase L1 | power_factor_gauge | meter hardware 기준 | `nearest_in_bucket` | mean with range QA | missing은 `NULL`; 0은 QA 후보 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `PF2` | Power factor phase L2 | power_factor_gauge | meter hardware 기준 | `nearest_in_bucket` | mean with range QA | missing은 `NULL`; 0은 QA 후보 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `PF3` | Power factor phase L3 | power_factor_gauge | meter hardware 기준 | `nearest_in_bucket` | mean with range QA | missing은 `NULL`; 0은 QA 후보 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `f` | Grid frequency, Hz | frequency_gauge | meter hardware 기준 | `nearest_in_bucket` | mean | missing은 `NULL`; range QA 필수 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `W` | thermal total energy 또는 electric total active energy, kWh | cumulative_energy | meter hardware 기준 | raw/candidate는 observed value 유지; derived 가능성 확인 | 논문: cumulative는 linear interpolation downsampling | non-zero 직후 0은 issue 후보; missing은 policy 필요 | block until cumulative policy applied |
| `W1` | Energy phase L1, kWh | cumulative_energy | Janitza only | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA | block |
| `W2` | Energy phase L2, kWh | cumulative_energy | Janitza only | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA | block |
| `W3` | Energy phase L3, kWh; 논문 Table 5 L3 row는 `W2`로 인쇄되어 있으나 archive에는 `W3` 존재 | cumulative_energy | Janitza only | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA | block |
| `W_in` | Electric energy consumed, kWh | directional_cumulative_energy | ABB-B24 등 | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA; `W` 계산 source | block |
| `W_out` | Electric energy delivered, kWh | directional_cumulative_energy | ABB-B24 등 | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA; `W` 계산 source | block |
| `WQ` | Total reactive energy, paper unit kWh/kvarh context | cumulative_reactive_energy | meter hardware 기준 | raw/candidate 유지; `WQ_in - WQ_out` derived 가능 | linear interpolation downsampling; Janitza/Socomec integer precision issue 주의 | 0 issue QA | block |
| `WQ_in` | Reactive energy consumed, kvarh | directional_cumulative_reactive_energy | ABB-B24 등 | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA; `WQ` 계산 source | block |
| `WQ_out` | Reactive energy delivered, kvarh | directional_cumulative_reactive_energy | ABB-B24 등 | raw/candidate 유지 | linear interpolation downsampling | 0 issue QA; `WQ` 계산 source | block |
| `Tvl` | Flow temperature, °C | thermal_temperature_gauge | SensorStar periodic 가능성 높음 | `nearest_in_bucket` | mean | missing은 `NULL`; 장기 missing은 `long_null_period`; 0은 range QA | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `Trl` | Return temperature, °C | thermal_temperature_gauge | SensorStar periodic 가능성 높음 | `nearest_in_bucket` | mean | missing은 `NULL`; 장기 missing은 `long_null_period`; 0은 range QA | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `Tdiff` | Temperature difference, mK | thermal_temperature_delta_gauge | SensorStar periodic 가능성 높음 | `nearest_in_bucket` | mean | missing은 `NULL`; 0은 정상 가능 | periodic 1min 또는 COV state 조건 충족 시 가능 |
| `qv` | Volume flow, L/h | thermal_flow_rate_gauge | SensorStar periodic 또는 state-like evidence 확인 필요 | periodic이면 `nearest_in_bucket`; state-like이면 `state_hold_last` candidate | non-cumulative이므로 mean, 단 source mode provenance 필요 | 미관측 시 state_hold 가능; outage/issue면 corrected/reference 분리 | source mode 확인 후 조건부 가능 |
| `V` | Cumulated volume, L | cumulative_flow_volume | SensorStar | raw/candidate 유지 | 논문: cumulative flow volume은 linear interpolation downsampling | 0/leap issue QA | block |
| `Ta` | Ambient air temperature, °C | weather_temperature_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate; long missing은 NaN/NULL | long missing/outage는 `NULL`, `weather_long_missing`; 0은 range QA이지 결측 아님 | COV state 조건부 가능; long missing block |
| `H` | Specific enthalpy, kJ/kg | weather_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL` | COV state 조건부 가능; long missing block |
| `Ah` | Absolute humidity, g/m3 | weather_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL` | COV state 조건부 가능; long missing block |
| `Dp` | Dew point, °C | weather_temperature_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL`; 0은 range QA | COV state 조건부 가능; long missing block |
| `Ua` | Relative humidity, % | weather_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL`; 0-100 range QA | COV state 조건부 가능; long missing block |
| `Igc` | Global horizontal irradiance, W/m2 | weather_irradiance_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL`; 야간 0은 정상 가능 | COV state 조건부 가능; long missing block |
| `Igm` | Mean global horizontal irradiance, 10분 moving average, W/m2 | weather_irradiance_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL`; 야간 0은 정상 가능 | COV state 조건부 가능; long missing block |
| `Sc` | Current wind speed, m/s | weather_wind_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL`; 0은 calm wind 가능 | COV state 조건부 가능; long missing block |
| `Dc` | Current wind direction, degree from North | weather_direction_circular | weather change-of-value | 새 event 또는 `state_hold_last` candidate | circular mean 필요, 산술 평균 금지 | long missing/outage는 `NULL`; 0도는 North 방향일 수 있음 | 1min COV state 조건부 가능; aggregate는 circular 구현 전 block |
| `rho` | Actual air density, paper `ϱ`, g/cm3 | weather_gauge | weather change-of-value | 새 event 또는 `state_hold_last` candidate | mean candidate | long missing/outage는 `NULL`; range QA | COV state 조건부 가능; long missing block |
| `Pa` | Ambient air pressure, hPa | weather_pressure_gauge | weather change-of-value | archive에서 현재 미관측; 존재 시 weather 정책 적용 | mean candidate | long missing/outage는 `NULL` | COV state 조건부 가능; long missing block |

## 8. Derived measurement policy

| derived measurement | source | rule | 적용 시점 | canonical |
|---|---|---|---|---|
| `W` | `W_in`, `W_out` | `W = W_in - W_out` | 논문 기준 all processing after resampling 이후 | block |
| `WQ` | `WQ_in`, `WQ_out` | `WQ = WQ_in - WQ_out` | 논문 기준 all processing after resampling 이후 | block |
| `W` for `V.Z84` | `W_out` | `W = W_out` | PV meter special case | block |

Derived measurement는 source observed row와 별도 provenance를 가져야 한다.

## 9. Canonical eligibility

Canonical promotion 후보는 다음 subset으로 제한한다.

```text
A. periodic observed canonical 후보
   source_update_mode = periodic_sample
   native_interval_seconds ~= 60
   cadence_confidence = proven
   measurement family = gauge/non-cumulative
   bucket_value_policy = nearest_in_bucket 또는 approved 1min representative policy
   missing bucket = NULL
   expected_points = 1/15/60 canonical CHECK와 일치

B. change-of-value state canonical 후보
   source_update_mode = change_of_value_state
   source semantics = proven no-change-means-same-state
   no known outage/manual issue/automatic issue interval overlaps the bucket
   state validity = source health evidence, sibling measurement heartbeat, or retrospective next-event continuity confirms no-change interval
   bucket_value_policy = state_hold_last
   provenance = prior source event id + source_age_seconds + state_hold flag
   weather long missing, outage window, issue-correction fill은 제외
```

Canonical 가능 후보는 measurement code만으로 결정하지 않고 `(meter_urn, measurement, hardware/source mode, cadence evidence)`로 결정한다.

Promotion block 대상은 다음과 같다.

```text
sub-minute source without approved aggregation
5min/sparse source without target-grain policy
change-of-value/state source without proven semantics or with outage/issue/long-missing evidence
weather long missing with outage/long-missing evidence
W/WQ/cumulative energy source before cumulative/derived policy implementation
V cumulative flow volume source before cumulative policy implementation
qv flow source until source mode/state policy confirmed
unknown_or_mismatch cadence source
circular direction source Dc until circular aggregation implemented
```

Block row는 staging/candidate에 남기고 다음 fields를 기록한다.

```text
block_reason
source_update_mode
cadence_group
source_native_interval_seconds
cadence_confidence
bucket_value_policy
source_age_seconds
missing_run_length_minutes
stale_state
paper_policy_ref
cadence_policy_id
```

## 10. Staging/candidate processing policy

Staging/candidate는 전체 source를 받는다.

```text
Kafka `measurement_raw_v1`: sparse raw observed event만 publish한다.
PostgreSQL staging: policy/provenance를 가진 bucket 또는 candidate row를 저장한다.
canonical: canonical eligibility subset만 별도 promotion 후보로 분리한다.
```

Staging에서는 다음을 반드시 구분한다.

| 상태 | value | 품질 |
|---|---|---|
| 실제 관측 0 | `0` | `observed`, 필요 시 `suspicious_zero_*` |
| periodic missing | `NULL` | `null_observation` |
| weather long missing | `NULL` | `weather_long_missing` |
| state hold within age | previous value | `state_hold` |
| state hold over age | previous value or `NULL` candidate | `stale_state`, canonical block |
| cumulative zero issue | `0` 유지 | `suspicious_zero_cumulative` |
| structural non-expectation | row 없음 또는 별도 diagnostic | `structural_not_expected` |

## 11. Required provenance fields

Candidate, scratch, canonical rows는 cadence decisions를 재구성하기에 충분한 provenance를 포함해야 한다.

```text
source_update_mode
cadence_group
source_native_interval_seconds
source_layer / lane
cadence_confidence
cadence_inventory_ref
cadence_policy_id
target_resolution
aggregation_policy
expected_points_policy
expected_points / observed_points / missing_points / coverage_ratio
mask_code / quality_code / quality_summary
source_event_ids
source_file or source_ref
source_run_id / run_id
evidence_level
promotion_id when promoted
```

현재 DDL 호환을 위해 `gap_points` 컬럼을 읽고 쓰는 경우에도 문서/보고에서는 `missing_points`로 설명한다.

## 12. Implementation impact

다음 implementation pass에서는 현재 1min assumptions를 parameterize해야 한다.

1. `src/cms/data/live_equalization_processor.py`: per-series cadence/target policy를 수용한다. 모든 series에 대해 항상 매 minute를 emit하지 말고 native cadence에서 `expected_points`를 계산한다.
2. `src/cms/data/live_equalization_plan.py`: global `measurement_series_count * window_minutes` count planning을 per-series target-grid planning과 native expected-point counts로 대체한다.
3. `docs/qa/qa_contract.md`: `15min expected points = 15`가 1min-derived series에만 적용됨을 명확히 한다. native 5min/15min series는 각각 3/1을 사용한다.
4. `scripts/live/dry_run_live_stream.py`: native cadence evidence를 유지하고 dry-run을 넘어 promotion할 때 cadence inventory artifact를 추가한다.
5. 모든 production DDL/migration: heterogeneous-cadence series를 promotion하기 전에 provenance가 cadence policy fields를 저장할 수 있는지 확인한다.
6. Runner는 이 문서를 기준으로 `block_reason`, `paper_policy_ref`, `cadence_policy_id`를 남긴다.
7. Canonical runner는 canonical eligibility만 처리한다.
8. 테스트 종료 후 Kafka local fixture/topic metadata와 PostgreSQL staging schema를 정리하고 policy 기준으로 재적재한다. MongoDB debug cache를 별도 승인으로 사용한 경우에만 해당 test collection cleanup을 기록한다.

## 13. Verification criteria

- AWS archive의 40개 measurement code가 모두 policy table에 포함된다.
- 논문 Tables 4-6의 unit/description과 archive code 차이가 기록된다.
- 0과 `NULL`이 구분된다.
- Periodic 1min source의 missing bucket은 `NULL`로 남는다.
- Native 5min/15min/1h source는 non-native 1min NULL rows를 만들지 않는다.
- Sub-minute source의 `expected_points`는 1이 아니라 native cadence 기준으로 계산된다.
- Change-of-value source는 `state_hold`, `source_age_seconds`, source health/continuity evidence 없이는 canonical에 들어가지 않는다.
- Weather no-change interval은 COV state로 materialize할 수 있지만, weather long missing/outage는 논문 기준에 따라 `NULL`로 남는다.
- Flow/cumulative 계열은 논문 기준 downsampling policy가 명시되어 있다. `qv`는 source mode 확인 후 조건부 후보, `V`와 W/WQ cumulative 계열은 cumulative/derived policy 구현 전 canonical promotion이 block된다.
- Staging/candidate에는 block reason과 policy provenance가 남는다.
