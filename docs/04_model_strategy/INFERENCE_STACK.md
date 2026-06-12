# Inference Stack Notes

이 문서는 `energy_v84` 모델을 실제 서비스에 붙일 때 필요한 추론 실행 구조, 저장 위치, 사양 측정 결과를 정리한다.

학습, 추론, API 재학습, 검증, 승격 명령어 순서는 `docs/04_model_strategy/MODEL_OPERATION_COMMANDS.md`에 정리되어 있다.

## 1. 현재 추론 코드

| 항목 | 내용 |
|------|------|
| 추론 엔트리포인트 | `src/energy_v84/inference.py` |
| 실행 방식 | `python -m energy_v84.inference` |
| 현재 서비스 horizon | `3h` |
| 예측 step | `t+1`, `t+2`, `t+3` |
| 주요 입력 | DB에서 조회한 계량기별 최근 history |
| 주요 출력 | 예측값, step별 warning, 물리 이상 태그, 입력 품질, reason code |
| 출력 스키마 문서 | `MODEL_IO_SPEC.md` |

예시 실행:

```bash
conda activate skn25
cd /home/sms/openclaw_file/skn25_final

python -m energy_v84.inference \
  --horizon 3 \
  --timestamp "2023-06-01T09:00:00+00:00" \
  --output-dir /tmp/inference_results
```

CPU-only 테스트:

```bash
CUDA_VISIBLE_DEVICES="" /usr/bin/time -v python -m energy_v84.inference \
  --horizon 3 \
  --timestamp "2023-06-01T09:00:00+00:00" \
  --output-dir /tmp/inference_perf_test_cpu
```

## 2. 추론에 필요한 artifact

추론 서버는 active artifact를 읽어야 한다. 현재 로컬 기준 active artifact 위치는 아래와 같다.

```text
artifacts/
  3h/
    {model_urn}/
      routing.json
      train_meta.json
      input_scaler.joblib
      target_scaler.joblib
      feature_columns.json
      hour_bias_corrections.csv
      ridge.joblib
      lstm_*.pt
      catboost.cbm
      lightgbm_t_plus_*.txt
  thresholds/
    val_thresholds.csv
  meter_tags.csv
```

실제 운영에서는 아래 둘 중 하나로 관리할 수 있다.

| 방식 | 설명 | 비고 |
|------|------|------|
| EC2/EBS 저장 | EC2 디스크에 `active/candidate/archive` 폴더를 둠 | 단순하고 비용 구조가 이해하기 쉬움 |
| S3 저장 | S3에 `active/candidate/archive` artifact를 둠 | 여러 서버 공유, 백업, 버전 관리에 유리 |

S3는 필수는 아니다. 초기 운영에서는 EC2 디스크에 artifact를 저장하고, 필요 시 S3로 이전해도 된다.

## 3. 권장 운영 구조

### 추론 실행

```text
Airflow 또는 스케줄러
  -> EC2 추론 프로세스 실행
  -> DB에서 최근 history 조회
  -> 1차 물리 이상 태깅
  -> 입력 보정
  -> 모델 추론
  -> threshold 기반 warning 계산
  -> 결과 DB 저장
```

추론은 AWS에서 돌리는 것이 자연스럽다.

- DB와 백엔드가 AWS에 있기 때문
- 추론 결과를 바로 DB에 저장해야 하기 때문
- 현재 측정 기준으로 GPU 없이 CPU 추론도 가능해 보이기 때문

### Airflow cold/warm 기준

| 구조 | 성격 |
|------|------|
| Airflow가 매번 `python -m ...inference` 실행 | cold start |
| Airflow가 Docker/ECS task를 매번 새로 실행 | cold start |
| Airflow가 상시 실행 중인 FastAPI/worker에 요청 | warm start |

현재 단일 timestamp 추론이 약 12초 수준이므로, 1시간 또는 3시간 주기에서는 cold start여도 큰 병목은 아닐 가능성이 높다.

## 4. 로컬 성능 측정 결과

측정 조건:

- `horizon=3`
- `timestamp=2023-06-01T09:00:00+00:00`
- 63개 계량기 대상
- CPU-only 강제: `CUDA_VISIBLE_DEVICES=""`
- DB 조회 포함
- artifact/model 로딩 포함

### Cold start

새 Python 프로세스를 실행해 추론한 결과다.

| 항목 | 결과 |
|------|------|
| wall clock time | 약 `11.88초` |
| CPU 사용률 | `197%` |
| 평균 CPU 코어 사용 추정 | 약 `2코어` |
| user CPU time | `22.79초` |
| system CPU time | `0.71초` |
| 최대 메모리 | `1,034,504 KB` 약 `1.0 GB` |
| 종료 상태 | `0` |

### Warm start 참고

같은 Python 프로세스 안에서 `run_inference()`를 두 번 호출한 결과다.

| 항목 | 결과 |
|------|------|
| 1번째 run | 약 `8.47초` |
| 2번째 run | 약 `7.87초` |
| 최대 메모리 | 약 `1.0 GB` |

## 5. AWS 추론 서버 사양 판단

현재 로컬 측정만 기준으로 단정할 수는 없다. AWS 인스턴스 CPU, DB 네트워크, 디스크 성능이 다르기 때문이다.

