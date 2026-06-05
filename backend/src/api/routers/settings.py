"""
시스템 설정 API.

GET  /settings          — 현재 설정 조회
POST /settings          — 설정 업데이트 (in-memory + LLM 클라이언트 재로드)
POST /settings/test-llm — LLM 연결 테스트 (저장 전 테스트 가능)
"""
import os
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

_config: dict = {
    "llm": {
        "provider":   os.getenv("LLM_PROVIDER", "openai"),
        "model":      os.getenv("LLM_MODEL", "gpt-4o"),
        "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
    },
    "profile": {
        "company_name": os.getenv("COMPANY_NAME", "Honda R&D Europe GmbH"),
    },
    "report_schedule": {
        "enabled": os.getenv("DAILY_REPORT_ENABLED", "true").lower() != "false",
        "hour":    int(os.getenv("DAILY_REPORT_HOUR", "6")),
        "minute":  int(os.getenv("DAILY_REPORT_MINUTE", "0")),
    },
    "alert": {
        "min_level": os.getenv("ALERT_MIN_LEVEL", "HIGH"),  # HIGH | CRITICAL
    },
}


def get_alert_min_level() -> str:
    """simulator 등 외부 모듈에서 임계값 읽기용."""
    return _config["alert"]["min_level"]


@router.get("")
def get_settings():
    return _config


class TestLlmRequest(BaseModel):
    provider:   Optional[str] = None
    model:      Optional[str] = None
    ollama_url: Optional[str] = None


@router.post("/test-llm")
def test_llm(body: TestLlmRequest = TestLlmRequest()):
    """LLM 연결 테스트. body 미제공 시 현재 저장된 설정 사용."""
    provider   = body.provider   or _config["llm"]["provider"]
    model      = body.model      or _config["llm"]["model"]
    ollama_url = body.ollama_url or _config["llm"]["ollama_url"]

    try:
        if provider == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            t0 = time.time()
            resp = client.messages.create(
                model=model, max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            return {"ok": True, "latency_ms": round((time.time() - t0) * 1000),
                    "response": resp.content[0].text[:50]}

        from openai import OpenAI
        if provider == "openai":
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        elif provider == "gemini":
            client = OpenAI(
                api_key=os.getenv("GEMINI_API_KEY"),
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        elif provider == "ollama":
            import httpx
            base = ollama_url.rstrip("/").removesuffix("/v1")
            t0 = time.time()
            r = httpx.post(
                f"{base}/api/chat",
                json={"model": model, "stream": False, "think": False,
                      "messages": [{"role": "user", "content": "ping"}],
                      "options": {"num_predict": 8}},
                timeout=30,
            )
            r.raise_for_status()
            return {"ok": True, "latency_ms": round((time.time() - t0) * 1000),
                    "response": r.json()["message"]["content"][:50]}
        else:
            return {"ok": False, "error": f"알 수 없는 프로바이더: {provider}"}

        t0 = time.time()
        resp = client.chat.completions.create(
            model=model, max_tokens=8,
            messages=[{"role": "user", "content": "ping"}],
        )
        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000),
                "response": (resp.choices[0].message.content or "")[:50]}

    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("")
def update_settings(body: dict):
    # ── LLM ─────────────────────────────────────
    if "llm" in body:
        llm = body["llm"]
        for key, env_key in [("provider", "LLM_PROVIDER"), ("model", "LLM_MODEL"), ("ollama_url", "OLLAMA_URL")]:
            if key in llm:
                _config["llm"][key] = llm[key]
                os.environ[env_key] = llm[key]
        try:
            from agents.llm_client import reload as llm_reload
            llm_reload()
        except Exception:
            pass

    # ── 기업 프로필 ──────────────────────────────
    if "profile" in body:
        _config["profile"].update(body["profile"])

    # ── 보고서 스케줄 ────────────────────────────
    if "report_schedule" in body:
        sched = body["report_schedule"]
        for k in ("enabled", "hour", "minute"):
            if k in sched:
                _config["report_schedule"][k] = sched[k]
        _apply_schedule()

    # ── 알림 임계값 ──────────────────────────────
    if "alert" in body and "min_level" in body["alert"]:
        _config["alert"]["min_level"] = body["alert"]["min_level"]

    return {"ok": True, "config": _config}


def _apply_schedule():
    """변경된 스케줄을 APScheduler에 실시간 반영."""
    try:
        from api.scheduler import _scheduler, start_scheduler, stop_scheduler
        from apscheduler.triggers.cron import CronTrigger

        enabled = _config["report_schedule"]["enabled"]
        hour    = _config["report_schedule"]["hour"]
        minute  = _config["report_schedule"]["minute"]

        if not enabled:
            stop_scheduler()
            return

        if _scheduler is None:
            os.environ["DAILY_REPORT_HOUR"]   = str(hour)
            os.environ["DAILY_REPORT_MINUTE"] = str(minute)
            start_scheduler()
        else:
            _scheduler.reschedule_job(
                "daily_report",
                trigger=CronTrigger(hour=hour, minute=minute),
            )
    except Exception as e:
        print(f"[Settings] schedule apply error: {e}")
