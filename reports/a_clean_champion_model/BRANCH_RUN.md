# A-clean champion model branch run

## Branch

```text
exp/a-clean-champion-models-20260520
```

Base commit at branch creation:

```text
4d4e7a8
```

## Rollback policy

- 이 작업은 `exp/a-clean-champion-models-20260520` branch에서 진행한다.
- 기존 `won/workspace`로 돌아가려면 `git switch won/workspace`를 사용한다.
- 이 branch에서 만든 산출물을 폐기하려면 branch 삭제 또는 해당 branch의 commit revert/reset으로 되돌린다.
- 기존 untracked 산출물이 많으므로 commit 전에는 `git status --short`로 포함 범위를 분리한다.

## Scope

A-clean 4개 target을 대상으로 논문 자료와 기존 paper-adjacent run을 참고해 champion model 후보를 만들고, RunPod에서 학습/평가한다.

입력 dataset:

```text
outputs/modeling/a_clean_targets_1h/
```

대상 target:

```text
T1_group__central_cooling__P
T1_group__local_cooling__P
T1_group__server_power__P
T1_group__ventilation__P
```

금지/보류:

```text
signed/net target 포함 안 함
기존 파일 삭제 안 함
원격 push 안 함
```
