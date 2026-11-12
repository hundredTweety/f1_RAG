"""FIA 스포팅 규정·심판 결정문 PDF (데이터 준비 영역, 도메인 전용).

규정 PDF는 위키 문서와 성격이 달라 청킹 방식을 다르게 한다.
- 위키: 섹션으로 나눈 뒤 길이 기준(1,000자/200자 중복)으로 자른다. 서술형이라 앞뒤 문맥이 이어지기 때문.
- 규정: 조항(예: "55) SAFETY CAR") 1개를 청크 1개로 둔다. 길이로 자르면 조항이 중간에 끊겨
  다른 조항 번호로 인용되는 문제가 생기기 때문이다. 너무 긴 조항만 하위 조항(55.1, 55.2 …) 경계에서 나눈다.
- 쪽마다 반복되는 머리말·꼬리말은 지운다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from f1rag.sources.pdf import extract_pages, strip_repeated_lines

# "55) SAFETY CAR" 형식. 목차는 "55 SAFETY CAR 61" 처럼 괄호가 없어 걸리지 않는다.
# 제목은 "PARC FERMÉ"처럼 악센트 문자가 있을 수 있어 문자 목록 대신 '소문자가 없는 줄'로 판단한다.
ARTICLE_RE = re.compile(r"^\s*(\d{1,3})\)\s+(\S.{1,90}?)\s*$")
SUB_RE = re.compile(r"^\s*(\d{1,3}\.\d{1,3})[a-z]?\s+\S")
SUB_MAJOR_RE = re.compile(r"^\s*(\d{1,3})\.\d{1,3}[a-z]?\s+\S")


def _is_heading_title(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    return len(letters) >= 3 and title == title.upper() and not title[0].isdigit() and not title.rstrip().endswith(tuple("0123456789"))
MIN_ARTICLES = 5


@dataclass
class Article:
    season: int
    number: int
    title: str
    lines: list[tuple[int, str]] = field(default_factory=list)  # (쪽 번호, 줄)

    @property
    def text(self) -> str:
        return "\n".join(line for _, line in self.lines).strip()

    @property
    def page_start(self) -> int:
        return self.lines[0][0] if self.lines else 0


def parse_articles(pages: list[str], season: int) -> list[Article]:
    pages = strip_repeated_lines(pages)
    articles: list[Article] = []
    current: Article | None = None
    for page_no, text in enumerate(pages, start=1):
        for line in text.splitlines():
            if not line.strip():
                continue
            m = ARTICLE_RE.match(line)
            if m and _is_heading_title(m.group(2)):
                number = int(m.group(1))
                # 본문 안의 "1) ..." 같은 목록을 조항으로 착각하지 않도록 번호가 앞으로만 증가할 때만 인정
                if current is None or current.number < number <= current.number + 50:
                    current = Article(season=season, number=number, title=m.group(2).strip())
                    current.lines.append((page_no, line.strip()))
                    articles.append(current)
                    continue
            sub = SUB_MAJOR_RE.match(line)
            if current is not None and sub and int(sub.group(1)) == current.number + 1:
                # 제목 줄을 놓쳤더라도(줄바꿈·특수문자) 다음 조항의 하위 조항 번호가 나오면 새 조항으로 나눈다
                current = Article(season=season, number=current.number + 1, title="(제목 인식 실패)")
                articles.append(current)
            if current is not None:
                current.lines.append((page_no, line.rstrip()))
    if len(articles) >= MIN_ARTICLES:
        return articles
    # 조항 형식을 찾지 못한 PDF는 쪽 단위로 대신한다
    return [
        Article(season=season, number=0, title=f"{no}쪽", lines=[(no, ln) for ln in text.splitlines() if ln.strip()])
        for no, text in enumerate(pages, start=1)
        if text.strip()
    ]


def _units(article: Article) -> list[tuple[str, int, str]]:
    """조항을 하위 조항 단위로 나눈다. (하위 조항 번호, 시작 쪽, 텍스트)"""
    units: list[tuple[str, int, list[str]]] = []
    for page, line in article.lines:
        m = SUB_RE.match(line)
        if m or not units:
            units.append((m.group(1) if m else str(article.number), page, [line]))
        else:
            units[-1][2].append(line)
    return [(num, page, "\n".join(lines).strip()) for num, page, lines in units]


def chunk_regulation(
    articles: list[Article],
    *,
    url: str,
    file_name: str,
    max_article_chars: int,
    chunk_size: int,
    chunk_overlap: int,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    docs: list[Document] = []
    for art in articles:
        season = art.season
        doc_id = f"fia-sr-{season}"
        name = f"{season} FIA F1 스포팅 규정 제{art.number}조 {art.title}" if art.number else f"{season} FIA F1 스포팅 규정 {art.title}"
        pieces: list[tuple[str, int, str]] = []  # (하위 조항 범위, 쪽, 텍스트)
        if len(art.text) <= max_article_chars:
            pieces.append((str(art.number), art.page_start, art.text))
        else:
            group: list[tuple[str, int, str]] = []
            size = 0
            for unit in _units(art):
                if group and size + len(unit[2]) > chunk_size:
                    pieces.append(_merge(group))
                    group, size = [], 0
                if len(unit[2]) > chunk_size:
                    for part in splitter.split_text(unit[2]):
                        pieces.append((unit[0], unit[1], part))
                    continue
                group.append(unit)
                size += len(unit[2])
            if group:
                pieces.append(_merge(group))
        for seq, (subs, page, text) in enumerate(pieces, start=1):
            article_no = str(art.number) if art.number else f"p{page}"
            docs.append(
                Document(
                    id=f"season{season}:{doc_id}:a{art.number:03d}-{seq:02d}" if art.number else f"season{season}:{doc_id}:p{page:03d}-{seq:02d}",
                    page_content=f"[{name}]\n{text}",
                    metadata={
                        "source_type": "fia_regulation",
                        "season": season,
                        "doc_id": doc_id,
                        "title": name,
                        "article_no": article_no,
                        "article_title": art.title,
                        "sub_articles": subs,
                        "page": page,
                        "file": file_name,
                        "url": f"{url}#page={page}" if url else "",
                    },
                )
            )
    return docs


def _merge(group: list[tuple[str, int, str]]) -> tuple[str, int, str]:
    first, last = group[0][0], group[-1][0]
    subs = first if first == last else f"{first}–{last}"
    return subs, group[0][1], "\n".join(text for _, _, text in group)


def load_regulation(path: Path, season: int, url: str, settings) -> list[Document]:
    articles = parse_articles(extract_pages(path), season)
    return chunk_regulation(
        articles,
        url=url,
        file_name=path.name,
        max_article_chars=int(settings.get("chunking.regulation.max_article_chars", 2500)),
        chunk_size=int(settings.get("chunking.regulation.chunk_size", 1500)),
        chunk_overlap=int(settings.get("chunking.regulation.chunk_overlap", 0)),
    )


def load_decision(path: Path, race_id: str, season: int, url: str, settings) -> list[Document]:
    """심판 결정문: 대부분 1~2쪽이라 쪽 텍스트를 이어 붙인 뒤 길이 기준으로만 자른다."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(settings.get("chunking.decision.chunk_size", 1500)),
        chunk_overlap=int(settings.get("chunking.decision.chunk_overlap", 150)),
    )
    pages = extract_pages(path)
    title = path.stem.replace("_", " ")
    doc_id = "dec-" + re.sub(r"[^0-9a-z]+", "-", path.stem.lower()).strip("-")[:60]
    docs = []
    seq = 0
    for page_no, text in enumerate(pages, start=1):
        for part in splitter.split_text(text):
            seq += 1
            docs.append(
                Document(
                    id=f"{race_id}:{doc_id}:{seq:03d}",
                    page_content=f"[FIA 심판 결정문: {title}]\n{part}",
                    metadata={
                        "source_type": "fia_decision",
                        "race_id": race_id,
                        "lang": "en",
                        "season": season,
                        "doc_id": doc_id,
                        "title": title,
                        "page": page_no,
                        "file": path.name,
                        "url": f"{url}#page={page_no}" if url else "",
                    },
                )
            )
    return docs


def load_manifest(path: Path) -> dict:
    if not Path(path).exists():
        return {"regulations": [], "decisions": []}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {"regulations": data.get("regulations") or [], "decisions": data.get("decisions") or []}
