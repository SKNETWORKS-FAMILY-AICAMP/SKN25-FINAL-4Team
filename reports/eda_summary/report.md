# EMS EDA 요약

**작성일:** 2026-05-15  
**대상 브랜치:** `dev`  
**분석 범위:** regime 변화, measurement 관계, 계량기 redundancy, 품질 lineage  
**주요 입력:** PostgreSQL/TimescaleDB `ems` schema의 corrected/resampled 계층

---

## 1. 목적

이 문서는 팀 통합 브랜치의 EDA 산출물을 모델링과 발표 자료에 연결하기 위한 요약 문서다. 단순 통계량 나열이 아니라 다음 네 가지 판단을 목적으로 한다.

1. 2017년 말부터 2024년 초까지의 전체 시계열을 하나의 동일 분포로 볼 수 있는지 확인한다.
2. 전기, 열, 기상 measurement 사이에 물리적으로 설명 가능한 관계가 있는지 확인한다.
3. 계량기 metadata의 redundancy pair가 실제 계측값에서도 일관성을 보이는지 확인한다.
4. 현재 DB 상태에서 raw/corrected lineage 분석이 가능한 범위와 한계를 명시한다.

---

## 2. 분석 입력과 전제

### 2.1 사용한 DB relation

| 목적 | relation |
|---|---|
| regime 및 measurement 관계 | `ems.reduced_measurement_1h` |
| meter-level redundancy | `ems.cr_measurement_1h` |
| source/load 품질 lineage | `ems.full_file_load_summary`, `ems.full_file_load_balance`, `ems.full_source_file` |
| measurement coverage | `ems.cr_measurement_1h` |

### 2.2 현재 raw table 상태

`ems.full_measurement`는 relation은 존재하지만 현재 row 수가 0이다.

```text
full_measurement_rows = 0
```

따라서 현 단계에서는 raw 값과 corrected/resampled 값을 직접 대조하는 분석은 수행하지 않았다. 품질 lineage는 load counter, source file balance, corrected/resampled coverage를 기준으로 정리했다.

---

## 3. Regime EDA

### 3.1 Regime 구분

EDA에서는 다음 이벤트 경계를 regime 후보로 사용했다.

| regime | 해석 |
|---|---|
| `R0_initial_operation` | 초기 운영 |
| `R1_chp_logic_update` | CHP 로직 변경 후 |
| `R2_pv_phase1` | PV 1차 설치 후 |
| `R3_covid` | COVID 영향 구간 |
| `R4_pv_phase2` | PV 2차 증설 후 |
| `R5_meter_replacement` | 계량기 교체 이후 |
| `R6_heating_modernization` | 난방 현대화 이후 |

### 3.2 핵심 관찰

전기 전체 P는 초기 운영 구간 평균이 높고, 2020년 이후 구간에서 낮아진다. PV 발전 P는 PV 2차 증설 이후 절대 규모가 크게 증가한다. 난방과 냉방은 regime 평균만으로 판단하기 어렵고, 계절 구간의 영향이 강하게 섞인다.

대표 series의 regime별 평균은 다음과 같다.

