import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# backend/src를 추가하여 애플리케이션의 절대 import를 그대로 사용한다.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from api.main import app

@pytest.fixture
def client():
    """FastAPI TestClient 픽스처"""
    return TestClient(app)

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """테스트 실행 시 필요한 환경 변수 강제 주입 (API 키 유출 및 오작동 방지)"""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-test")
    # 실제 DB에 붙지 않도록 더미 설정 (라우터 에러 핸들링 테스트용)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/dummy")
