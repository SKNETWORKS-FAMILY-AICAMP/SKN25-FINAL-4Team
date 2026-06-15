"""
사용자 관리 API (in-memory, 데모용).

GET    /users        — 전체 사용자 목록
POST   /users        — 사용자 추가
PATCH  /users/{id}   — 정보 수정 (이름 / 역할 / 상태)
DELETE /users/{id}   — 삭제
"""
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["users"])

_users: list[dict] = [
    {
        "id": "1",
        "name": "공장장",
        "email": "admin@honda-rd.eu",
        "role": "admin",
        "status": "active",
        "created_at": "2023-01-01",
        "last_login": "2024-03-15T09:23",
    },
    {
        "id": "2",
        "name": "운영팀 A",
        "email": "op_team_a@honda-rd.eu",
        "role": "operator",
        "status": "active",
        "created_at": "2023-03-15",
        "last_login": "2024-03-14T16:42",
    },
    {
        "id": "3",
        "name": "설비 담당자",
        "email": "maintenance@honda-rd.eu",
        "role": "operator",
        "status": "active",
        "created_at": "2023-06-01",
        "last_login": "2024-03-13T11:05",
    },
    {
        "id": "4",
        "name": "데이터 분석가",
        "email": "analyst@honda-rd.eu",
        "role": "viewer",
        "status": "inactive",
        "created_at": "2023-09-20",
        "last_login": "2024-01-08T14:30",
    },
]

# ── 데모 계정 비밀번호 ────────────────────────────────────────────────
# 프로토타입용 평문 저장. 운영 전환 시 bcrypt 등 해시 저장 필요.
_DEFAULT_PASSWORD = "honda123"
_passwords: dict[str, str] = {
    "admin@honda-rd.eu":       "admin123",
    "op_team_a@honda-rd.eu":   "operator123",
    "maintenance@honda-rd.eu": "operator123",
    "analyst@honda-rd.eu":     "viewer123",
}


def find_by_email(email: str) -> Optional[dict]:
    return next((u for u in _users if u["email"].lower() == (email or "").lower()), None)


def verify_credentials(email: str, password: str) -> Optional[dict]:
    """이메일+비밀번호 검증. 성공 시 사용자 dict, 실패 시 None."""
    user = find_by_email(email)
    if not user:
        return None
    expected = _passwords.get(user["email"], _DEFAULT_PASSWORD)
    return user if password == expected else None


def touch_last_login(user: dict) -> None:
    user["last_login"] = __import__("datetime").datetime.utcnow().isoformat(timespec="minutes")


class CreateUser(BaseModel):
    name: str
    email: str
    role: str = "viewer"
    password: Optional[str] = None   # 미지정 시 기본 비밀번호 부여


class PatchUser(BaseModel):
    name:   Optional[str] = None
    role:   Optional[str] = None
    status: Optional[str] = None


class PasswordReset(BaseModel):
    password: str


@router.get("")
def list_users():
    return _users


@router.post("", status_code=201)
def create_user(body: CreateUser):
    if any(u["email"] == body.email for u in _users):
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.")
    user = {
        "id":         str(uuid.uuid4())[:8],
        "name":       body.name,
        "email":      body.email,
        "role":       body.role,
        "status":     "active",
        "created_at": __import__("datetime").date.today().isoformat(),
        "last_login": None,
    }
    _users.append(user)
    _passwords[body.email] = body.password or _DEFAULT_PASSWORD
    return user


@router.post("/{user_id}/password")
def reset_password(user_id: str, body: PasswordReset):
    user = next((u for u in _users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if not body.password or len(body.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다.")
    _passwords[user["email"]] = body.password
    return {"ok": True}


@router.patch("/{user_id}")
def update_user(user_id: str, body: PatchUser):
    user = next((u for u in _users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if body.name   is not None: user["name"]   = body.name
    if body.role   is not None: user["role"]   = body.role
    if body.status is not None: user["status"] = body.status
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str):
    global _users
    victim = next((u for u in _users if u["id"] == user_id), None)
    if not victim:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    _users = [u for u in _users if u["id"] != user_id]
    _passwords.pop(victim["email"], None)