| series              | regime                   | 구간             |        평균 |
|:--------------------|:-------------------------|:-----------------|------------:|
| electricity/total/P | R0_initial_operation     | 초기 운영        |  237140     |
| electricity/total/P | R1_chp_logic_update      | CHP 로직 변경 후 |  207018     |
| electricity/total/P | R2_pv_phase1             | PV 1차 설치 후   |  202943     |
| electricity/total/P | R3_covid                 | COVID 영향 구간  |  181553     |
| electricity/total/P | R4_pv_phase2             | PV 2차 증설 후   |  189498     |
| electricity/total/P | R5_meter_replacement     | 계량기 교체 이후 |  132678     |
| electricity/total/P | R6_heating_modernization | 난방 현대화 이후 |  139280     |
| cooling/total/P     | R0_initial_operation     | 초기 운영        |   46160.4   |
| cooling/total/P     | R1_chp_logic_update      | CHP 로직 변경 후 |   34751.3   |
| cooling/total/P     | R2_pv_phase1             | PV 1차 설치 후   |   43632.6   |
| cooling/total/P     | R3_covid                 | COVID 영향 구간  |   23869.7   |
| cooling/total/P     | R4_pv_phase2             | PV 2차 증설 후   |   72835.5   |
| cooling/total/P     | R5_meter_replacement     | 계량기 교체 이후 |   41776     |
| cooling/total/P     | R6_heating_modernization | 난방 현대화 이후 |   42574.9   |
| heating/total/P     | R0_initial_operation     | 초기 운영        |  247575     |
| heating/total/P     | R1_chp_logic_update      | CHP 로직 변경 후 |  274122     |
| heating/total/P     | R2_pv_phase1             | PV 1차 설치 후   |  267317     |
| heating/total/P     | R3_covid                 | COVID 영향 구간  |  208593     |
| heating/total/P     | R4_pv_phase2             | PV 2차 증설 후   |   98851.9   |
| heating/total/P     | R5_meter_replacement     | 계량기 교체 이후 |  275790     |
| heating/total/P     | R6_heating_modernization | 난방 현대화 이후 |  145314     |
| electricity/pv/P    | R2_pv_phase1             | PV 1차 설치 후   |  -11775.9   |
| electricity/pv/P    | R3_covid                 | COVID 영향 구간  |  -23262.6   |
| electricity/pv/P    | R4_pv_phase2             | PV 2차 증설 후   | -104082     |
| electricity/pv/P    | R5_meter_replacement     | 계량기 교체 이후 |  -70741.3   |
| electricity/pv/P    | R6_heating_modernization | 난방 현대화 이후 |  -79077.2   |
| electricity/chp/P   | R0_initial_operation     | 초기 운영        |  -64431.2   |
| electricity/chp/P   | R1_chp_logic_update      | CHP 로직 변경 후 |  -76516     |
| electricity/chp/P   | R2_pv_phase1             | PV 1차 설치 후   |  -74942.1   |
| electricity/chp/P   | R3_covid                 | COVID 영향 구간  |  -72843.2   |
| electricity/chp/P   | R4_pv_phase2             | PV 2차 증설 후   |    -104.179 |
| electricity/chp/P   | R5_meter_replacement     | 계량기 교체 이후 |  -66187.9   |
| electricity/chp/P   | R6_heating_modernization | 난방 현대화 이후 |  -62406.4   |
| weather/weather/Ta  | R0_initial_operation     | 초기 운영        |      11.857 |
| weather/weather/Ta  | R1_chp_logic_update      | CHP 로직 변경 후 |      12.099 |
| weather/weather/Ta  | R2_pv_phase1             | PV 1차 설치 후   |      12.007 |
| weather/weather/Ta  | R3_covid                 | COVID 영향 구간  |      11.929 |
| weather/weather/Ta  | R4_pv_phase2             | PV 2차 증설 후   |      20.329 |
| weather/weather/Ta  | R5_meter_replacement     | 계량기 교체 이후 |      11.301 |
| weather/weather/Ta  | R6_heating_modernization | 난방 현대화 이후 |      15.646 |
| weather/weather/Igm | R0_initial_operation     | 초기 운영        |     130.358 |
| weather/weather/Igm | R1_chp_logic_update      | CHP 로직 변경 후 |     169.571 |
| weather/weather/Igm | R2_pv_phase1             | PV 1차 설치 후   |     103.686 |
| weather/weather/Igm | R3_covid                 | COVID 영향 구간  |     201.698 |
| weather/weather/Igm | R4_pv_phase2             | PV 2차 증설 후   |     211.942 |
| weather/weather/Igm | R5_meter_replacement     | 계량기 교체 이후 |     121.667 |
| weather/weather/Igm | R6_heating_modernization | 난방 현대화 이후 |     138.581 |

### 3.3 해석

