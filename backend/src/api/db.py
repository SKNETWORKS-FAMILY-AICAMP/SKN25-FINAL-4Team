"""공유 DB 커넥션 풀 — 모든 라우터가 이 모듈을 통해 커넥션을 획득한다."""
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2 import pool

load_dotenv()

_pool: pool.ThreadedConnectionPool | None = None


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.ThreadedConnectionPool(2, 10, os.getenv("DATABASE_URL"))
    return _pool


@contextmanager
def get_conn():
    """커넥션을 컨텍스트 매니저로 제공. 종료 시 자동 반환."""
    p   = _get_pool()
    conn = p.getconn()
    try:
        yield conn
    finally:
        p.putconn(conn)
