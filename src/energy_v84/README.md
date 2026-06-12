# src/energy_v84

v84 residual 모델의 핵심 학습/추론 패키지입니다.

## 주요 파일

| 파일 | 역할 |
|------|------|
| `train.py` | 2-pass 학습 파이프라인. LSTM, CatBoost, LightGBM, Ridge, seasonal naive, 앙상블 라우팅과 artifact 저장을 수행 |
| `inference.py` | active artifact를 읽어 3h 예측, step별 warning, 입력 품질, 물리 이상 태그, reason code를 생성 |
| `compute_thresholds.py` | validation actual P 기준 시간대별 사전 경보 threshold 생성 |
| `generate_inference_report.py` | `artifacts/inference_results`의 추론 CSV를 HTML 리포트로 변환 |
| `common/` | DB, 전처리, 모델 정의, ensemble, artifact I/O, 라우팅 등 공통 모듈 |

## 실행 예시

```bash
PYTHONPATH=src:. python -m energy_v84.inference --horizon 3
PYTHONPATH=src:. python -m energy_v84.train --horizon 3
PYTHONPATH=src:. python -m energy_v84.compute_thresholds
```

## artifact 의존성

추론은 `artifacts/3h/{meter_urn}/` 아래의 모델 파일과 `artifacts/thresholds/val_thresholds.csv`를 읽습니다.
학습은 기본적으로 `artifacts/candidate/<run_id>/`에 새 산출물을 저장합니다.