1. PV 2차 증설 구간은 데이터에서 뚜렷하게 관찰된다.
2. 전기 전체 부하는 2020년 이후 구조 변화 가능성이 있다.
3. 냉난방 부하는 계절 효과가 크므로 regime 평균만으로 설비 효과를 단정하지 않는다.
4. 모델링에서는 전체 기간을 단일 분포로 가정하기보다 event/regime 또는 calendar feature를 포함하는 편이 안전하다.

---

## 4. Measurement 관계 EDA

### 4.1 핵심 관계

`reduced_measurement_1h`를 일 단위 평균으로 집계한 뒤 주요 series 사이의 Pearson 상관을 계산했다.

| series A            | series B            |   겹치는 일수 |   상관계수 |   mean_a |      mean_b |
|:--------------------|:--------------------|--------------:|-----------:|---------:|------------:|
| electricity/pv/P    | weather/weather/Igm |         1,636 |     -0.776 | -62342.2 |    131.194  |
| electricity/pv/P    | weather/weather/Ta  |         1,636 |     -0.603 | -62342.2 |     12.5558 |
| cooling/total/P     | weather/weather/Ta  |         2,159 |      0.621 |  43117.2 |     12.4008 |
| heating/total/P     | weather/weather/Ta  |         2,159 |     -0.835 | 246854   |     12.4008 |
| electricity/total/P | cooling/total/P     |         2,192 |      0.514 | 169719   |  43034.5    |
| electricity/total/P | heating/total/P     |         2,192 |     -0.25  | 169719   | 245985      |
| electricity/total/P | electricity/pv/P    |         1,648 |      0.181 | 150335   | -62360.5    |
| electricity/chp/P   | heating/chp_heat/P  |         2,192 |     -0.998 | -64465.8 | 106149      |
| cooling/cool_elec/P | cooling/total/P     |         2,192 |      0.938 |  19452.3 |  43034.5    |

### 4.2 해석

1. PV `P`와 일사량 `Igm`은 강한 음의 상관을 보인다. 이는 PV 발전이 음수 power로 표현되는 부호 규약과 일치한다.
2. 난방 total `P`는 외기온 `Ta`와 강한 음의 상관을 보인다. 외기온이 낮을수록 난방 수요가 증가하는 물리 관계와 맞다.
3. 냉방 total `P`는 외기온 `Ta`와 양의 상관을 보인다.
4. CHP 전기 `P`와 CHP 열 `P`는 거의 반대 방향으로 움직인다. 부호 규약과 열·전기 생산 관계를 함께 확인해야 한다.
5. 냉방 전기 `P`와 냉방 total `P`의 상관이 높아 냉방 계통의 전력/열 부하 연결성이 확인된다.

### 4.3 모델링 연결

Measurement 관계는 feature 후보를 바로 확정하기 위한 결과가 아니라, 모델 입력을 구성할 때 다음 구조를 반영해야 한다는 근거다.

| 모델링 항목 | 반영 방향 |
|---|---|
| PV 관련 target 또는 feature | 일사량, 시간대, 계절 feature와 함께 해석 |
| 냉방 부하 | 외기온 및 계절 feature 필수 |
| 난방 부하 | 외기온, regime, 난방 현대화 이벤트 반영 |
| CHP | 전기 P와 열 P의 부호 규약 및 운전 regime 분리 |
| total 전기 부하 | PV/CHP/냉난방 계통을 분리한 설명 변수 검토 |

---

## 5. 계량기 balance/redundancy EDA

### 5.1 기준

Redundancy pair는 `docs/specs/계량기_메타데이터.md`의 12개 pair를 사용했다. DB schema를 변경하지 않고 문서 기준 mapping만 로컬 분석 코드에서 적용했다.

### 5.2 Redundancy pair 요약

상관계수 기준 분포는 다음과 같다.

| 구분 | pair 수 |
|---|---:|
| `corr >= 0.95` | 5 |
| `0.90 <= corr < 0.95` | 3 |
| `corr < 0.90` | 4 |

상관이 낮은 순서의 주요 pair는 다음과 같다.

