# EMS/FEMS 설비계통 이상 후보 실증확인 중간 결과

## 1. 목적

본 실증확인은 EMS 계량 데이터만으로 설비군별 성능 이상 후보와 운영 점검 후보를 어느 정도 판단할 수 있는지 확인하기 위한 작업이다. 판단 단위는 설비 고장 확정으로 두지 않는다. 계량 데이터에서 관찰되는 정상 관계, residual, 공개 이벤트 overlap, 물리 관계 해석 가능성을 기준으로 후보 수준을 분리한다.

## 2. 실행 범위

- 작업 root: `/home/viowlet/Projects/SKN25-FINAL-4Team`
- DB: `221.151.189.54:5432/SKN25`, schema `ems`
- 기준 layer:
  - `ems.cr_measurement_15min`
  - `ems.cr_measurement_1h`
  - `ems.reduced_measurement_15min`
  - `ems.reduced_measurement_1h`
  - `ems.meter_definition`
  - `ems.full_source_file`
- 1차 관계 검증 기준: `ems.reduced_measurement_1h`
- known issue registry: 현재 SKN/EMS 프로젝트 파일 검색에서는 별도 issue registry 파일을 찾지 못했다.

## 3. 데이터 coverage 확인

| relation | rows | distinct_ts | range |
|---|---:|---:|---|
| `ems.cr_measurement_15min` | 272,679,348 | 210,438 | 2017-12-30 23:00:00+00:00 ~ 2024-01-01 00:00:00+00:00 |
| `ems.cr_measurement_1h` | 68,169,983 | 52,611 | 2017-12-30 23:00:00+00:00 ~ 2024-01-01 00:00:00+00:00 |
| `ems.reduced_measurement_15min` | 4,827,566 | 210,437 | 2017-12-30 23:00:00+00:00 ~ 2024-01-01 00:00:00+00:00 |
| `ems.reduced_measurement_1h` | 1,206,894 | 52,610 | 2017-12-30 23:00:00+00:00 ~ 2024-01-01 00:00:00+00:00 |

## 4. 모듈별 데이터 가용성

| 모듈 | core signal | 1차 등급 | 확인된 근거 |
|---|---|---:|---|
| Cooling efficiency | 있음 | A~B | `central_cooling`, `cooling_thermal`, cooling reduced signal, `Ta` |
| CHP operation | 있음 | A~B | `chp`, `chp_heat_generation`, heating/chp reduced signal, `Ta` |
| PV performance | 있음 | A~B | `pv`, `electricity/pv/P`, `Igm` |
| Server cooling mismatch | 부분 있음 | B~C | `server_power`, `local_cooling`, `server_thermal`, `Ta` |
| Ventilation / baseload | 부분 있음 | C | `ventilation`, office/workshop distribution, calendar/weather |
| Heating / boiler | 부분 있음 | C~D | `heat_generation`, `chp_heat_generation`, heating total |
| Transformer / grid boundary | 부분 있음 | C~D | `grid_transformer`, electricity total, PV/CHP context |

## 5. Cooling / CHP / PV 정상 관계 1차 검증

단순 OLS를 사용해 1h reduced signal의 정상 관계 강도를 확인했다. 이 단계의 목적은 설비군별 관계가 데이터에서 재현되는지 확인하는 것이다.

| 모듈 | target | n | 주요 pair corr | test R² | test MAE | 1차 해석 |
|---|---|---:|---:|---:|---:|---|
| Cooling efficiency | `cooling_elec_P` | 51,350 | 0.912 | 0.801 | 3,256.295 | 냉방 열량·외기온 대비 전력 관계가 강하게 확인됨 |
| CHP operation | `chp_elec_P` | 51,350 | -0.992 | 0.983 | 4,180.859 | 전기·열 생산 관계가 매우 강하게 확인됨. 부호는 생산 sign convention 영향으로 해석 |
| PV performance | `pv_P` | 18,607 | -0.811 | 0.701 | 55,171.318 | 일사량·시간대 기반 PV 발전 관계가 확인됨. group별 방향·설치 regime 추가가 필요함 |

## 6. 공개 이벤트 sanity check

이벤트 전후 동일 길이 window의 평균·중앙값 변화를 비교했다. 이 결과는 change signal screening으로 사용한다. 원인 확정 결과로 해석하지 않는다.

| 이벤트 | 관찰 |
|---|---|
| 2019-06 PV group 1/2 commissioning | 이벤트 전 PV row 없음, 이후 PV row 확인. PV availability 시작이 데이터에 드러남 |
| 2020-06 PV group 4–6 commissioning | PV 발전 평균 변화 확인. 일사량·기온 변화가 함께 존재함 |
| 2023-06 heating/CHP modernization | CHP 전기·열, heating total 변화 후보 확인 |
| 2023-09 cooling load scaling issue 후보 | cooling electric/thermal 관계 변화 후보 확인. 온도 변화와 분리 검토 필요 |
| 2020-09 transformer replacement context | grid boundary 해석 시 교체 context 보존 필요 |

