"""오프라인 문서 묶음 출처.

외부 문서 호출이 제한되는 환경에서 제공된 문서 묶음을 읽어 수집 단계를 대신한다.
문서 묶음 형식: {offline_docs_dir}/{lang}/*.json
  {"title": "...", "lang": "ko", "pageid": 123, "url": "https://...", "text": "..."}
scripts/collect_item_docs.py --export-offline 으로 온라인 환경에서 미리 만들어 둘 수 있다.
"""

from __future__ import annotations

import json
import re
import zlib
from pathlib import Path

from f1rag.sources.wiki import Candidate, Page, page_url

_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def _terms(text: str) -> set[str]:
    words = {w.lower() for w in _WORD_RE.findall(text)}
    # 한국어는 조사가 붙으므로 두 글자 묶음도 함께 쓴다 ("세이프티카는" -> "세이", "이프", ...)
    grams = {w[i : i + 2] for w in words if not w.isascii() for i in range(len(w) - 1)}
    return words | grams


class OfflineSource:
    name = "offline"

    def __init__(self, root: Path):
        self.root = Path(root)
        self._pages: dict[tuple[str, str], Page] = {}
        for path in sorted(self.root.glob("*/*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            lang = data.get("lang") or path.parent.name
            page = Page(
                lang=lang,
                title=data["title"],
                # pageid 가 없으면 제목으로 고정 번호를 만든다 (실행마다 같은 문서 ID가 나와야 덮어쓰기가 된다)
                pageid=int(data.get("pageid") or zlib.crc32(data["title"].encode("utf-8"))),
                url=data.get("url") or page_url(lang, data["title"]),
                text=data["text"],
            )
            self._pages[(lang, page.title)] = page

    def __len__(self) -> int:
        return len(self._pages)

    def search(self, query: str, lang: str, limit: int) -> list[Candidate]:
        q = _terms(query)
        scored = []
        for (page_lang, title), page in self._pages.items():
            if page_lang != lang:
                continue
            title_terms = _terms(title)
            body_terms = _terms(page.text[:3000])
            score = 3 * len(q & title_terms) + len(q & body_terms)
            if score:
                scored.append((score, page))
        scored.sort(key=lambda x: (-x[0], x[1].title))
        return [
            Candidate(lang=p.lang, title=p.title, pageid=p.pageid, snippet=p.text[:200], query=query, size=len(p.text))
            for _, p in scored[:limit]
        ]

    def fetch(self, title: str, lang: str) -> Page | None:
        return self._pages.get((lang, title))


def export_page(root: Path, page: Page) -> Path:
    folder = Path(root) / page.lang
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{page.pageid}.json"
    payload = {"title": page.title, "lang": page.lang, "pageid": page.pageid, "url": page.url, "text": page.text}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
