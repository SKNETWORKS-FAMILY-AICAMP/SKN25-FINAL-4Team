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


class CreateUser(BaseModel):
    name: str
    email: str
    role: str = "viewer"


class PatchUser(BaseModel):
    name:   Optional[str] = None
    role:   Optional[str] = None
    status: Optional[str] = None


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
    return user


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
    before = len(_users)
    _users = [u for u in _users if u["id"] != user_id]
    if len(_users) == before:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
