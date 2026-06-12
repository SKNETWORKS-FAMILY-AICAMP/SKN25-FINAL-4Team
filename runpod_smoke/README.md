# runpod_smoke

RunPod serverless 인프라 자체가 정상 동작하는지 확인하기 위한 최소 smoke handler입니다.

## 파일

| 파일 | 역할 |
|------|------|
| `handler.py` | 입력을 그대로 받아 간단한 성공 응답을 반환하는 RunPod 테스트 handler |

## 사용 목적

`runpod_job`이 실패했을 때 원인이 모델 코드인지, Docker image/RunPod endpoint/API key/GPU worker 문제인지 분리하기 위해 사용합니다.

- smoke도 실패하면 RunPod endpoint, image pull, API key, worker 설정 문제일 가능성이 큽니다.
- smoke는 성공하고 `runpod_job`만 실패하면 DB 접속, 학습 코드, artifact upload 설정 문제일 가능성이 큽니다.
