"""
인증 API (세션 토큰 기반, 프로토타입).

POST /auth/login   — 이메일+비밀번호 로그인 → 토큰 발급
GET  /auth/me      — 토큰으로 현재 사용자 조회
POST /auth/logout  — 토큰 무효화

토큰은 in-memory 세션 스토어에 보관한다(재시작 시 초기화).
운영 전환 시 JWT + 영속 세션 + 전 엔드포인트 인가로 확장 필요.
"""
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cms.service.routers import users

router = APIRouter(prefix="/auth", tags=["auth"])

# token → 공개 사용자 정보(dict)
_sessions: dict[str, dict] = {}

_PUBLIC_FIELDS = ("id", "name", "email", "role")


def _public(user: dict) -> dict:
    return {k: user[k] for k in _PUBLIC_FIELDS}


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Authorization: Bearer <token> 헤더를 검증하는 의존성.

    보호가 필요한 엔드포인트에서 Depends(current_user)로 사용. (운영 확장용)
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    token = authorization.split(" ", 1)[1].strip()
    user = _sessions.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="세션이 만료되었거나 유효하지 않습니다.")
    return user


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginRequest):
    user = users.verify_credentials(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다. 관리자에게 문의하세요.")
    token = secrets.token_urlsafe(32)
    pub = _public(user)
    _sessions[token] = pub
    users.touch_last_login(user)
    return {"token": token, "user": pub}


@router.get("/me")
def me(authorization: Optional[str] = Header(None)):
    return current_user(authorization)


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        _sessions.pop(authorization.split(" ", 1)[1].strip(), None)
    return {"ok": True}