## 7. 상위 residual 후보 1차 라벨링

모듈별 residual 상위 30개를 대상으로 자동 사전 라벨을 붙였다. 라벨은 수작업 검토의 시작점이다.

| 모듈 | 후보 수 | event overlap | max abs z | dominant label | 1차 의미 |
|---|---:|---:|---:|---|---|
| Cooling efficiency | 30 | 18 | 67.00 | `regime_or_known_event_context;cooling_under_electricity_or_thermal_scaling_candidate` | 2023-09-19~20 cluster가 강함. data quality 또는 thermal relation 검토 우선 |
| CHP operation | 30 | 4 | 25.75 | `chp_electric_heat_mismatch_candidate` | 전기·열 관계 mismatch 후보가 반복됨. physical relation 검토 우선 |
| PV performance | 30 | 1 | 4.22 | `pv_underperformance_candidate` | 저발전 후보가 다수. z-score 강도는 Cooling/CHP 대비 낮음 |

주요 cluster:

| cluster | 기간(local) | n | max abs z | label | event context |
|---|---|---:|---:|---|---|
| `cooling_efficiency_08` | 2023-09-19 21:00 ~ 2023-09-20 07:00 | 11 | 67.00 | cooling thermal relation 후보 | cooling load scaling issue context |
| `cooling_efficiency_09` | 2023-12-06 13:00 ~ 14:00 | 2 | 46.60 | cooling thermal relation 후보 | 없음 |
| `chp_operation_05` | 2020-09-10 03:00 ~ 04:00 | 2 | 25.75 | CHP 전기·열 mismatch 후보 | transformer replacement context |
| `chp_operation_02` | 2018-01-26 02:00 ~ 07:00 | 6 | 19.09 | CHP heat/electric relation 후보 | 없음 |
| `cooling_efficiency_07` | 2023-08-21 12:00 ~ 20:00 | 9 | 13.61 | cooling over-electricity 후보 | 없음 |

## 8. 중간 판단

### Cooling efficiency

가장 먼저 수작업 검토할 가치가 높다. 정상 관계 강도는 충분하고, residual 강도도 매우 크다. 2023-09-19~20 cluster는 공개 cooling scaling issue context와 가까워 data-quality 또는 thermal scaling 검토가 우선이다. 2023-08-21 cluster는 event overlap이 없어 효율·운영 후보로 별도 검토할 수 있다.

### CHP operation

정상 관계 강도가 가장 높다. residual 후보도 강하게 나타난다. 전기 생산과 열 생산의 부호·비율·운전 상태 해석이 핵심이다. 2018-01-26 cluster와 2023년 여름 cluster는 BMS command와 정비 이력 부재를 전제로 physical relation 후보로만 둔다.

### PV performance

정상 관계는 확인된다. residual 강도는 Cooling/CHP 대비 낮다. 현재 모델은 group별 방향·기울기·설치 regime을 단순하게 처리했기 때문에, 다음 단계에서 PV group/meter 단위로 분리해야 판단력이 올라간다.

## 9. 다음 작업

1. Cooling cluster 3개를 우선 수작업 검토한다.
   - `cooling_efficiency_08`: 2023-09-19~20
   - `cooling_efficiency_09`: 2023-12-06
   - `cooling_efficiency_07`: 2023-08-21
2. CHP cluster 2개를 검토한다.
   - `chp_operation_02`: 2018-01-26
   - `chp_operation_05`: 2020-09-10
3. PV는 group/meter 단위 feature를 추가한 뒤 residual 후보를 다시 뽑는다.
4. known issue registry 원천 위치를 별도로 확보하면 overlap 검사를 재실행한다.

## 10. Cooling / CHP 이상 후보 정량화 결과

### 10.1 기준

- Cooling active 기준: `cooling_thermal_P` positive p50 이상, 값 `29,051.667`
- CHP active 기준: `chp_heat_P` positive p50 또는 `abs(chp_elec_P)` positive p50 이상
- residual 기준: train period residual의 robust z-score
- moderate 후보: `abs robust z >= 4`
- severe 후보: `abs robust z >= 6`
- CHP 물리 기준: 공개 power-to-heat ratio `0.677`, 전기·열 동시성, high heat / low electric, high electric / low heat

### 10.2 Cooling 결과

