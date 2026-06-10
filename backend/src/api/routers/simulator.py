"""
시뮬레이션 시계 & 실시간 모니터링 워커.

가상 시계를 2023-01-01부터 가속(1×/6×/24×)으로 진행시켜
'실시간 데이터 유입'을 모사한다. 워커가 매 tick 마다 새 시간 데이터의
이상탐지를 실행하고, HIGH/CRITICAL 발견 시 자동 broadcast 알림 전송.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from fastapi import APIRouter, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

router = APIRouter(prefix="/simulator", tags=["simulator"])

# ── 가상 시계 ────────────────────────────────────────────────────

SIM_START_DEFAULT = datetime(2023, 1, 1, 0, 0, 0)


class SimulationClock:
    """
    실시간 흐름을 가상 시각(sim_now)으로 변환.
      sim_now = sim_start + (real_elapsed * speed) seconds

    speed=3600 → 1초(real)당 1시간(sim) 진행
    """
    def __init__(self):
        self._lock = RLock()
        self.reset()

    def reset(self, sim_start: datetime | None = None, speed: int = 3600):
        with self._lock:
            self.sim_start_anchor = sim_start or SIM_START_DEFAULT
            self.real_start_anchor = datetime.utcnow()
            self.speed             = speed
            self.running           = False
            self.last_checked      = self.sim_start_anchor

    def start(self):
        with self._lock:
            if self.running:
                return
            # 일시정지된 시점부터 재개 — anchor를 현재 sim_now로 갱신
            self.sim_start_anchor  = self._now_unlocked()
            self.real_start_anchor = datetime.utcnow()
            self.running           = True

    def pause(self):
        with self._lock:
            if not self.running:
                return
            self.sim_start_anchor = self._now_unlocked()
            self.running          = False

    def set_speed(self, speed: int):
        with self._lock:
            # 새 속도 적용 — 현재 sim_now를 새 anchor로
            self.sim_start_anchor  = self._now_unlocked()
            self.real_start_anchor = datetime.utcnow()
            self.speed             = speed

    def seek(self, to: datetime):
        with self._lock:
            self.sim_start_anchor  = to
            self.real_start_anchor = datetime.utcnow()
            self.last_checked      = to

    def _now_unlocked(self) -> datetime:
        if not self.running:
            return self.sim_start_anchor
        elapsed_real = (datetime.utcnow() - self.real_start_anchor).total_seconds()
        return self.sim_start_anchor + timedelta(seconds=elapsed_real * self.speed)

    @property
    def now(self) -> datetime:
        with self._lock:
            return self._now_unlocked()

    def status(self) -> dict:
        with self._lock:
            return {
                "now":          self._now_unlocked().isoformat(),
                "running":      self.running,
                "speed":        self.speed,
                "sim_start":    self.sim_start_anchor.isoformat(),
                "last_checked": self.last_checked.isoformat(),
            }


clock = SimulationClock()


def effective_now() -> datetime:
    """
    '지금'으로 간주할 시각 = 시뮬레이터 재생 헤드(clock.now).

    데모는 2023년(테스트 연도)을 실시간 재생하는 구조이므로,
    재생 헤드가 항상 '지금'이다 (idle이면 시작점 2023-01-01).
    데이터가 없는 미래 시각은 호출부에서 latest로 클램프한다(cms 등).
    """
    return clock.now


# ── 모니터링 워커 ─────────────────────────────────────────────────

_WORKER_TICK_SECONDS = 5    # 워커가 매 5초마다 새 시간 데이터 검사
_worker_task: asyncio.Task | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_stats = {"checks": 0, "anomalies_found": 0, "last_check_at": None, "last_anomaly_at": None}


async def _monitor_loop():
    """매 _WORKER_TICK_SECONDS 마다 last_checked ~ sim_now 사이 데이터 이상탐지."""
    print(f"[Simulator] monitor worker started (tick={_WORKER_TICK_SECONDS}s)")
    while True:
        try:
            await asyncio.sleep(_WORKER_TICK_SECONDS)
            if not clock.running:
                continue
            await _do_check()
        except asyncio.CancelledError:
            print("[Simulator] monitor worker cancelled")
            raise
        except Exception as e:
            print(f"[Simulator] monitor error: {e}")


async def _do_check():
    """현재 시뮬 시각까지 새로 들어온 데이터 검사."""
    sim_now      = clock.now
    last_checked = clock.last_checked

    # 1시간 미만이면 스킵 (1시간 단위 데이터)
    if (sim_now - last_checked).total_seconds() < 3600:
        return

    # 너무 큰 점프(24시간 초과)면 잘라서 처리 — 대량 broadcast 방지
    window_end = min(sim_now, last_checked + timedelta(hours=24))

    # 일 단위가 바뀌면 어제 일일 보고서 자동 생성 (시뮬에서 매일 06:00 자동 생성 모사)
    if last_checked.date() != window_end.date():
        try:
            loop_ = asyncio.get_running_loop()
            target_date = (window_end - timedelta(days=1)).date().isoformat()
            await loop_.run_in_executor(None, _ensure_daily_report, target_date)
        except Exception as e:
            print(f"[Simulator] auto daily report failed: {e}")

        # 같은 타이밍에 권고 outcome 평가 → 회고 토스트 발송
        try:
            from api.routers.control import evaluate_pending_outcomes
            evaluated = await asyncio.get_running_loop().run_in_executor(
                None, evaluate_pending_outcomes, window_end
            )
            if evaluated:
                from api.routers.notifications import manager as notification_manager
                for ev in evaluated:
                    o = ev["outcome"]
                    if o["label"] == "성공":
                        icon, level = "🎉", "INFO"
                    elif o["label"] == "실패":
                        icon, level = "⚠️", "WARNING"
                    else:
                        continue   # 중립은 알림 안 함
                    await notification_manager.broadcast({
                        "type":    "alert",
                        "level":   level,
                        "message": f"{icon} [AI 학습] {ev['title']}\n{o['note']}",
                    })
        except Exception as e:
            print(f"[Simulator] outcome evaluation failed: {e}")

    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, _check_window_sync, last_checked, window_end
        )
        _worker_stats["checks"] += 1
        _worker_stats["last_check_at"] = datetime.utcnow().isoformat()

        if result and result.get("anomalies"):
            from api.routers.notifications import manager as notification_manager
            for a in result["anomalies"]:
                msg = {
                    "type":    "alert",
                    "level":   a["level"],
                    "message": a["message"],
                }
                await notification_manager.broadcast(msg)
                _worker_stats["anomalies_found"] += 1
                _worker_stats["last_anomaly_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        print(f"[Simulator] _do_check error: {e}")
    finally:
        clock.last_checked = window_end


# 잔차 모델은 seq_len(수십 시간) 이상의 윈도우가 있어야 안정 동작.
# 워커가 주는 윈도우(≤24h)가 좁으면 IF 피처 슬라이싱이 어긋나 0건이 되므로
# 최소 탐지폭을 확보하고, 새 구간(last_checked 이후)만 알림 대상으로 한다.
_MIN_DETECT_WINDOW = timedelta(days=3)


def _check_window_sync(start: datetime, end: datetime) -> dict:
    """동기 윈도우 검사 — executor에서 실행. start 이후 새 이상만 반환."""
    import pandas as pd
    from data.loader import load_range

    # 모델 안정 동작을 위해 탐지 시작점을 최소 3일 앞으로 확장
    detect_start = min(start, end - _MIN_DETECT_WINDOW)

    start_str = detect_start.strftime("%Y-%m-%dT%H:%M:%S")
    end_str   = end.strftime("%Y-%m-%dT%H:%M:%S")

    # 잔차 모델은 lookback 필요 (400시간)
    df_start = (detect_start - timedelta(hours=400)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        df = load_range(df_start, end_str)
    except Exception as e:
        print(f"[Simulator] load_range failed: {e}")
        return {"anomalies": []}

    if df.empty:
        return {"anomalies": []}

    anomalies = []
    try:
        from models.anomaly.residual_model import predict_anomaly, is_available
        if is_available():
            res = predict_anomaly(df, start_str, end_str)
            if not res.empty:
                res = res.copy()
                res["_ts"] = pd.to_datetime(res["ts"], utc=True)
                # 새 구간(start 이후)의 HIGH/CRITICAL만 — 이전 검사분 재알림 방지
                new_cut = pd.Timestamp(start, tz="UTC")
                try:
                    from api.routers.settings import get_alert_min_level
                    min_lvl = get_alert_min_level()
                    alert_levels = ["CRITICAL"] if min_lvl == "CRITICAL" else ["HIGH", "CRITICAL"]
                except Exception:
                    alert_levels = ["HIGH", "CRITICAL"]
                high = res[
                    res["anomaly_level"].isin(alert_levels) &
                    (res["_ts"] > new_cut)
                ]
                for _, r in high.iterrows():
                    ts = str(r["ts"])[:16]
                    anomalies.append({
                        "level":   r["anomaly_level"],
                        "message": (
                            f"🚨 [{ts}] 이상 감지 — "
                            f"잔차 {abs(r['residual_w']) / 1000:.0f} kW. "
                            f"상세 원인을 분석할까요?"
                        ),
                    })
                # anomaly_results 테이블에도 저장 (새 구간만)
                _persist_residual_results(high.drop(columns=["_ts"]))
    except Exception as e:
        print(f"[Simulator] residual model error: {e}")

    return {"anomalies": anomalies}


def _ensure_daily_report(date_str: str) -> None:
    """
    해당 날짜 일일 보고서가 없으면 KPI/프로파일만 빠르게 생성 (시뮬 워커용).
    AI 요약은 사용자가 대시보드에서 명시적으로 조회할 때만 생성 (비용 절감).
    """
    try:
        from api.routers.report import _fetch_daily, build_daily_report
        from api.db import get_conn
        with get_conn() as conn:
            existing = _fetch_daily(conn, date_str)
        if existing:
            return
        print(f"[Simulator] auto-generating daily report (no AI) for {date_str}")
        build_daily_report(date_str, generated_by="simulator", skip_ai=True)
    except Exception as e:
        print(f"[Simulator] ensure_daily failed for {date_str}: {e}")


def _persist_residual_results(df) -> None:
    """잔차 모델 결과를 anomaly_results 테이블에 저장."""
    try:
        from api.db import get_conn
        import psycopg2
        from psycopg2.errors import UniqueViolation
        with get_conn() as conn:
            cur = conn.cursor()
            for _, r in df.iterrows():
                try:
                    cur.execute("""
                        INSERT INTO anomaly_results
                            (timestamp, meter_id, anomaly_type, severity, description,
                             vote_count, actual_w, predicted_w, residual_w, gateway_failure)
                        VALUES (%s, 'grid_P', 'ResidualSpike', %s, %s, %s, %s, %s, %s, FALSE)
                        ON CONFLICT DO NOTHING;
                    """, (
                        r["ts"].to_pydatetime() if hasattr(r["ts"], "to_pydatetime") else r["ts"],
                        r["anomaly_level"],
                        f"잔차 {abs(r['residual_w']) / 1000:.1f} kW (res_flag={int(r['res_flag'])}, if_flag={int(r['if_flag'])})",
                        int(r["vote"]),
                        float(r["actual_w"]),
                        float(r["predicted_w"]),
                        float(r["residual_w"]),
                    ))
                except Exception:
                    pass
            conn.commit()
    except Exception as e:
        print(f"[Simulator] persist failed: {e}")


def start_worker(loop: asyncio.AbstractEventLoop):
    """app startup 시 호출 — 모니터링 워커 실행."""
    global _worker_task, _worker_loop
    if _worker_task is not None:
        return
    _worker_loop = loop
    _worker_task = loop.create_task(_monitor_loop())


def stop_worker():
    """app shutdown 시 호출."""
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        _worker_task = None


# ── API 엔드포인트 ────────────────────────────────────────────────

@router.get("/status")
def status():
    """현재 시뮬 시계 상태 + 워커 통계."""
    return {**clock.status(), "worker": _worker_stats}


@router.post("/start")
def start():
    clock.start()
    return clock.status()


@router.post("/pause")
def pause():
    clock.pause()
    return clock.status()


@router.post("/reset")
def reset():
    clock.reset()
    return clock.status()


@router.post("/speed")
def set_speed(value: int = Query(..., description="실시간 1초당 시뮬 진행 초 수 (3600=1×, 21600=6×, 86400=24×)")):
    clock.set_speed(value)
    return clock.status()


@router.post("/seek")
def seek(to: str = Query(..., description="ISO 날짜 예: 2023-06-15T00:00:00")):
    try:
        target = datetime.fromisoformat(to.replace("Z", ""))
    except ValueError:
        return {"error": "Invalid ISO datetime"}
    clock.seek(target)
    return clock.status()
