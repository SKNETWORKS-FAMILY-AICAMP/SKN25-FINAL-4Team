# EMS 예측 target 정책 및 A 계열 확장 전략

**갱신일:** 2026-05-19

## 목적

이 문서는 EMS 전력 수요 예측 모델링에서 target 등급, A 계열 champion recipe 선정, 후속 확장 순서, 제외 범위와 주요 리스크를 정리한다.

## 현재 target 범위

현재 target catalog는 다음 범위로 제한되어 있다.

- domain: `electricity`
- role: `consumption`
- measurement: `P`
- redundant endpoint 제외
- production, thermal, weather는 demand target에서 제외

확인된 catalog scope:

```text
[('electricity', 'consumption', 'P')]
production target 포함: False
thermal target 포함: False
weather target 포함: False
```

냉방 관련 target 중 `central_cooling_P`, `local_cooling_P`는 냉방 열량 target이 아니라 냉방 설비의 전기 소비 `P` target이다. PV/CHP 발전량은 target이 아니며, grid import 예측의 외생 feature로만 사용할 수 있다.

## 최종 policy 분포

| final_policy | target_count |
| --- | --- |
| `A_STRICT_BENCHMARK` | 4 |
| `A_VERSIONED_STRICT_BENCHMARK` | 3 |
| `B_VERSIONED_BENCHMARK` | 1 |
| `B_VERSIONED_BENCHMARK_REVIEW` | 4 |
| `C_PERIOD_LIMITED_VERSIONED_TARGET` | 2 |
| `D_DIAGNOSTIC_VERSIONED_AGGREGATE` | 1 |

## A 계열 benchmark

A 계열은 champion model 자체보다 champion recipe를 고르는 기준 집합으로 사용한다. 여기서 recipe는 모델 구조, sequence length, feature set, missing-data policy, scaler policy, outage policy, loss, early stopping 규칙을 포함한다.

A 계열 target:

| target_id | target_name | final_policy |
| --- | --- | --- |
| `T2_building__H1__P` | `building_H1_consumption_P` | `A_STRICT_BENCHMARK` |
| `T2_building__V__P` | `building_V_consumption_P` | `A_STRICT_BENCHMARK` |
| `T1_group__central_cooling__P` | `central_cooling_P` | `A_STRICT_BENCHMARK` |
| `T1_group__emission_lab__P` | `emission_lab_P` | `A_STRICT_BENCHMARK` |
| `T1_group__local_cooling__P` | `local_cooling_P` | `A_VERSIONED_STRICT_BENCHMARK` |
| `T1_group__server_power__P` | `server_power_P` | `A_VERSIONED_STRICT_BENCHMARK` |
| `T1_group__ventilation__P` | `ventilation_P` | `A_VERSIONED_STRICT_BENCHMARK` |

## A에서 빠진 target 처리

### 역할별 / 설비군별

| target_id | target_name | policy | 처리 방향 |
| --- | --- | --- | --- |
| `T1_group__grid_transformer__P` | `grid_transformer_P` | `B_VERSIONED_BENCHMARK` | H2 office transformer 교체 전후 component set을 분리하면 pre/post strict coverage가 높다. 142시간 replacement gap은 exclude/flag 처리한다. |
| `T1_group__design_studio_distribution__P` | `design_studio_distribution_P` | `B_VERSIONED_BENCHMARK_REVIEW` | 2020-06-30 전후 component set이 바뀐다. 두 version 모두 strict coverage가 높으나 target 의미 변화 검토가 필요하다. |
| `T1_group__workshop_test__P` | `workshop_test_P` | `B_VERSIONED_BENCHMARK_REVIEW` | H2.ZE74가 2022-03-18 이후 시작한다. version_id 기록과 전후 target 의미 검토가 필요하다. |
| `T1_group__office_distribution__P` | `office_distribution_P` | `C_PERIOD_LIMITED_VERSIONED_TARGET` | H4.Z50/H4.Z51이 2023-06-23 이후 중단된다. 전기간 benchmark보다 period-limited version target이 적합하다. |

