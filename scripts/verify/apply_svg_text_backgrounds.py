#!/usr/bin/env python3
"""Add small white background rectangles behind Mermaid sequence SVG text labels.

This keeps message labels readable when rendered over participant lifelines or arrows.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

DIAGRAM_DIR = Path("docs/specs/diagrams")
DIAGRAMS = [
    "01_pre_model_pipeline",
    "02_latency_sequence",
    "03_chat_routing",
    "04_airflow_report",
]
TARGET_CLASSES = {"messageText", "loopText", "labelText", "sectionTitle"}

TEXT_RE = re.compile(r"(<text\b(?P<attrs>[^>]*)>(?P<body>.*?)</text>)", re.DOTALL)
ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")
EXISTING_BG_RE = re.compile(r'<rect\s+class="text-bg"[^>]*/>')


def attrs_to_dict(attrs: str) -> dict[str, str]:
    return dict(ATTR_RE.findall(attrs))


def class_tokens(value: str) -> set[str]:
    return set(value.split())


def first_float(*values: str | None, default: float = 0.0) -> float:
    for value in values:
        if not value:
            continue
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return default


def dy_to_px(value: str | None, font_size: float) -> float:
    if not value:
        return 0.0
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return 0.0
    number = float(match.group(0))
    if "em" in value:
        return number * font_size
    return number


def text_lines(body: str) -> list[str]:
    parts = re.findall(r"<tspan\b[^>]*>(.*?)</tspan>", body, flags=re.DOTALL)
    if not parts:
        parts = [TAG_RE.sub("", body)]
    lines = [html.unescape(TAG_RE.sub("", part)).strip() for part in parts]
    return [line for line in lines if line]


def estimate_rect(attrs: dict[str, str], body: str) -> str | None:
    tokens = class_tokens(attrs.get("class", ""))
    if not (tokens & TARGET_CLASSES):
        return None

    lines = text_lines(body)
    if not lines:
        return None

    # Prefer explicit text x/y, then first tspan x/y if Mermaid emits it there.
    tspan_match = re.search(r"<tspan\b([^>]*)>", body)
    tspan_attrs = attrs_to_dict(tspan_match.group(1)) if tspan_match else {}
    x = first_float(attrs.get("x"), tspan_attrs.get("x"), default=0.0)
    y = first_float(attrs.get("y"), tspan_attrs.get("y"), default=0.0)
    font_size = first_float(attrs.get("font-size"), attrs.get("style"), default=16.0)
    dy = dy_to_px(attrs.get("dy"), font_size) + dy_to_px(tspan_attrs.get("dy"), font_size)

    width = max(len(line) for line in lines) * font_size * 0.58 + 14
    height = max(1, len(lines)) * font_size * 1.22 + 8

    style = attrs.get("style", "")
    anchor = attrs.get("text-anchor", "")
    if "text-anchor: middle" in style or anchor == "middle":
        rx = x - width / 2
    elif "text-anchor: end" in style or anchor == "end":
        rx = x - width
    else:
        rx = x - 7

    y += dy

    if attrs.get("dominant-baseline") in {"central", "middle"} or attrs.get("alignment-baseline") in {"central", "middle"}:
        ry = y - height / 2
    else:
        ry = y - font_size - 4

    return (
        f'<rect class="text-bg" x="{rx:.1f}" y="{ry:.1f}" '
        f'width="{width:.1f}" height="{height:.1f}" rx="3" ry="3" '
        f'fill="#ffffff" fill-opacity="0.96" stroke="#cbd5e1" '
        f'stroke-opacity="0.75" stroke-width="0.8"/>'
    )


def add_backgrounds(svg: str) -> tuple[str, int]:
    svg = EXISTING_BG_RE.sub("", svg)
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        rect = estimate_rect(attrs_to_dict(match.group("attrs")), match.group("body"))
        if rect is None:
            return match.group(1)
        count += 1
        return rect + match.group(1)

    return TEXT_RE.sub(repl, svg), count


def main() -> None:
    for name in DIAGRAMS:
        path = DIAGRAM_DIR / f"{name}.svg"
        svg = path.read_text(encoding="utf-8")
        updated, count = add_backgrounds(svg)
        path.write_text(updated, encoding="utf-8")
        print(f"{name}: text_bg={count}")


if __name__ == "__main__":
    main()
