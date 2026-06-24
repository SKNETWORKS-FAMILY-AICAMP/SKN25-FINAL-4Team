"""Airflow wrapper for the scheduled CMS daily report DAG."""

from __future__ import annotations

from cms.workflow.daily_report_airflow import make_airflow_dag

dag = make_airflow_dag(enabled=True)

__all__ = ["dag"]
