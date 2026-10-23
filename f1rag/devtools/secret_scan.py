"""소스코드·제출물에 API 키가 남아 있지 않은지 검사 (NF2003)."""

from __future__ import annotations

import os
import re
from pathlib import Path

PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),          # OpenAI 형식 키
    re.compile(r"lsv2_[A-Za-z0-9_]{20,}"),          # LangSmith 키
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
]
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "data", "logs", ".pytest_cache", "node_modules"}
TEXT_EXT = {".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".js", ".html", ".css", ".csv", ".example", ".cfg", ".ini"}


def scan(root: Path) -> list[str]:
    actual = os.getenv("OPENAI_API_KEY") or ""
    hits = []
    for path in Path(root).rglob("*"):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.name == ".env" or (path.suffix not in TEXT_EXT and path.name != ".env.example"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in PATTERNS:
            if pat.search(text):
                hits.append(f"{path.relative_to(root)}: {pat.pattern}")
        if actual and len(actual) > 8 and actual in text:
            hits.append(f"{path.relative_to(root)}: 실제 OPENAI_API_KEY 값")
    return hits
