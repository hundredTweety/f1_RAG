"""외부 API 호출 공통 클라이언트 (NF2006).

- 같은 호스트 호출 사이에 최소 간격을 둔다.
- 429·5xx·네트워크 오류는 지수 백오프로 재시도하고, Retry-After 헤더가 있으면 따른다.
- 출처가 요구하는 식별 정보(User-Agent 연락처)를 모든 요청에 붙인다.
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import requests

from f1rag.errors import SourceError

logger = logging.getLogger("f1rag.http")

APP_NAME = "F1RaceNotesRAG/1.0 (SSAFY AI core project)"


class PoliteSession:
    def __init__(
        self,
        contact: str | None,
        min_interval_sec: float = 1.0,
        retries: int = 4,
        timeout_sec: float = 20,
        backoff_sec: float = 1.0,
        per_host_interval: dict[str, float] | None = None,
    ):
        agent = f"{APP_NAME}; contact: {contact}" if contact else APP_NAME
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": agent, "Accept": "application/json"})
        self.min_interval = min_interval_sec
        self.per_host_interval = per_host_interval or {}
        self.retries = retries
        self.timeout = timeout_sec
        self.backoff = backoff_sec
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings, contact: str | None, **overrides) -> "PoliteSession":
        kwargs = dict(
            contact=contact,
            min_interval_sec=float(settings.get("http.min_interval_sec", 1.0)),
            retries=int(settings.get("http.retries", 4)),
            timeout_sec=float(settings.get("http.timeout_sec", 20)),
            backoff_sec=float(settings.get("http.backoff_sec", 1.0)),
        )
        kwargs.update(overrides)
        return cls(**kwargs)

    def _wait_turn(self, host: str) -> None:
        interval = self.per_host_interval.get(host, self.min_interval)
        with self._lock:
            now = time.monotonic()
            wait = self._last_call.get(host, 0.0) + interval - now
            if wait > 0:
                time.sleep(wait)
            self._last_call[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None, stream: bool = False) -> requests.Response:
        host = urlparse(url).netloc
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._wait_turn(host)
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout, stream=stream)
            except requests.RequestException as exc:
                last_error = exc
                delay = self.backoff * 2**attempt
                logger.warning("요청 실패(%s), %.1f초 후 재시도: %s", type(exc).__name__, delay, url)
                time.sleep(delay)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = _retry_after(resp) or self.backoff * 2**attempt
                last_error = SourceError(f"HTTP {resp.status_code}")
                logger.warning("HTTP %s, %.1f초 후 재시도: %s", resp.status_code, delay, url)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise SourceError(f"HTTP {resp.status_code}: {url}")
            return resp
        raise SourceError(f"재시도 {self.retries}회 후에도 실패: {url} ({last_error})")

    def get_json(self, url: str, params: dict | None = None) -> dict:
        return self.get(url, params=params).json()


def _retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return float(value) if value else None
    except ValueError:
        return None
