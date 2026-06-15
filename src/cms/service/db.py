"""공유 DB 커넥션 풀 — 모든 라우터가 이 모듈을 통해 커넥션을 획득한다."""
import os
import time
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2 import pool, OperationalError

load_dotenv()

_pool: pool.ThreadedConnectionPool | None = None


def _make_pool() -> pool.ThreadedConnectionPool:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return pool.ThreadedConnectionPool(2, 25, db_url)
    return pool.ThreadedConnectionPool(
        2, 25,
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = _make_pool()
    return _pool


def _reset_pool():
    global _pool
    try:
        if _pool:
            _pool.closeall()
    except Exception:
        pass
    _pool = _make_pool()


def _get_valid_conn(p: pool.ThreadedConnectionPool):
    """풀에서 살아있는 커넥션을 반환. 죽은 커넥션은 폐기하고 재시도."""
    for _ in range(p.maxconn):
        conn = p.getconn()
        try:
            conn.cursor().execute("SELECT 1")
            conn.rollback()
            return conn
        except OperationalError:
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
    raise OperationalError("pool에 유효한 커넥션 없음")


@contextmanager
def get_conn():
    """커넥션을 컨텍스트 매니저로 제공. 종료 시 자동 반환."""
    p = _get_pool()
    conn = None
    for attempt in range(10):
        try:
            conn = _get_valid_conn(p)
            break
        except (pool.PoolError, OperationalError):
            if attempt == 5:
                _reset_pool()
                p = _get_pool()
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
        try:
            p.putconn(conn)
        except Exception:
            pass
