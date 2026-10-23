"""외부 문서(위키백과) 청킹과 청크 식별자 규칙 (F2005).

청크 ID = "항목ID:문서ID:순번" 예) 2021_22:kowiki-123456:003
- 여러 경기가 같은 문서를 수집해도 항목 ID가 달라 서로 덮어쓰지 않는다.
- 같은 경기에서 같은 문서를 다시 색인하면 ID가 같아 덮어쓰기만 일어난다(중복 저장 없음).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from f1rag.sources.wiki import Page

# TextExtracts(exsectionformat=wiki)는 섹션 제목을 "== 제목 ==" 형태로 준다
_SECTION_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)

# 근거로 쓸 내용이 없는 섹션은 버린다
SKIP_SECTIONS = {
    "각주", "주석", "참고 문헌", "참고문헌", "외부 링크", "같이 보기", "출처", "관련 항목",
    "references", "notes", "external links", "see also", "footnotes", "bibliography", "further reading",
}


def split_sections(text: str) -> list[tuple[str, str]]:
    """(섹션 제목, 본문) 목록. 첫 제목 전 본문은 '개요'로 둔다."""
    sections: list[tuple[str, str]] = []
    last_title, last_end = "개요", 0
    for m in _SECTION_RE.finditer(text):
        sections.append((last_title, text[last_end : m.start()]))
        last_title, last_end = m.group(2).strip(), m.end()
    sections.append((last_title, text[last_end:]))
    return [(t, body.strip()) for t, body in sections if body.strip() and t.lower() not in SKIP_SECTIONS]


def chunk_wiki_page(
    page: Page,
    item_id: str,
    season: int,
    *,
    chunk_size: int,
    chunk_overlap: int,
    query: str = "",
    is_item_doc: bool = False,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", "다. ", " ", ""],
    )
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    docs: list[Document] = []
    seq = 0
    for section, body in split_sections(page.text):
        for part in splitter.split_text(body):
            seq += 1
            docs.append(
                Document(
                    id=f"{item_id}:{page.doc_id}:{seq:03d}",
                    page_content=f"[{page.title} › {section}]\n{part}",
                    metadata={
                        "source_type": "wikipedia",
                        "race_id": item_id,
                        "season": season,
                        "doc_id": page.doc_id,
                        "title": page.title,
                        "section": section,
                        "lang": page.lang,
                        "url": page.url,
                        "query": query,
                        "is_item_doc": is_item_doc,
                        "collected_at": collected_at,
                    },
                )
            )
    return docs