다만 현재 결과 기준으로는 아래처럼 시작할 수 있다.

| 사양 | 판단 |
|------|------|
| 4 vCPU / 8GB RAM | 동작 가능성이 높지만 운영 여유는 작을 수 있음 |
| 4 vCPU / 16GB RAM | 첫 운영 테스트용 권장 |
| 8 vCPU / 16~32GB RAM | 여유 운영용 |

최종 최소 사양은 EC2에서 동일 명령으로 다시 측정해야 한다.

측정 시 확인할 항목:

- 단일 timestamp 전체 추론 시간
- 최대 메모리 사용량
- DB 조회 시간
- 모델 로딩 시간
- 실패/timeout 여부
- 동시에 백엔드/LLM 요청이 있을 때 영향

## 6. 재학습과 추론의 위치 분리

추론은 AWS에서 수행하고, 재학습은 RunPod 같은 GPU 환경에서 수행하는 구조가 적합하다.

```text
AWS
  - DB
  - 백엔드/API
  - 추론 실행
  - active artifact 관리
  - validate/promote 실행

RunPod
  - GPU 재학습
  - candidate artifact 생성
  - candidate artifact 압축 및 AWS 업로드
```

재학습 결과는 바로 active로 넣지 않는다.

```text
RunPod 재학습
  -> candidate/run_xxx 생성
  -> AWS로 업로드
  -> validate_candidate.py 실행
  -> validated.marker 생성
  -> 사용자/관리자 확인
  -> promote_candidate.py 실행
  -> active 백업 후 승격
```

### RunPod Serverless 재학습 구조

현재 유지하는 목표 구조는 RunPod Serverless endpoint를 호출하면 worker가 자동으로 준비되고, 학습 종료 후 idle/scale down 상태로 돌아가는 방식이다.

```text
로컬 또는 EC2
  -> RunPod Serverless /run 호출

RunPod Serverless worker
  -> runpod_job.handler 실행
  -> train.py 실행
  -> candidate/run_xxx 생성
  -> tar.gz 압축
  -> 로컬/EC2 /model-artifacts/upload 로 업로드

로컬 또는 EC2
  -> candidate 저장
  -> validate_candidate.py 실행
  -> 필요 시 promote_candidate.py 실행
```

이 구조에서 `meters=["H1.Z10"]`, `epochs=1`처럼 일부 계량기만 학습하는 것은 연결 확인용 smoke test다. smoke test candidate는 업로드까지 성공해도 validate 단계에서 실패하는 것이 정상이다. 검증 스크립트는 전체 51개 모델 artifact와 `train_summary_3h.csv`를 기대하기 때문이다.

운영 후보 artifact 생성은 폴더 재배치가 아니라, RunPod에서 기본 설정 그대로 전체 51개 모델을 재학습해 검증/승격 가능한 candidate 묶음을 만드는 작업이다.

```text
운영 후보 artifact
  candidate/{run_id}/
    3h/{model_urn}/...        # 기대 모델 51개
    train_summary_3h.csv
```

전체 candidate가 업로드된 뒤에만 `validate_candidate.py`가 pass 또는 warn marker를 만들 수 있고, 그 이후에만 `promote_candidate.py`로 active 승격을 진행한다.

## 7. LLM 연동 시 역할 분리

LLM은 직접 서버 파일을 수정하거나 RunPod에 SSH 접속하는 주체가 아니라, 백엔드 API를 호출하고 결과를 요약하는 역할로 두는 것이 안전하다.

```text
사용자/공장 관리자
  -> LLM에 자연어 요청
  -> LLM이 백엔드 API 호출
  -> 백엔드가 RunPod job 실행 또는 validate/promote 실행
  -> LLM이 결과 요약
  -> 사용자/관리자가 승격 여부 승인
```

권장 권한 분리:

| 주체 | 역할 |
|------|------|
| 사용자/공장 관리자 | 재학습 요청, 검증 요청, 승격 승인 |
| LLM | 명령 해석, API 호출 요청, 결과 요약 |
| AWS 백엔드 | 실제 실행 권한 보유, RunPod/API/검증/승격 제어 |
| RunPod | 재학습 실행 |

## 8. 결과 불변 최적화 후보

결과를 바꾸지 않는 선에서 가능한 최적화만 정리한다.

| 최적화 | 결과 영향 | 설명 |
|--------|-----------|------|
| artifact/model 캐시 | 없음 | 같은 프로세스 안에서 모델 파일 반복 로딩 방지 |
| DB 조회 최소화 | 없음, 단 row 집합 동일해야 함 | 필요한 기간을 한 번 조회 후 메모리 slicing |
| 전이 모델 artifact 공유 캐시 | 없음 | 같은 `model_urn`을 쓰는 계량기는 모델 파일을 한 번만 로드 |
| batch 전용 window 사전 생성 | 검증 필요 | 전처리 순서가 조금만 달라도 결과가 달라질 수 있어 신중히 적용 |

현재 측정 기준에서는 추론 속도 자체가 큰 병목은 아니므로, 우선순위는 DB timestamp 정합성, 실패 재시도, 운영 결과 저장 구조가 더 높다.
