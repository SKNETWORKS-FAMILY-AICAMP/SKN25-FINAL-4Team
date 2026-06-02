#!/usr/bin/env python3
"""Apply readable message-label backgrounds to rendered CMS sequence SVGs.

This compatibility helper delegates the actual SVG mutation to
``scripts/verify/render_diagrams.py``. Prefer running ``render_diagrams.py``
for full Mermaid source-to-SVG regeneration.
"""

from __future__ import annotations

from pathlib import Path

from render_diagrams import DIAGRAM_DIR, add_sequence_text_backgrounds


def main() -> None:
    for path in sorted(DIAGRAM_DIR.glob("sequence_*.svg")):
        count = add_sequence_text_backgrounds(path)
        print(f"{path.name}: message_text_bg={count}")


if __name__ == "__main__":
    main()
