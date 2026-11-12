"""로그 설정. 오류 상세는 화면 대신 logs/app.log 에만 남긴다."""

from __future__ import annotations

import logging
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("f1rag")
    root.setLevel(level)
    root.addHandler(handler)
    _configured = True


def log_exception(logger: logging.Logger, context: str) -> str:
    """예외를 로그에 남기고, 화면에 보여 줄 짧은 오류 번호를 돌려준다."""
    error_id = uuid.uuid4().hex[:8]
    logger.exception("[%s] %s", error_id, context)
    return error_id
