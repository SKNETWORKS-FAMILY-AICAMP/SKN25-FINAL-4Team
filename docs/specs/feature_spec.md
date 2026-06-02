# CMS Feature Specification

**Updated:** 2026-06-01
**Status:** 현재 feature contract 및 planning boundary

## 1. 범위

이 문서는 현재 CMS architecture에서 feature ownership과 feature data boundary를 설명한다. production feature pipeline이 구현되었다고 주장하지 않는다. 계획된 script나 report path는 active tree에 존재할 때까지 planned로 표시해야 한다.

## 2. Feature source hierarchy

Feature computation은 승인되었거나 명시적으로 표시된 preview source에서 읽어야 한다.

1. production service/anomaly feature에는 `canonical.measurement_1min`, `canonical.measurement_15min`, `canonical.measurement_1h` observed facts를 사용한다.
2. Candidate/serving preview output은 dry-run, QA review, model inference dry-run에만 사용한다.
3. mart/model policy가 승인되고 구현되면 계획된 `mart.anomaly_input` 또는 `mart.model_input`을 사용한다.
4. `reference.corrected_resampled_*`는 audit, comparison, historical reproduction에 사용한다.
5. Legacy `ems.cr_measurement_*` mart는 historical analysis를 reproduction할 때만 사용하며, legacy label을 반드시 붙인다.

Production feature run에는 모든 input에 승인된 source label이 필요하다.
Production anomaly feature는 canonical observed data 또는 승인된 `mart.anomaly_input` contract를 사용한다.

## 3. Feature groups

| Group | Description | Source requirement |
|---|---|---|
| Time features | hour, day, weekday, holiday/calendar flag | timestamp에서 deterministic하게 생성 |
| Meter metadata | building, equipment group, meter role, sign convention | 승인된 metadata table 또는 ontology-derived dictionary |
| Lag/window features | prior value, rolling mean/std, recent delta | production은 canonical observed data 사용; dry-run은 candidate만 사용 |
| QA features | missingness, coverage, gap mask, provenance flag, source quality counter | QA evidence 및 canonical quality/provenance field |
| External/context features | 사용 가능한 경우 weather 또는 policy context | 문서화된 source lineage 필요 |

## 4. Output boundary

Production feature는 canonical promotion 이후 `mart` 또는 명확한 이름의 feature schema 아래 materialize해야 한다. Preview feature는 ignored local output 또는 scratch schema에 쓸 수 있지만, 반드시 evidence level label을 포함해야 한다.

Anomaly service feature는 canonical observed data를 직접 읽거나 계획된 `mart.anomaly_input` view/table을 읽는다. Champion model input은 아직 boundary decision 상태다. model input policy가 확정될 때까지 `mart.model_input`은 planned contract label로만 사용한다.

```text
local dry-run
in-memory unit
mocked adapter integration
scratch DB integration
candidate serving preview
production canonical feature
```

## 5. Planned implementation slots

다음 path는 active implemented file이 실제로 존재하지 않는 한 planning slot이다.

```text
scripts/features/
outputs/tables/feature_baseline/
reports/feature_baseline/
```

나중에 이 항목들이 생성되면 같은 change에서 `README.md`와 이 문서를 update한다.

## 6. Verification

Feature-contract change는 다음 항목과 대조해 확인해야 한다.

```text
docs/specs/database_schema.md
docs/specs/pipeline_skeleton.md
docs/specs/mongo_live_replay_contract.md
src/cms/contracts/measurement.py
```

update 후 skeleton contract verification을 실행한다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py
```
