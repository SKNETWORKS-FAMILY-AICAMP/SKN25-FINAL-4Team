"""
FastAPI 백그라운드 자동 스케줄러.
매월 1일 자정에 v84 앙상블 모델 재학습(train.py)을 서브프로세스로 자동 실행.
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# 프로젝트 루트 경로 추가
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

scheduler = BackgroundScheduler()


def trigger_ml_retraining():
    """백그라운드에서 python -m ml.pipeline.train 실행"""
    print(f"[{datetime.now()}] [Scheduler] 자동 ML 재학습(1h/3h) 크론 잡이 시작되었습니다.")
    
    # 런팟 또는 로컬 가상환경 파이썬 진입점 찾기
    venv_python = ROOT / "backend" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        # 폴백
        venv_python = "python"
    
    log_dir = ROOT / "backend" / "src" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / f"auto_train_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    try:
        # 1시간 예측 및 3시간 예측 모델 순차적 백그라운드 학습 트리거
        cmd_1h = [str(venv_python), "-m", "ml.pipeline.train", "--horizon", "1"]
        cmd_3h = [str(venv_python), "-m", "ml.pipeline.train", "--horizon", "3"]
        
        print(f"[Scheduler] 1h 모델 학습 커맨드: {' '.join(cmd_1h)}")
        print(f"[Scheduler] 3h 모델 학습 커맨드: {' '.join(cmd_3h)}")
        
        # 로그 파일 스트림 오픈
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            log_file.write(f"=== Auto Retraining Start at {datetime.now()} ===\n")
            log_file.flush()
            
            # Non-blocking 실행을 위해 서브프로세스로 비동기 트리거
            # 1h 완료 후 3h 순차 실행을 셸 스크립트 결합식으로 실행
            combined_cmd = f"{venv_python} -m ml.pipeline.train --horizon 1 && {venv_python} -m ml.pipeline.train --horizon 3"
            
            process = subprocess.Popen(
                combined_cmd,
                shell=True,
                cwd=str(ROOT / "backend"),
                stdout=log_file,
                stderr=subprocess.STDOUT
            )
            print(f"[Scheduler] 학습 프로세스 트리거 성공 (PID: {process.pid}), 로그: {log_file_path}")
            
    except Exception as e:
        print(f"[Scheduler] ML 재학습 프로세스 실행 중 오류 발생: {e}")


def start_scheduler():
    """FastAPI startup_event에서 호출하여 스케줄러 기동"""
    if not scheduler.running:
        # 매월 1일 00시 00분에 주기적으로 작동
        trigger = CronTrigger(day="1", hour="0", minute="0")
        
        scheduler.add_job(
            trigger_ml_retraining,
            trigger=trigger,
            id="ml_retraining_job",
            name="Monthly ML Retraining Job",
            replace_existing=True
        )
        
        scheduler.start()
        print(f"[{datetime.now()}] [Scheduler] 백그라운드 크론 스케줄러가 성공적으로 시작되었습니다. (매월 1일 자정 실행)")


def stop_scheduler():
    """FastAPI shutdown_event에서 호출하여 안전하게 해제"""
    if scheduler.running:
        scheduler.shutdown()
        print(f"[{datetime.now()}] [Scheduler] 백그라운드 크론 스케줄러가 중지되었습니다.")


def scheduler_status() -> dict:
    """스케줄러 상태 반환 — /report/daily/scheduler 엔드포인트용."""
    if not scheduler.running:
        return {"enabled": False, "next_run": None, "job_id": None}
    jobs = scheduler.get_jobs()
    job = next((j for j in jobs), None)
    return {
        "enabled": True,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "job_id": job.id if job else None,
        "job_name": job.name if job else None,
    }