| primary   | redundant   | 그룹                       |   겹치는 시간 |   상관계수 |   MAE |   동일 부호 비율 |
|:----------|:------------|:---------------------------|--------------:|-----------:|------:|-----------------:|
| H3.Z40    | H3.ZE40     | design_studio_distribution |        15,686 |      0.723 | 544.1 |            0.996 |
| H2.Z65    | H2.ZE65     | server_power               |        15,056 |      0.84  | 648.1 |            1     |
| H3.Z41    | H3.ZE41     | design_studio_distribution |        15,686 |      0.862 | 307.4 |            0.981 |
| H2.Z67    | H2.ZE67     | local_cooling              |        15,585 |      0.892 | 293   |            0.95  |
| H2.Z66    | H2.ZE66     | local_cooling              |        15,585 |      0.908 | 455.6 |            0.993 |

### 5.3 해석

1. 대부분의 redundancy pair는 높은 상관을 보인다.
2. `H3.Z40`~`H3.ZE40`, `H2.Z65`~`H2.ZE65`, `H3.Z41`~`H3.ZE41`은 상대적으로 추가 점검 대상이다.
3. PV, CHP, 오피스 배전 일부 pair는 매우 높은 일관성을 보인다.
4. production 계량기인 PV/CHP는 음수 부호가 정상 발전 방향일 수 있으므로, 부호를 임의로 뒤집지 않고 원 부호로 비교했다.

### 5.4 활용 방향

Redundancy EDA는 이상탐지 결과를 해석할 때 다음 기준으로 활용할 수 있다.

| 상황 | 해석 방향 |
|---|---|
| primary와 redundant가 함께 이상 | 설비 또는 공통 upstream 문제 후보 |
| 한쪽만 이상 | 특정 계량기 또는 source file 품질 문제 후보 |
| 두 계량기 모두 결측 | source file lineage와 적재 counter 확인 |
| 생산 계량기의 부호 차이 | 부호 규약 적용 후 비교 |

---

## 6. Quality lineage EDA

### 6.1 Load summary

| processing_level    | resolution   |   files |    csv_rows |   inserted_rows |   null_value_rows |   conflict_rows |   duplicate_key_rows |
|:--------------------|:-------------|--------:|------------:|----------------:|------------------:|----------------:|---------------------:|
| corrected_resampled | 15min        |   1,603 | 272,679,348 |     272,679,348 |            50,044 |               0 |                    0 |
| corrected_resampled | 1h           |   1,603 |  68,169,983 |      68,169,983 |            12,340 |               0 |                    0 |
| corrected_resampled | 1min         |     275 | 862,382,708 |     862,382,708 |                 0 |               0 |                    0 |

### 6.2 Load balance status

| processing_level    | resolution   | status   |   files |    csv_rows |   inserted_rows |   null_value_rows |   unaccounted_rows |
|:--------------------|:-------------|:---------|--------:|------------:|----------------:|------------------:|-------------------:|
| corrected_resampled | 15min        | loaded   |   1,603 | 272,679,348 |     272,679,348 |            50,044 |                  0 |
| corrected_resampled | 1h           | loaded   |   1,603 |  68,169,983 |      68,169,983 |            12,340 |                  0 |
| corrected_resampled | 1min         | loaded   |     275 | 862,382,708 |     862,382,708 |                 0 |                  0 |

### 6.3 Measurement coverage

`cr_measurement_1h`의 주요 measurement coverage는 다음과 같다.

| measurement   |      rows |   meters | min_ts                    | max_ts                    |
|:--------------|----------:|---------:|:--------------------------|:--------------------------|
| Igm           |    52,608 |        1 | 2017-12-30 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| P             | 3,441,702 |       80 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| Q             | 2,175,635 |       44 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| Ta            |    52,608 |        1 | 2017-12-30 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| Tdiff         |   465,349 |        9 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| Trl           |   465,142 |        9 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| Tvl           |   465,096 |        9 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| V             |   465,052 |        9 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |
| W             | 3,545,288 |       80 | 2018-01-01 00:00:00+00:00 | 2024-01-01 00:00:00+00:00 |
| WQ            | 2,311,970 |       44 | 2018-01-01 00:00:00+00:00 | 2024-01-01 00:00:00+00:00 |
| qv            |   465,059 |        9 | 2017-12-31 23:00:00+00:00 | 2023-12-31 22:00:00+00:00 |

