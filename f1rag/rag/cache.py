"""수집 캐시 ("이미 수집한 검색어는 다시 수집하지 않는다").

data/cache/collect_cache.json 에 저장한다.
- questions: (경기, 질문) -> 프롬프트 1이 뽑은 위키 검색어와 영어 검색 문장. 같은 질문이면 LLM을 다시 부르지 않는다.
- queries:   (경기, 검색어) -> 수집 결과. 이미 수집한 검색어는 검색·선별·본문 수집을 건너뛴다.
- item_docs: (경기, 언어, 제목) -> 항목 자체 문서 색인 여부.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from f1rag.store.jsonfile import read_json, write_json


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class CollectCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = {"questions": {}, "queries": {}, "item_docs": {}}
        self._merge_disk()  # 깨진 캐시는 빈 것으로 본다

    def _merge_disk(self) -> None:
        """파일에만 있는 항목을 가져온다. Streamlit과 FastAPI 서버를 같이 켜 두면 둘이 같은 파일을 쓰므로,
        저장하기 전에 다른 쪽이 쓴 항목을 합쳐 서로 지우지 않게 한다."""
        loaded = read_json(self.path)
        for key in self._data:
            for k, v in (loaded.get(key) or {}).items():
                self._data[key].setdefault(k, v)

    def _save(self) -> None:
        self._merge_disk()
        write_json(self.path, self._data)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 질문 -> 검색어
    def get_queries(self, item_id: str, question: str) -> dict | list[str] | None:
        return self._data["questions"].get(f"{item_id}|{_norm(question)}")

    def set_queries(self, item_id: str, question: str, queries: dict | list[str]) -> None:
        with self._lock:
            self._data["questions"][f"{item_id}|{_norm(question)}"] = queries
            self._save()

    # 검색어 수집 여부
    def query_done(self, item_id: str, query: str) -> bool:
        return f"{item_id}|{_norm(query)}" in self._data["queries"]

    def mark_query(self, item_id: str, query: str, selected: list[str], candidates: int) -> None:
        with self._lock:
            self._data["queries"][f"{item_id}|{_norm(query)}"] = {
                "at": self._now(),
                "candidates": candidates,
                "selected": selected,
            }
            self._save()

    # 항목 자체 문서
    def item_doc(self, item_id: str, lang: str, title: str) -> dict | None:
        return self._data["item_docs"].get(f"{item_id}|{lang}|{title}")

    def mark_item_doc(self, item_id: str, lang: str, title: str, doc_id: str | None, chunks: int) -> None:
        with self._lock:
            self._data["item_docs"][f"{item_id}|{lang}|{title}"] = {"at": self._now(), "doc_id": doc_id, "chunks": chunks}
            self._save()

    def stats(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._data.items()}
