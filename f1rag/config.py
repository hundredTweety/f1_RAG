"""설정과 환경변수 로딩.

- 근거 품질에 영향을 주는 값(청크 크기, Top-k, 검색 방식 등)은 config/settings.yaml 에 둔다.
- API 키는 환경변수(.env)로만 받는다.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"

load_dotenv(ROOT / ".env")


class Settings:
    """settings.yaml 을 점 표기로 읽는 얇은 래퍼. 예: settings.get("retrieval.top_k")"""

    def __init__(self, data: dict[str, Any], source: Path | None = None):
        self._data = data
        self.source = source

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def path(self, dotted: str) -> Path:
        """paths.* 값을 프로젝트 루트 기준 절대 경로로 돌려준다."""
        value = Path(self.get(f"paths.{dotted}"))
        return value if value.is_absolute() else ROOT / value

    def with_overrides(self, overrides: dict[str, Any]) -> "Settings":
        """검증 스크립트에서 값만 바꿔 비교할 때 사용. 원본은 바뀌지 않는다."""
        data = copy.deepcopy(self._data)
        for dotted, value in overrides.items():
            node = data
            keys = dotted.split(".")
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = value
        return Settings(data, self.source)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.getenv("F1RAG_SETTINGS") or DEFAULT_SETTINGS)
    with open(path, encoding="utf-8") as f:
        return Settings(yaml.safe_load(f) or {}, path)


# ---------------------------------------------------------------- 환경변수


def openai_api_key() -> str | None:
    """OPENAI_API_KEY, 없으면 SSAFY GMS 안내의 이름인 GMS_KEY."""
    return os.getenv("OPENAI_API_KEY") or os.getenv("GMS_KEY") or None


def openai_base_url() -> str | None:
    return os.getenv("OPENAI_BASE_URL") or None


def wiki_contact() -> str | None:
    return os.getenv("WIKI_CONTACT") or None


def fake_models_enabled() -> bool:
    return os.getenv("F1RAG_FAKE_MODELS", "").strip().lower() in {"1", "true", "yes"}