### 6.4 한계

현재 `full_measurement` row 수가 0이므로 다음 분석은 제한된다.

1. raw 값과 corrected/resampled 값의 직접 비교
2. raw 기준 이상치와 corrected 기준 이상치의 차이 분석
3. source correction 전후의 값 변화량 산출
4. raw quality flag 또는 synthetic flag 기반 검증

현 단계에서는 corrected/resampled layer의 load counter와 coverage를 기준으로 품질 상태를 설명해야 한다.

---

## 7. 종합 판단

### 7.1 확인된 사실

1. EMS 시계열은 regime 변화가 존재한다.
2. PV 발전, CHP 운전, 냉난방 부하는 서로 다른 부호 규약과 물리 관계를 가진다.
3. 외기온과 일사량은 냉난방 및 PV 계통 해석에 중요한 외생 변수다.
4. redundancy pair는 대체로 일관성이 있으나 일부 pair는 이상탐지 해석 시 추가 점검 대상이다.
5. raw table이 비어 있어 raw/corrected 직접 비교는 현재 DB 상태에서 불가능하다.

### 7.2 모델링으로 넘길 기준

| 항목 | 권장 기준 |
|---|---|
| 학습 기간 | 전체 기간 단일 분포 가정보다 regime/event feature 포함 |
| target 선정 | total 부하와 설비별 부하를 분리해 검토 |
| 외생 변수 | `Ta`, `Igm`, calendar, hour, season 포함 검토 |
| PV/CHP | 부호 규약 유지, 생산/소비 계통 분리 |
| 냉난방 | 외기온과 계절 효과를 함께 반영 |
| 이상탐지 | redundancy pair와 source lineage를 판정 근거로 병행 |
| 품질 한계 | raw/corrected 비교 불가를 보고서에 명시 |

### 7.3 추가 확인 필요 사항

1. `full_measurement` raw layer를 비워 둔 것이 의도된 구조인지 확인한다.
2. raw/corrected 비교가 필요하면 raw table 또는 원천 parquet/archive 계층 접근 방식을 별도로 확정한다.
3. 낮은 redundancy pair의 차이가 설비 특성인지 계량기 문제인지 source file 단위로 추가 확인한다.
4. Regime 경계는 발표/모델링 목적에 맞게 필요 시 단순화할 수 있다.

---

## 8. 산출물 위치

- `notebooks/overview/02_regime_eda.ipynb`
- `notebooks/overview/03_measurement_relationship_eda.ipynb`
- `notebooks/overview/04_meter_balance_redundancy_eda.ipynb`
- `notebooks/overview/05_quality_lineage_eda.ipynb`
- `outputs/tables/regime_eda/`
- `outputs/tables/measurement_relationship_eda/`
- `outputs/tables/meter_balance_redundancy_eda/`
- `outputs/tables/quality_lineage_eda/`
- `outputs/figures/regime_eda/`
- `outputs/figures/measurement_relationship_eda/`
- `outputs/figures/meter_balance_redundancy_eda/`
- `outputs/figures/quality_lineage_eda/`

---

## 9. 결론

현재 EDA는 단순한 산출물 보강이 아니라 모델링 전제와 발표 논리를 정리하는 역할을 한다. 핵심 결론은 다음과 같다.

1. EMS 데이터는 설비 이벤트와 운영 구간 변화의 영향을 받는다.
2. 냉난방, PV, CHP는 물리 관계와 부호 규약을 분리해서 해석해야 한다.
3. redundancy pair는 이상탐지 결과 검증의 보조 근거로 사용할 수 있다.
4. raw/corrected 비교는 현재 DB 상태에서는 제한되며, 보고서에는 이 한계를 명시해야 한다.
