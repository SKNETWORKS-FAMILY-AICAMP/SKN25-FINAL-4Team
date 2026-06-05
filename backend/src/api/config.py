import json
import os
import re
from pathlib import Path

# src 경로 기준 JSON 위치
_CONFIG_PATH = Path(__file__).parent.parent / "equipment_config.json"

_EQUIPMENT_CACHE = None
_EQ_KEYWORDS_CACHE = None

def get_equipment_list() -> list[dict]:
    """JSON 설정 파일에서 설비 목록(API 및 메타데이터용) 반환"""
    global _EQUIPMENT_CACHE
    if _EQUIPMENT_CACHE is None:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 원본 딕셔너리를 그대로 반환하여 cms.py에서 활용할 수 있게 합니다.
                _EQUIPMENT_CACHE = data
        except Exception as e:
            print(f"[Config] 설비 설정 파일 로드 실패: {e}")
            _EQUIPMENT_CACHE = []
    return _EQUIPMENT_CACHE


def get_eq_keywords() -> list[tuple[str, re.Pattern]]:
    """JSON 설정 파일에서 에이전트 정규식용 키워드 매핑 반환"""
    global _EQ_KEYWORDS_CACHE
    if _EQ_KEYWORDS_CACHE is None:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                _EQ_KEYWORDS_CACHE = []
                for eq in data:
                    kw_list = eq.get("keywords", [])
                    if kw_list:
                        pattern = re.compile("|".join(kw_list))
                        _EQ_KEYWORDS_CACHE.append((eq["id"], pattern))
        except Exception as e:
            print(f"[Config] 설비 설정 파일 키워드 로드 실패: {e}")
            _EQ_KEYWORDS_CACHE = []
    return _EQ_KEYWORDS_CACHE
