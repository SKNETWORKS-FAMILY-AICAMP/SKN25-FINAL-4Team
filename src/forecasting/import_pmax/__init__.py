"""Import P-max forecasting training and inference."""

from __future__ import annotations

__all__ = ["DEFAULT_MODEL_ROOT", "FEATURE_COLUMNS", "LOGICAL_METERS"]


def __getattr__(name: str):
    if name == "DEFAULT_MODEL_ROOT":
        from .inference import DEFAULT_MODEL_ROOT

        return DEFAULT_MODEL_ROOT
    if name in {"FEATURE_COLUMNS", "LOGICAL_METERS"}:
        from . import training

        return getattr(training, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
