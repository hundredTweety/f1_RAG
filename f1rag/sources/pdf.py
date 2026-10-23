"""PDF를 쪽 단위 텍스트로 바꾸는 공통 도구 (F2016)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pypdf import PdfReader


def extract_pages(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    return [(page.extract_text() or "") for page in reader.pages]


def strip_repeated_lines(pages: list[str], min_ratio: float = 0.5, min_pages: int = 4, zone: int = 3) -> list[str]:
    """여러 쪽에 반복되는 머리말·꼬리말 줄을 지운다.

    규정 PDF는 쪽마다 "2024 Formula 1 Sporting Regulations", "©2024 FIA" 같은 줄이 붙어서
    그대로 두면 모든 청크에 같은 문장이 섞여 검색 품질이 떨어진다.
    - 쪽 번호처럼 숫자만 다른 줄도 같은 줄로 본다.
    - 본문의 "48.1 …", "48.2 …"처럼 숫자만 다른 줄이 지워지지 않도록,
      쪽의 맨 위·맨 아래 zone 줄 안에서만 반복 여부를 센다.
    """
    if len(pages) < min_pages:
        return pages

    def key(line: str) -> str:
        return "".join("#" if ch.isdigit() else ch for ch in line.strip())

    def edges(lines: list[str]) -> set[int]:
        idx = [i for i, ln in enumerate(lines) if ln.strip()]
        return set(idx[:zone] + idx[-zone:])

    split = [text.splitlines() for text in pages]
    counts: Counter[str] = Counter()
    for lines in split:
        counts.update({key(lines[i]) for i in edges(lines)})
    threshold = max(2, int(len(pages) * min_ratio))
    repeated = {k for k, c in counts.items() if c >= threshold}
    out = []
    for lines in split:
        drop = {i for i in edges(lines) if key(lines[i]) in repeated}
        out.append("\n".join(ln for i, ln in enumerate(lines) if i not in drop))
    return out
