"""공유 DB 커넥션 풀 — 모든 라우터가 이 모듈을 통해 커넥션을 획득한다."""
import os
import time
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2 import pool

load_dotenv()

_pool: pool.ThreadedConnectionPool | None = None


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            _pool = pool.ThreadedConnectionPool(2, 25, db_url)
        else:
            # DATABASE_URL 없을 때 개별 변수로 연결 (비밀번호 특수문자 자동 처리)
            _pool = pool.ThreadedConnectionPool(
                2, 25,
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "5432")),
                dbname=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
            )
    return _pool


@contextmanager
def get_conn():
    """커넥션을 컨텍스트 매니저로 제공. 종료 시 자동 반환."""
    p = _get_pool()
    conn = None
    for _ in range(10):
        try:
            conn = p.getconn()
            break
        except pool.PoolError:
            time.sleep(0.2)
    if conn is None:
        raise RuntimeError("connection pool exhausted")
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    else:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        p.putconn(conn)
