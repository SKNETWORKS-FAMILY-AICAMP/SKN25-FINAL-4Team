#!/usr/bin/env python3
"""Backward-compatible wrapper for scripts/stream/canonical_promotion.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_TARGET = Path(__file__).with_name("canonical_promotion.py")
_SPEC = importlib.util.spec_from_file_location("_cms_stream_canonical_promotion", _TARGET)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - importlib guard
    raise ImportError(f"cannot load renamed canonical promotion entrypoint: {_TARGET}")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

main = _MODULE.main
__all__ = tuple(name for name in vars(_MODULE) if not name.startswith("_"))


def __getattr__(name: str) -> Any:
    return getattr(_MODULE, name)


if __name__ == "__main__":
    raise SystemExit(main())
