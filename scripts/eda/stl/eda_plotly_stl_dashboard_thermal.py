from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eda.stl.eda_plotly_stl_dashboard_electric import OUTPUT_ROOT, generate_dashboard


THERMAL_METERS = [
    "V.K21",
    "H1.K11",
    "H1.K12",
    "H1.K14",
    "H1.K15",
    "H1.K16",
    "H2.K21",
    "H1.W11",
    "H1.W12",
]

THERMAL_DIR = OUTPUT_ROOT / "thermal"
HTML_PATH = OUTPUT_ROOT / "thermal_stl_dashboard.html"


def main() -> None:
    generate_dashboard(
        meters=THERMAL_METERS,
        title="Thermal STL Dashboard",
        heading="Thermal STL Plotly Dashboard",
        accent="#7a5b2a",
        body_bg="linear-gradient(180deg, #f8f4ed 0%, #efe3d1 100%)",
        panel_bg="rgba(255, 252, 247, 0.95)",
        output_dir=THERMAL_DIR,
        html_path=HTML_PATH,
    )


if __name__ == "__main__":
    main()