### 건물별

| target_id | target_name | policy | 처리 방향 |
| --- | --- | --- | --- |
| `T2_building__H2__P` | `building_H2_consumption_P` | `B_VERSIONED_BENCHMARK_REVIEW` | H2 transformer replacement와 H2.ZE74 시작이 함께 작용한다. version_id와 replacement gap 처리가 필요하다. |
| `T2_building__H3__P` | `building_H3_consumption_P` | `B_VERSIONED_BENCHMARK_REVIEW` | H3 design studio 계량기들이 2020-06-30 이후 시작한다. version별 strict coverage는 높으나 target 의미 변화 검토가 필요하다. |
| `T2_building__H4__P` | `building_H4_consumption_P` | `C_PERIOD_LIMITED_VERSIONED_TARGET` | H4.Z50/H4.Z51이 2023-06-23 이후 중단된다. 2023-06-23 이전 구간 중심으로 사용한다. |

### 전체 aggregate

| target_id | target_name | policy | 처리 방향 |
| --- | --- | --- | --- |
| `T0_all_consumption_P` | `all_electricity_consumption_P` | `D_DIAGNOSTIC_VERSIONED_AGGREGATE` | 여러 lifecycle event가 누적되어 전기간 단일 target 의미가 흔들린다. 진단용 aggregate 또는 별도 versioned 연구 target으로 둔다. |

## 확장 순서

1. A 계열에서 champion recipe를 선정한다.
2. `grid_transformer_P`는 `grid_demand` 실험군으로 별도 확장한다. H2 transformer replacement gap 142시간은 exclude 또는 flag 처리한다.
3. H2, H3, design studio, workshop target은 version별 target 의미를 문서화한 뒤 확장 실험한다.
4. H4, office distribution은 2023-06-23 이전 중심의 period-limited target으로 실험한다.
5. all consumption은 aggregate 검산과 coverage drift 진단에 우선 사용한다.

## 예상 문제와 통제 기준

| 문제 | 발생 조건 | 통제 기준 |
|---|---|---|
| target 간 성능 비교 불공정 | A/B/C/D target을 한 표에서 단순 비교 | `experiment_group`, `final_policy`, `target_version_id`, 제외 구간을 성능표에 포함 |
| versioned target 의미 변화 | component set이 기간별로 바뀜 | version별 metric과 component set을 별도 보고 |
| period-limited target 평가 왜곡 | H4/office가 2023-06-23 이후 중단 | 전기간 test와 분리하여 period-limited metric 사용 |
| replacement gap 처리에 따른 metric 편향 | H2 transformer 교체 gap 142시간 제외/포함 여부 차이 | clean metric과 flagged metric을 분리 |
| all consumption 해석 혼선 | 여러 lifecycle event가 aggregate에 누적 | 대표 benchmark가 아니라 진단용 aggregate로 제한 |
| 실험 수 증가 | target × version × model × policy 조합 증가 | manifest와 naming rule 강제 |

## 본격 진행 전 판단

A 계열부터 본격적으로 들어가는 방향은 타당하다. 다만 바로 LSTM 재학습으로 들어가기보다 다음 순서를 권장한다.

1. 현재 target 정책 산출물과 본 문서를 먼저 커밋 대상으로 확정한다.
2. A 계열 target dataset builder를 작성하고 schema 검증을 먼저 수행한다.
3. naive / seasonal naive baseline을 먼저 만든다.
4. 이후 LSTM 또는 개선 모델을 적용해 champion recipe를 선정한다.
5. A에서 선정된 recipe를 grid_demand와 versioned_review로 확장한다.

이 순서를 따르면 모델 성능보다 target 정의 차이가 결과를 좌우하는 문제를 줄일 수 있다.
