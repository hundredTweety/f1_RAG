"""위키백과(MediaWiki API) 문서 출처.

검색(list=search)으로 후보를 모으고, 선별된 문서만 본문(prop=extracts)을 받는다.
본문을 받기 전에 버릴 문서를 정하기 위해 검색 단계에서는 제목과 요약(snippet)만 쓴다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote

from f1rag.sources.http import PoliteSession

API = "https://{lang}.wikipedia.org/w/api.php"
PAGE_URL = "https://{lang}.wikipedia.org/wiki/{title}"

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Candidate:
    lang: str
    title: str
    pageid: int
    snippet: str
    query: str = ""
    size: int = 0

    @property
    def doc_id(self) -> str:
        return f"{self.lang}wiki-{self.pageid}"

    @property
    def url(self) -> str:
        return page_url(self.lang, self.title)


@dataclass
class Page:
    lang: str
    title: str
    pageid: int
    url: str
    text: str
    extra: dict = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return f"{self.lang}wiki-{self.pageid}"


class DocumentSource(Protocol):
    """온라인 위키백과와 오프라인 문서 묶음이 같은 방식으로 쓰이도록 하는 인터페이스."""

    name: str

    def search(self, query: str, lang: str, limit: int) -> list[Candidate]: ...

    def fetch(self, title: str, lang: str) -> Page | None: ...


def page_url(lang: str, title: str) -> str:
    return PAGE_URL.format(lang=lang, title=quote(title.replace(" ", "_")))


def clean_snippet(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


class WikipediaSource:
    name = "wikipedia"

    def __init__(self, http: PoliteSession):
        self.http = http

    def _query(self, lang: str, params: dict) -> dict:
        base = {"action": "query", "format": "json", "formatversion": 2}
        return self.http.get_json(API.format(lang=lang), {**base, **params})

    def search(self, query: str, lang: str, limit: int) -> list[Candidate]:
        data = self._query(lang, {"list": "search", "srsearch": query, "srlimit": limit, "srprop": "snippet|size"})
        return [
            Candidate(
                lang=lang,
                title=item["title"],
                pageid=int(item["pageid"]),
                snippet=clean_snippet(item.get("snippet", "")),
                query=query,
                size=int(item.get("size", 0)),
            )
            for item in data.get("query", {}).get("search", [])
        ]

    def fetch(self, title: str, lang: str) -> Page | None:
        data = self._query(
            lang,
            {
                "prop": "extracts|info",
                "explaintext": 1,
                "exsectionformat": "wiki",
                "inprop": "url",
                "redirects": 1,
                "titles": title,
            },
        )
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing") or not pages[0].get("extract"):
            return None
        p = pages[0]
        return Page(
            lang=lang,
            title=p["title"],
            pageid=int(p["pageid"]),
            url=p.get("fullurl") or page_url(lang, p["title"]),
            text=p["extract"],
        )

    def langlinks(self, titles: list[str], from_lang: str, to_lang: str) -> dict[str, tuple[bool, str | None]]:
        """from_lang 제목 -> (문서 존재 여부, to_lang 문서 제목). 한 번에 50개씩 조회."""
        result: dict[str, tuple[bool, str | None]] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i : i + 50]
            params = {"prop": "langlinks", "lllang": to_lang, "lllimit": "max", "redirects": 1, "titles": "|".join(batch)}
            pages: dict[str, dict] = {}
            mapping: dict[str, str] = {}
            while True:
                data = self._query(from_lang, params)
                query = data.get("query", {})
                for key in ("normalized", "redirects"):
                    for item in query.get(key, []):
                        mapping[item["from"]] = item["to"]
                for page in query.get("pages", []):
                    entry = pages.setdefault(page["title"], {"missing": page.get("missing", False), "link": None})
                    for link in page.get("langlinks", []):
                        entry["link"] = link["title"]
                if "continue" not in data:
                    break
                params = {**params, **data["continue"]}
            for title in batch:
                info = pages.get(_follow(title, mapping))
                result[title] = (info is not None and not info["missing"], info["link"] if info else None)
        return result

    def lengths(self, titles: list[str], lang: str) -> dict[str, int]:
        """제목 -> 문서 길이(바이트). 없으면 0."""
        result: dict[str, int] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i : i + 50]
            data = self._query(lang, {"prop": "info", "redirects": 1, "titles": "|".join(batch)})
            query = data.get("query", {})
            mapping = {item["from"]: item["to"] for key in ("normalized", "redirects") for item in query.get(key, [])}
            pages = {p["title"]: p for p in query.get("pages", [])}
            for title in batch:
                page = pages.get(_follow(title, mapping))
                result[title] = 0 if page is None or page.get("missing") else int(page.get("length", 0))
        return result


def _follow(title: str, mapping: dict[str, str]) -> str:
    seen = set()
    while title in mapping and title not in seen:
        seen.add(title)
        title = mapping[title]
    return title
