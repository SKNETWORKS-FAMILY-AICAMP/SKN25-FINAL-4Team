"""
LLM 클라이언트 추상화 레이어.

.env의 LLM_PROVIDER / LLM_MODEL 만 바꾸면 모델 전환 완료.

지원 프로바이더:
  openai    — OpenAI GPT 시리즈   (OPENAI_API_KEY)
  anthropic — Anthropic Claude    (ANTHROPIC_API_KEY)
  gemini    — Google Gemini       (GEMINI_API_KEY, OpenAI 호환 엔드포인트 사용)

예시 .env:
  LLM_PROVIDER=openai
  LLM_MODEL=gpt-4o
  OPENAI_API_KEY=sk-...
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_MODEL       = os.getenv("LLM_MODEL", "gpt-4o")
LLM_MODEL_FAST  = os.getenv("LLM_MODEL_FAST", LLM_MODEL)  # 미설정 시 기본 모델과 동일

_cache: dict = {}   # 프로바이더별 클라이언트 캐시


def _get_client():
    if LLM_PROVIDER in _cache:
        return _cache[LLM_PROVIDER]

    if LLM_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    elif LLM_PROVIDER == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    elif LLM_PROVIDER == "gemini":
        from openai import OpenAI  # Gemini는 OpenAI 호환 API 제공
        client = OpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    elif LLM_PROVIDER == "ollama":
        from openai import OpenAI  # Ollama도 OpenAI 호환 API(/v1) 제공
        client = OpenAI(
            base_url=os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
            api_key="ollama",  # 더미 (Ollama는 키 불필요)
        )

    else:
        raise ValueError(
            f"지원하지 않는 LLM_PROVIDER: '{LLM_PROVIDER}'. "
            "openai / anthropic / gemini / ollama 중 하나를 사용하세요."
        )

    _cache[LLM_PROVIDER] = client
    return client


def reload():
    """설정 변경 후 전역 변수 및 클라이언트 캐시 재초기화."""
    global LLM_PROVIDER, LLM_MODEL, LLM_MODEL_FAST, _cache
    _cache.clear()
    LLM_PROVIDER   = os.getenv("LLM_PROVIDER", "openai").lower()
    LLM_MODEL      = os.getenv("LLM_MODEL", "gpt-4o")
    LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", LLM_MODEL)


def _ollama_base_url() -> str:
    """OLLAMA_URL에서 /v1 접미사를 제거해 네이티브 API 베이스 URL 반환."""
    url = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
    return url.rstrip("/").removesuffix("/v1")


def chat(messages: list[dict], max_tokens: int = 1024, fast: bool = False, thinking: bool | None = None) -> str:
    """통합 LLM 호출 — 텍스트 응답만 반환.

    fast=True           → EXAONE: 의도 분류·단순 쿼리용, thinking 자동 OFF
    fast=False          → Gemma4: 진단·보고서·분석용, thinking 자동 ON
    thinking=False 명시 → Gemma4를 thinking 없이 호출 (~3s, RAG 단순 설명용)
    """
    model = LLM_MODEL_FAST if fast else LLM_MODEL

    if LLM_PROVIDER == "ollama":
        num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
        if thinking is None:
            thinking = not fast  # 기본값: quality → ON, fast → OFF
        num_predict = max_tokens * 4 if thinking else max_tokens
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,  # 모델 영구 적재 — 재요청 시 로딩 대기 없음
            "think": thinking,
            "options": {
                "num_predict": num_predict,
                "num_ctx": num_ctx,
                "temperature": 0.1 if fast else 0.3,
            },
        }
        resp = httpx.post(
            f"{_ollama_base_url()}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        msg = resp.json()["message"]
        return msg.get("content") or msg.get("thinking", "")

    client = _get_client()

    if LLM_PROVIDER == "anthropic":
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.content[0].text

    else:   # openai / gemini (OpenAI 호환)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.choices[0].message.content
