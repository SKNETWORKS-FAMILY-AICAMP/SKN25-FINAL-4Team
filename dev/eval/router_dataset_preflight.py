# -*- coding: utf-8 -*-
"""Common preflight validator for Stage-1 / Stage-2 router datasets.

This module validates:
1) JSON parsing + schema conformance
2) unique id
3) minimum per-class distribution for target label fields

It is intentionally lightweight and can be called from evaluator entrypoints
(router_accuracy_eval.py, future two-stage runners, CI hooks).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except Exception as exc:
    Draft202012Validator = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT / "schemas"

# ── 기본 분포 최소값(강제 규칙) ─────────────────────────────────────────
# stage별 분포가 극단적으로 붕괴하지 않도록 최소 개수만 검사한다.
DEFAULT_MIN_DISTRIBUTION = {
    "router_stage1_request_type": {
        "expected_request_type": {
            "query": 100,
            "action_request": 50,
            "approval_required": 40,
            "off_topic": 40,
        }
    },
    "router_stage2_agent_route": {
        "expected_route": {
            "anomaly": 80,
            "cms": 70,
            "forecast": 50,
            "report": 80,
            "rag": 50,
        }
    },
}

# 데이터셋명에서 스키마/체크 규칙 추론
SCHEMA_BY_DATASET_HINT = {
    "stage1": (SCHEMA_DIR / "router_stage1_request_type.schema.json", "router_stage1_request_type"),
    "stage2": (SCHEMA_DIR / "router_stage2_agent_route.schema.json", "router_stage2_agent_route"),
}


def _load_json(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"루트 타입이 JSON 배열이 아님: {type(rows).__name__}")
    normalized: list[dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"{i}번째 항목이 dict가 아님: {type(row).__name__}")
        normalized.append(row)
    return normalized


def _infer_schema_and_profile(dataset_path: Path) -> tuple[Path | None, str | None]:
    stem = dataset_path.name.lower()
    if "stage1" in stem:
        return SCHEMA_BY_DATASET_HINT["stage1"]
    if "stage2" in stem:
        return SCHEMA_BY_DATASET_HINT["stage2"]
    return None, None


def _parse_min_distribution(value: str | None) -> dict[str, int] | None:
    if not value:
        return None
    out: dict[str, int] = {}
    for item in value.split(","):
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"--min-distribution 형식 오류: {item} (예: query:100,cms:70)")
        k, v = item.split(":", 1)
        k = k.strip()
        if not k:
            raise ValueError(f"--min-distribution 형식 오류: 키가 비어있음 ({item})")
        try:
            out[k] = int(v.strip())
        except ValueError as e:
            raise ValueError(f"--min-distribution 숫자 변환 실패: {item}") from e
    if not out:
        raise ValueError("--min-distribution이 비어 있습니다")
    return out


def _determine_label_field(schema: dict[str, Any], config_profile: str | None) -> str | None:
    # 스키마에서 분포 검증에 쓰일 타겟 라벨 키를 우선 결정
    required = set(schema.get("required", []))
    if "expected_route" in required:
        return "expected_route"
    if "expected_request_type" in required:
        return "expected_request_type"

    # 보조 후보로 title/설명에서 추정
    title = str(schema.get("title", "")).lower()
    if "stage2" in title:
        return "expected_route"
    if "stage1" in title:
        return "expected_request_type"

    if config_profile == "router_stage2_agent_route":
        return "expected_route"
    if config_profile == "router_stage1_request_type":
        return "expected_request_type"
    return None


def run_preflight(dataset: Path, schema_path: Path | None = None, *,
                 min_distribution: dict[str, int] | None = None,
                 label_field_override: str | None = None,
                 emit_json: bool = False) -> dict[str, Any]:
    """Run preflight checks. Return detailed result dict with pass/fail + issues.

    Returns:
      {"ok": bool, "dataset": str, ...}
    """
    if Draft202012Validator is None:
        raise RuntimeError(
            "jsonschema 패키지가 필요합니다. `pip install jsonschema` 후 재실행하세요."
            f" (현재 오류: {_IMPORT_ERROR})"
        )

    rows = _load_json(dataset)

    resolved_schema_path, profile = _infer_schema_and_profile(dataset)
    if schema_path is None:
        schema_path = resolved_schema_path
        profile = profile

    schema: dict[str, Any] = {}
    if schema_path is not None:
        schema = json.loads(schema_path.expanduser().read_text(encoding="utf-8"))

    # 1) 스키마 유효성 검사
    schema_errors: list[dict[str, Any]] = []
    if schema_path is None:
        schema_errors.append({"type": "schema", "message": "스키마 경로 미지정/미추론", "detail": "stage1/2 힌트 없이 자동 추론 실패"})
    else:
        validator = Draft202012Validator(schema)
        if schema.get("type") == "array" and schema.get("items") is not None:
            errors = sorted(validator.iter_errors(rows), key=lambda e: e.path)
            for err in errors:
                schema_errors.append({
                    "type": "schema",
                    "index": 1,
                    "path": "/" + "/".join(str(p) for p in err.path),
                    "message": err.message,
                })
        else:
            for idx, row in enumerate(rows, 1):
                errors = sorted(validator.iter_errors(row), key=lambda e: e.path)
                for err in errors:
                    schema_errors.append({
                        "type": "schema",
                        "index": idx,
                        "path": "/" + "/".join(str(p) for p in err.path),
                        "message": err.message,
                    })

    # 2) 고유 ID 검사
    id_counter = Counter(str(row.get("id", "")) for row in rows)
    duplicated_ids = sorted([k for k, n in id_counter.items() if n > 1 and k])

    # 3) 분포 최소값 검사
    distribution_errors: list[dict[str, Any]] = []
    active_profile = profile
    if active_profile is None and schema:
        title = str(schema.get("title", "")).lower()
        if "stage1" in title:
            active_profile = "router_stage1_request_type"
        elif "stage2" in title:
            active_profile = "router_stage2_agent_route"

    if not schema and not min_distribution and not profile:
        active_profile = None

    label_field = label_field_override or _determine_label_field(schema, active_profile)
    required_min: dict[str, int] = {}

    if label_field:
        counts = Counter(row.get(label_field) for row in rows)
        required_min: dict[str, int] = {}

        if min_distribution:
            required_min.update(min_distribution)
        elif active_profile and active_profile in DEFAULT_MIN_DISTRIBUTION:
            required_min.update(DEFAULT_MIN_DISTRIBUTION[active_profile].get(label_field, {}))
        elif profile is None and schema:
            props = schema.get("properties", {})
            enum_vals = props.get(label_field, {}).get("enum")
            if isinstance(enum_vals, list):
                required_min = {str(v): 1 for v in enum_vals}

        for key, min_count in required_min.items():
            observed = counts.get(key, 0)
            if observed < min_count:
                distribution_errors.append({
                    "type": "distribution",
                    "label": label_field,
                    "key": key,
                    "observed": observed,
                    "minimum": min_count,
                })
    else:
        counts = Counter()

    ok = (
        len(schema_errors) == 0
        and len(distribution_errors) == 0
        and len(duplicated_ids) == 0
    )

    result: dict[str, Any] = {
        "ok": ok,
        "dataset": str(dataset),
        "schema_path": str(schema_path) if schema_path else None,
        "schema_resolved_from_dataset": bool(resolved_schema_path is not None and (schema_path == resolved_schema_path)),
        "row_count": len(rows),
        "schema_errors": schema_errors,
        "duplicate_ids": duplicated_ids,
        "distribution": {
            "label_field": label_field,
            "counts": {str(k): int(v) for k, v in (Counter(row.get(label_field) for row in rows) if label_field else Counter()).items()} if label_field else {},
            "required_min": required_min,
            "issues": distribution_errors,
        },
    }

    if emit_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=== Preflight summary ===")
        print(f"dataset: {result['dataset']}")
        print(f"rows: {len(rows)}")
        print(f"schema: {result['schema_path'] or 'N/A'}")
        print(f"label field: {label_field or 'N/A'}")
        if label_field:
            print(f"distribution: {result['distribution']['counts']}")
        if duplicated_ids:
            print(f"duplicate ids: {duplicated_ids[:10]}{'...' if len(duplicated_ids) > 10 else ''}")
        if schema_errors:
            print(f"schema_errors: {len(schema_errors)}")
            for e in schema_errors[:5]:
                print(f"  - {e}")
        if distribution_errors:
            print(f"distribution_errors: {len(distribution_errors)}")
            for e in distribution_errors:
                print(f"  - {e}")
        print("OK" if ok else "FAILED")

    return result



def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Router dataset 공통 preflight")
    p.add_argument("dataset", type=Path, help="검증할 JSON 데이터셋 경로")
    p.add_argument("--schema", dest="schema", type=Path, default=None,
                   help="옵션: 스키마 파일 경로. 생략 시 stage1/stage2 파일명으로 자동 추론")
    p.add_argument("--label-field", default=None, help="분포 검사할 라벨 키(override)")
    p.add_argument("--min-distribution", default=None,
                   help="라벨 최소값 override. 예: query:100,cms:70")
    p.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    return p


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        dist = _parse_min_distribution(args.min_distribution)
        result = run_preflight(args.dataset, args.schema,
                               min_distribution=dist,
                               label_field_override=args.label_field,
                               emit_json=args.json)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
