"""
일일 보고서 자동 생성 스케줄러.

매일 정해진 시각(기본 06:00)에 ems 데이터의 '가장 최근 완전한 날짜'를 찾아
일일 보고서를 생성한다. 과거 이력 데이터에서는 마지막 날짜가 고정이지만,
실시간 데이터가 유입되면 자연히 '어제' 보고서가 매일 생성된다.

환경변수:
  DAILY_REPORT_ENABLED  : "false"면 스케줄러 비활성화 (기본 활성)
  DAILY_REPORT_HOUR     : 실행 시각(시), 기본 6
  DAILY_REPORT_MINUTE   : 실행 시각(분), 기본 0
"""

import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler: BackgroundScheduler | None = None
_last_run: dict = {"date": None, "status": "대기 중", "at": None}


def _job():
    """스케줄 실행: 최신 데이터 날짜의 일일 보고서를 생성."""
    from datetime import datetime

    from api.routers.report import build_daily_report, latest_data_date

    target = latest_data_date()
    if not target:
        _last_run.update(status="실패 — 데이터 날짜 확인 불가", at=datetime.now().isoformat())
        print("[Scheduler] 최신 데이터 날짜를 찾을 수 없음")
        return

    print(f"[Scheduler] 일일 보고서 생성 시작: {target}")
    try:
        result = build_daily_report(target, generated_by="scheduler")
        if result is None:
            _last_run.update(date=target, status="실패 — 데이터 없음", at=datetime.now().isoformat())
        else:
            _last_run.update(date=target, status="성공", at=datetime.now().isoformat())
            print(f"[Scheduler] 일일 보고서 생성 완료: {target}")
    except Exception as e:
        _last_run.update(date=target, status=f"오류: {e}", at=datetime.now().isoformat())
        print(f"[Scheduler] 일일 보고서 생성 오류: {e}")


def start_scheduler():
    global _scheduler
    if os.getenv("DAILY_REPORT_ENABLED", "true").lower() == "false":
        print("[Scheduler] 비활성화됨 (DAILY_REPORT_ENABLED=false)")
        return
    if _scheduler is not None:
        return

    hour   = int(os.getenv("DAILY_REPORT_HOUR", "6"))
    minute = int(os.getenv("DAILY_REPORT_MINUTE", "0"))

    _scheduler = BackgroundScheduler(timezone="Europe/Berlin")
    _scheduler.add_job(
        _job,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="daily_report",
        replace_existing=True,
    )
    _scheduler.start()
    print(f"[Scheduler] 일일 보고서 스케줄러 시작 — 매일 {hour:02d}:{minute:02d} (Europe/Berlin)")


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        print("[Scheduler] 종료됨")


def scheduler_status() -> dict:
    """스케줄러 상태 + 다음 실행 시각 + 마지막 실행 결과."""
    enabled = _scheduler is not None
    next_run = None
    if enabled:
        job = _scheduler.get_job("daily_report")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    return {
        "enabled":  enabled,
        "next_run": next_run,
        "last_run": _last_run,
    }
