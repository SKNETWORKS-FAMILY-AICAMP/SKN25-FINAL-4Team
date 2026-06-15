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

- `/meters` 목록 API는 사람이 빠르게 확인할 수 있도록 일부 필드만 반환합니다.
- `/meters/{meter_urn}` 상세 API는 `meter_metadata.json`의 전체 필드를 반환합니다.
- `note`, `location_prefix`, `anomaly_target`, `installation_note`, `redundant_pair`는 원본 metadata로 보존하되 목록 응답에서는 제외합니다.
- 일부 설명과 그룹 정보는 논문/Dryad 기반 정보와 수동 추정 정보가 섞여 있으므로, 운영 판단에 직접 쓰기 전 검수가 필요합니다.
