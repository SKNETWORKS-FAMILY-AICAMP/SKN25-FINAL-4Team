# src

서비스에서 import하는 Python package 루트입니다.

실행 시 보통 아래처럼 `src`를 `PYTHONPATH`에 포함합니다.

```bash
PYTHONPATH=src:. python -m energy_v84.inference --horizon 3
```

## 하위 폴더

| 폴더 | 역할 |
|------|------|
| `energy_v84/` | residual 기반 v84 모델의 학습, 추론, threshold, report 코드 |
