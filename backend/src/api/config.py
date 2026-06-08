import json
import os
import re
from pathlib import Path

# src 경로 기준 JSON 위치
_CONFIG_PATH = Path(__file__).parent.parent / "equipment_config.json"

def get_equipment_list() -> list[dict]:
    """JSON 설정 파일에서 설비 목록 반환. 파일이 작으므로 캐시 없이 매번 읽음."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Config] 설비 설정 파일 로드 실패: {e}")
        return []


def get_eq_keywords() -> list[tuple[str, re.Pattern]]:
    """JSON 설정 파일에서 에이전트 정규식용 키워드 매핑 반환."""
    result = []
    for eq in get_equipment_list():
        kw_list = eq.get("keywords", [])
        if kw_list:
            result.append((eq["id"], re.compile("|".join(kw_list))))
    return result
