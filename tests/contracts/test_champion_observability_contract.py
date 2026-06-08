"""Import-safe Grafana/observability contracts for champion model monitoring."""

from __future__ import annotations

import subprocess
import sys

import pytest

from cms.contracts.observability import (
    CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS,
    CHAMPION_MODEL_DASHBOARD_UID,
    CHAMPION_MODEL_EXTERNAL_ALERT_SENDING_ENABLED,
    CHAMPION_MODEL_SOURCE_TABLES,
    DashboardPanelContract,
)

REQUIRED_CHAMPION_PANEL_TITLES = {
    "Model input readiness",
    "168h history coverage",
    "Prediction freshness",
    "Warning by horizon",
    "Post-hoc anomaly/error",
    "Champion inference latency",
    "Import P-Max prediction freshness",
    "Import P-Max quality status",
    "Import P-Max evaluation error",
}


def test_champion_model_observability_contract_imports_without_external_clients() -> None:
    code = (
        "import sys;"
        "from cms.contracts.observability import CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS;"
        "assert len(CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS) == 9;"
        "assert 'boto3' not in sys.modules;"
        "assert 'grafana_api' not in sys.modules;"
        "assert 'psycopg' not in sys.modules;"
        "print('ok')"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_champion_model_panel_contracts_cover_required_grafana_views() -> None:
    assert CHAMPION_MODEL_DASHBOARD_UID == "cms-champion-model"
    assert CHAMPION_MODEL_EXTERNAL_ALERT_SENDING_ENABLED is False

    panels = {panel.title: panel for panel in CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS}
    assert set(panels) == REQUIRED_CHAMPION_PANEL_TITLES

    assert panels["Model input readiness"].source_table == "mart.champion_model_input_1h"
    assert panels["168h history coverage"].source_table == "live.measurement_1h"
    assert panels["Prediction freshness"].source_table == "mart.champion_prediction_1h"
    assert panels["Warning by horizon"].source_table == "mart.champion_prediction_1h"
    assert panels["Post-hoc anomaly/error"].source_table == "qa.champion_prediction_issue"
    assert panels["Champion inference latency"].source_table == "ops.champion_inference_metric"
    assert panels["Import P-Max prediction freshness"].source_table == "mart.import_pmax_forecast_15min"
    assert panels["Import P-Max quality status"].source_table == "ops.import_pmax_inference_log"
    assert panels["Import P-Max evaluation error"].source_table == "qa.import_pmax_forecast_evaluation"

    assert {panel.source_table for panel in CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS} <= set(CHAMPION_MODEL_SOURCE_TABLES)
    assert {panel.severity for panel in CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS} == {"P0", "P1"}


def test_dashboard_panel_contract_allows_safe_mart_qa_ops_live_sources_only() -> None:
    for source_table in (
        "mart.champion_model_input_1h",
        "mart.champion_prediction_1h",
        "qa.champion_prediction_issue",
        "ops.champion_inference_metric",
        "live.measurement_1h",
        "mart.import_pmax_forecast_15min",
        "ops.import_pmax_inference_log",
        "qa.import_pmax_forecast_evaluation",
    ):
        assert DashboardPanelContract("allowed", source_table, "contract test").source_table == source_table

    for source_table in (
        "reference.corrected_resampled",
        "public.metric",
        "mart.bad;drop",
        "ops.bad table",
        "live.missing_extra.part",
    ):
        with pytest.raises(ValueError, match="unsupported dashboard source table"):
            DashboardPanelContract("blocked", source_table, "contract test")
