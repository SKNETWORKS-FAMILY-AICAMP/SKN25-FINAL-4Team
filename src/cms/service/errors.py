"""에러 처리 헬퍼 — 내부 예외는 서버 로그로, 클라이언트엔 일반 메시지만.

raw 예외 문자열(스택·내부 경로·SQL 등)이 UI에 노출되는 것을 막는다.
"""
import logging
import sys

logger = logging.getLogger("ems")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] ems: %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_DEFAULT_MSG = "요청 처리 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."


def safe_err(e: Exception, user_msg: str = _DEFAULT_MSG) -> str:
    """전체 예외를 스택과 함께 로깅하고, 사용자용 일반 메시지를 반환."""
    logger.error("처리된 예외: %s", e, exc_info=True)
    return user_msg
