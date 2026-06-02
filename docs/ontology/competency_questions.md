# CMS 온톨로지 역량질문

**갱신일:** 2026-06-02
**범위:** CMS ontology helper와 SHACL/validation layer가 답해야 하는 대표 질문. 질문은 계량기 context, feature 생성, anomaly explanation, QA evidence, LLM grounding에 사용한다.

## 1. Meter context

**질문:** `H2.Z64`의 equipment group, building, role, redundant meter, hardware model, sign convention, anomaly priority는 무엇인가?

**검증 경로:**

```python
CMSOntology.from_default().get_meter_context("H2.Z64")
```

**기대 기준:**

```text
group = server_power
building = H2
role = consumption
redundant_with = H2.ZE64
hardware_model = ABB-B24
sign_convention = positive_consumption_negative_quality_candidate
anomaly_priority = 2
```

## 2. Aggregate meter set

**질문:** `server_power` aggregate에서 redundant endpoint를 제외한 primary meter set은 무엇인가?

**검증 경로:**

```python
CMSOntology.from_default().get_feature_meter_set(
    group="server_power",
    domain="electricity",
    role="consumption",
    exclude_redundant=True,
)
```

**기대 기준:** 9개 meter. `H2.Z64`는 포함하고 `H2.ZE64`는 제외한다.

## 3. Production sign convention

**질문:** `V.Z84` 음수 값은 어떤 sign convention으로 해석하는가?

**검증 경로:**

```python
CMSOntology.from_default().get_sign_interpretation("V.Z84")
```

**기대 기준:**

```text
role = production
sign_convention = negative_production_positive_noise_or_reverse_flow
negative_value_interpretation = production_or_outflow
positive_value_interpretation = noise_or_reverse_flow_candidate
```

## 4. Weather exclusion

**질문:** weather meter는 energy aggregate에서 왜 제외되는가?

**기준:** weather meter는 외생 변수 feature이며 energy aggregate row에 포함하지 않는다.

**검증 경로:**

```python
CMSOntology.from_default().get_aggregate_policy(
    group="server_power",
    domain="electricity",
    role="consumption",
)
```

**기대 기준:** `rule_energy_aggregate_excludes_weather`가 feature rule에 포함된다.

## 5. Redundancy anomaly interpretation

**질문:** primary와 redundant가 모두 이상이면 어떤 원인 후보로 분류하는가?

**기준:**

```text
primary 이상, redundant 이상 -> equipment_candidate
primary 또는 redundant 한쪽만 이상 -> meter_candidate
둘 다 정상 -> no_redundancy_anomaly
```

고장 확정 표현은 금지한다. 공개 source에는 BMS state, alarm log, setpoint, run command가 없을 수 있으므로 anomaly explanation은 점검 후보로 제한한다.

## 6. Native cadence and coverage

**질문:** 특정 `(meter_urn, measurement)` series를 1min/15min/1h canonical observed lane에 넣을 때 expected_points는 어떻게 정하는가?

**기준:**

```text
native 1min -> 1min bucket expected_points = 1
native 5min -> 15min bucket expected_points = 3
native 15min -> 15min bucket expected_points = 1
sub-minute -> 1min bucket expected_points = native tick count, e.g. 60 for 1s
unknown/mismatch -> canonical promotion blocked until cadence policy approved
```

**관련 문서:** `docs/specs/meter_measurement_cadence_policy.md`

## 7. Corrected/reference leakage

**질문:** `reference.corrected_resampled_*` row가 `canonical.measurement_*` 또는 service truth로 직접 사용되는 경로가 있는가?

**기준:**

```text
reference.corrected_resampled_* = audit/comparison/reference only
canonical.measurement_* = observed facts + gap/null + coverage + mask/provenance only
interpolation / forward-fill / backfill / corrected substitution -> canonical observed lane forbidden
```

**기대 verdict:** leakage가 있으면 QA review는 `block_contract_violation`을 반환한다.

## 8. Source provenance

**질문:** 특정 meter hardware model이나 source description은 어떤 원천에서 왔는가?

**기준:**

```text
Tier 0: Nature Scientific Data DOI 10.1038/s41597-025-05186-3, Tables 2-3
Tier 0: Dryad DOI 10.5061/dryad.73n5tb363
Tier 1: docs/specs/meter_metadata.md, docs/ontology/cms.ttl
Tier 2: selected archive sanity-check tables only
```

Benchmark/model reports는 ontology source truth가 아니다.

## 9. SQLLM grounding

**질문:** Text-to-SQL LLM이 어떤 schema/table을 조회할 수 있는가?

**기준:**

```text
SELECT-only
allowed tables are documented in docs/specs/database_schema.md and docs/specs/llm_pipeline_contract.md
DDL/DML/promotion/write/delete/grant/revoke forbidden
unknown schema or column -> blocked_unknown_schema
```

## 10. Live replay leakage rule

**질문:** live replay current tick에서 사용할 수 없는 feature 정보는 무엇인가?

**기준:**

```text
current_tick_uses_only_past_data
future_imputation_for_current_tick_forbidden
post_hoc_anomaly_label_as_feature_forbidden
corrected_resampled_reference_as_live_truth_forbidden
```
