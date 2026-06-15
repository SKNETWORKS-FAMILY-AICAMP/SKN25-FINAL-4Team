"""Airflow wrapper for the manual paused CMS champion 1h DAG."""

from __future__ import annotations

from cms.workflow.champion_airflow_skeleton import make_airflow_dag

# Registered for manual inspection only: schedule=None, paused on creation, and
# all runtime writes remain disabled by the underlying task contracts.
dag = make_airflow_dag(enabled=True)

__all__ = ["dag"]
