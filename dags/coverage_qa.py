"""Airflow DAG for source-vs-live coverage QA.

Scheduled runs are dry-run by default. Set DAG run config
``{"execute_write": true}`` for reviewed active ``qa.live_issue`` writes.
Historical repaired gaps remain audit evidence files; only currently observed
source-vs-live gaps should become active QA rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.param import Param
from airflow.operators.bash import BashOperator

DAG_ID = "coverage_qa"
WORKER = "/workspace/scripts/ops/coverage_worker.py"
SOURCE_ROOT = "/workspace/data/live_source/day_cache"
OUTPUT_ROOT = "/opt/airflow/logs/coverage_qa"

with DAG(
    dag_id=DAG_ID,
    description="Compare source day-cache coverage with live DB and record active qa.live_issue rows when explicitly enabled.",
    schedule="*/15 * * * *",
    start_date=datetime(2023, 12, 1),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=20),
    params={
        "execute_write": Param(False, type="boolean"),
        "window_start": Param("", type="string"),
        "window_end": Param("", type="string"),
    },
    tags=["cms", "qa", "coverage", "source-live"],
) as dag:
    coverage_qa = BashOperator(
        task_id="source_live_coverage_check",
        bash_command="""
        set -euo pipefail
        WINDOW_START="{{ dag_run.conf.get('window_start') or data_interval_start.isoformat() }}"
        WINDOW_END="{{ dag_run.conf.get('window_end') or data_interval_end.isoformat() }}"
        OUTPUT_DIR="{{ dag_run.conf.get('output_dir') or '' }}"
        if [ -z "$OUTPUT_DIR" ]; then
          OUTPUT_DIR="%s/{{ dag_run.run_id | replace(':', '_') | replace('/', '_') }}"
        fi
        WRITE_FLAG=""
        if [ "{{ '1' if (dag_run.conf.get('execute_write') or params.execute_write) else '0' }}" = "1" ]; then
          WRITE_FLAG="--execute-write"
        fi
        python %s \
          --source-root %s \
          --window-start "$WINDOW_START" \
          --window-end "$WINDOW_END" \
          --output-dir "$OUTPUT_DIR" \
          --db-event-chunk-minutes 15 \
          --db-rollup-chunk-minutes 30 \
          $WRITE_FLAG
        """ % (OUTPUT_ROOT, WORKER, SOURCE_ROOT),
        env={
            "PYTHONPATH": "/workspace/src",
        },
        append_env=True,
    )

__all__ = ["dag"]
