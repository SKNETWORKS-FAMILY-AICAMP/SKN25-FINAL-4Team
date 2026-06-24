#!/usr/bin/env python3
"""Render CMS Mermaid diagrams to SVG and verify basic readability properties.

The script keeps the canonical source in ``docs/diagrams/{flow,seq}/*.mmd`` and
regenerates matching ``*.svg`` files. Sequence diagrams get explicit text
background boxes behind Mermaid message labels so labels remain readable when
arrows pass underneath them.
"""

from __future__ import annotations

import argparse
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIAGRAM_DIR = ROOT / "docs/diagrams"
CONFIG_PATH = DIAGRAM_DIR / "config/mermaid.json"
PNG_PREVIEW_DIR = Path("/tmp/cms_spec_diagram_render/png")
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render CMS Mermaid diagrams to SVG.")
    parser.add_argument("--png-preview", action="store_true", help="Also create PNG previews under /tmp.")
    return parser.parse_args()


def render_svg(source: Path) -> Path:
    target = source.with_suffix(".svg")
    subprocess.run(
        [
            "npx",
            "-y",
            "@mermaid-js/mermaid-cli@latest",
            "-c",
            str(CONFIG_PATH),
            "-i",
            str(source),
            "-o",
            str(target),
            "-b",
            "white",
        ],
        cwd=ROOT,
        check=True,
    )
    return target


def _text_content(node: ET.Element) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in list(node):
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return " ".join(part.strip() for part in parts if part.strip())


def _float_attr(node: ET.Element, name: str, default: float) -> float:
    value = node.attrib.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def add_sequence_text_backgrounds(svg_path: Path) -> int:
    text = svg_path.read_text(encoding="utf-8")
    if "sequenceDiagram" not in text and "messageText" not in text:
        return 0

    tree = ET.parse(svg_path)
    root = tree.getroot()
    count = 0
    for parent in root.iter():
        children = list(parent)
        inserts: list[tuple[int, ET.Element]] = []
        for index, child in enumerate(children):
            if child.tag != f"{{{SVG_NS}}}text":
                continue
            class_name = child.attrib.get("class", "")
            if not any(token in class_name for token in ["messageText", "loopText", "labelText"]):
                continue
            label = _text_content(child)
            if not label:
                continue
            x = _float_attr(child, "x", 0.0)
            y = _float_attr(child, "y", 0.0)
            dy = child.attrib.get("dy", "")
            font_size = 16.0
            style = child.attrib.get("style", "")
            for part in style.split(";"):
                part = part.strip()
                if part.startswith("font-size:") and part.endswith("px"):
                    try:
                        font_size = float(part.split(":", 1)[1].strip()[:-2])
                    except ValueError:
                        pass
            if dy.endswith("em"):
                try:
                    y += float(dy[:-2]) * font_size
                except ValueError:
                    pass
            elif dy.endswith("px"):
                try:
                    y += float(dy[:-2])
                except ValueError:
                    pass
            anchor = child.attrib.get("text-anchor", "middle")
            width = max(72.0, min(430.0, len(label) * 7.1 + 18.0))
            height = 21.0
            if anchor == "start":
                rect_x = x - 9.0
            elif anchor == "end":
                rect_x = x - width + 9.0
            else:
                rect_x = x - width / 2.0
            rect = ET.Element(
                f"{{{SVG_NS}}}rect",
                {
                    "class": "message-text-bg",
                    "x": f"{rect_x:.1f}",
                    "y": f"{y - 15.5:.1f}",
                    "width": f"{width:.1f}",
                    "height": f"{height:.1f}",
                    "rx": "4",
                    "ry": "4",
                    "fill": "#ffffff",
                    "fill-opacity": "0.96",
                    "stroke": "#cbd5e1",
                    "stroke-opacity": "0.85",
                    "stroke-width": "0.8",
                },
            )
            inserts.append((index + count, rect))
            count += 1
        for index, rect in inserts:
            parent.insert(index, rect)
    if count:
        tree.write(svg_path, encoding="unicode", xml_declaration=False)
    return count


def render_png_preview(svg_path: Path) -> Path:
    target = PNG_PREVIEW_DIR / svg_path.relative_to(DIAGRAM_DIR).with_suffix(".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["rsvg-convert", "-b", "white", "-f", "png", "-o", str(target), str(svg_path)],
        cwd=ROOT,
        check=True,
    )
    return target


def main() -> int:
    args = parse_args()
    rows: list[dict[str, object]] = []
    sources = sorted((DIAGRAM_DIR / "flow").glob("*.mmd")) + sorted((DIAGRAM_DIR / "seq").glob("*.mmd"))
    for source in sources:
        svg = render_svg(source)
        background_count = add_sequence_text_backgrounds(svg)
        png = render_png_preview(svg) if args.png_preview else None
        svg_text = svg.read_text(encoding="utf-8", errors="ignore")
        rows.append(
            {
                "source": str(source.relative_to(ROOT)),
                "svg": str(svg.relative_to(ROOT)),
                "bytes": svg.stat().st_size,
                "foreign_object_count": svg_text.count("foreignObject"),
                "message_text_bg_count": background_count,
                "png_preview": str(png) if png else None,
            }
        )
    for row in rows:
        print(row)
    expected_sources = 12
    if len(rows) != expected_sources:
        raise SystemExit(f"expected {expected_sources} diagram sources, got {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
