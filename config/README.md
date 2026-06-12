# config

계량기 메타데이터를 관리하는 폴더입니다.

## 파일

| 파일 | 역할 |
|------|------|
| `meter_metadata.json` | API에서 조회할 계량기 표시명, 타입, 설명 등 정적 메타데이터 |
| `meter_metadata.py` | `meter_metadata.json`을 읽어 API router에서 사용하기 쉬운 형태로 제공 |
| `__init__.py` | Python package 인식용 파일 |

## 주의

- 모델 학습/추론의 핵심 계량기 스펙은 `src/energy_v84/common/config.py`와 `mapping.py`에도 정의되어 있습니다.
- 프론트/API 표시용 메타데이터와 모델 라우팅용 설정을 혼동하지 않아야 합니다.