| 후보 유형 | 후보 시간 | event 수 | active 대비 | max abs z | 기간 |
|---|---:|---:|---:|---:|---|
| moderate relation candidate | 2,336 h | 554 | 9.424% | 221.22 | 2018-01-08 ~ 2023-12-06 |
| severe over-electricity candidate | 790 h | 157 | 3.187% | 45.00 | 2018-05-28 ~ 2023-09-19 |
| severe under-electricity / thermal-scaling candidate | 462 h | 104 | 1.864% | 221.22 | 2018-01-23 ~ 2023-12-06 |
| severe union | 1,252 h | 245 | 5.051% | 221.22 | 2018-01-23 ~ 2023-12-06 |

Cooling에서는 실제 데이터상 이상 후보가 확인된다. 특히 2023-09-19~20 구간은 `cooling_load_scaling_issue` context와 겹치며, active thermal load 조건에서 실제 전력과 기대 전력의 괴리가 매우 크다. 이 구간은 설비 효율 저하 후보로 확정하기 전에 thermal scaling 또는 data-quality 후보로 먼저 검토해야 한다.

별도 event context가 없는 2023-08-21 구간은 severe over-electricity 후보로 나타난다. 이 구간은 냉방 열량 대비 전력 과다 후보이며, 운영·효율 점검 후보로 수작업 검토할 가치가 있다.

### 10.3 CHP 결과

| 후보 유형 | 후보 시간 | event 수 | active 대비 | max abs z | 기간 |
|---|---:|---:|---:|---:|---|
| severe relation residual candidate | 3,806 h | 1,441 | 14.824% | 113.26 | 2018-01-01 ~ 2023-12-24 |
| heat without electricity candidate | 4 h | 1 | 0.016% | 84.51 | 2018-01-26 |
| electricity without heat candidate | 25 h | 14 | 0.097% | 113.26 | 2018-01-23 ~ 2023-10-30 |
| power-to-heat ratio deviation candidate | 59 h | 22 | 0.230% | 24.25 | 2018-01-24 ~ 2023-10-31 |
| strict physical / ratio union | 88 h | 34 | 0.343% | 113.26 | 2018-01-23 ~ 2023-10-31 |

CHP는 회귀 residual 기준으로는 후보가 넓게 잡힌다. 이 값은 운전 regime과 부호 convention 영향을 함께 포함하므로 business-facing count로 바로 쓰기에는 넓다. 더 엄격한 물리 기준을 적용하면 88시간, 34개 event가 설비계통 이상 후보로 남는다.

대표 후보는 2018-01-26 03:00~07:00의 heat without electricity 후보, 2020-09-10 03:00~04:00 및 2023-08-31~09-01의 electricity without heat 후보이다. 이들은 CHP 전기·열 동시성 또는 power-to-heat 관계 검토 대상으로 분류할 수 있다.

### 10.4 현재 답

Cooling과 CHP 모두 실제 데이터에서 설비계통 이상 후보가 확인된다.

- Cooling: active cooling 구간의 5.051%가 severe relation 후보이며, 245개 event로 묶인다.
- CHP: residual 후보는 14.824%로 넓게 잡힌다. 물리 기준까지 통과한 strict 후보는 active CHP 구간의 0.343%, 34개 event이다.

Cooling은 data-quality/thermal scaling 후보와 efficiency 후보를 분리하는 다음 검토가 필요하다. CHP는 residual 전체보다 전기·열 동시성 및 power-to-heat ratio 후보를 중심으로 수작업 검토하는 편이 안전하다.

## 11. 생성 산출물

```text
outputs/tables/equipment_anomaly_validation/00_environment_check.csv
outputs/tables/equipment_anomaly_validation/00_relation_inventory.csv
outputs/tables/equipment_anomaly_validation/00_source_load_balance.csv
outputs/tables/equipment_anomaly_validation/00_table_coverage.csv
outputs/tables/equipment_anomaly_validation/01_meter_group_inventory.csv
outputs/tables/equipment_anomaly_validation/01_meter_signal_inventory.csv
outputs/tables/equipment_anomaly_validation/01_module_availability_matrix.csv
outputs/tables/equipment_anomaly_validation/01_reduced_signal_coverage.csv
outputs/tables/equipment_anomaly_validation/02_relation_input_1h_summary.csv
outputs/tables/equipment_anomaly_validation/03_relation_strength_1h.csv
outputs/tables/equipment_anomaly_validation/03_top_residual_candidates_1h.csv
outputs/tables/equipment_anomaly_validation/04_event_sanity_check_1h.csv
outputs/tables/equipment_anomaly_validation/05_top_residual_review_prelabels_1h.csv
outputs/tables/equipment_anomaly_validation/05_residual_candidate_clusters_1h.csv
outputs/tables/equipment_anomaly_validation/05_review_label_summary_1h.csv
outputs/tables/equipment_anomaly_validation/05_module_review_summary_1h.csv
```
