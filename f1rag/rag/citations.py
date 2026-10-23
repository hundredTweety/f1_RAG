"""근거 번호 표시 (F2008).

답변 본문에서 근거 번호 표기를 찾아 실제로 인용된 근거만 화면에 남긴다. 존재하지 않는 번호는 무시한다.

프롬프트는 [근거 3] 형식을 요구하지만, 실제 실행에서 모델이 출처 이름을 함께 넣는 경우가 있었다.
  [근거 3] [근거 1, 2] [근거 2-4] [근거 1][근거 3]          ← 요구한 형식
  [위키백과 근거 4] [위키백과 레이스 리포트, 근거 5]       ← 실제로 나온 형식
  [경기 메타데이터·근거 1] [근거 2, 근거 5]
그래서 대괄호 묶음 하나 안에서 '근거' 뒤에 오는 숫자를 모두 읽는다. '근거'가 없는 대괄호는 무시한다.
"""

from __future__ import annotations

import re

BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,120})\]")
NUMS_AFTER_RE = re.compile(r"근거\s*[·:#]?\s*([0-9][0-9\s,~\-–]*)")


def _expand(nums: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*,\s*", nums.strip().strip(",")):
        rng = re.split(r"\s*[~\-–]\s*", part.strip())
        try:
            if len(rng) == 2 and rng[0] and rng[1]:
                a, b = int(rng[0]), int(rng[1])
                out.extend(range(a, b + 1) if 0 < b - a < 30 else [a, b])
            elif rng[0]:
                out.append(int(rng[0]))
        except ValueError:
            continue
    return out


def bracket_numbers(inner: str) -> list[int]:
    """대괄호 안쪽 문자열 하나에서 근거 번호들."""
    nums: list[int] = []
    for m in NUMS_AFTER_RE.finditer(inner):
        nums.extend(_expand(m.group(1)))
    return nums


def cited_numbers(answer: str) -> list[int]:
    """처음 나온 순서대로, 중복 없이."""
    out: list[int] = []
    for m in BRACKET_RE.finditer(answer):
        for n in bracket_numbers(m.group(1)):
            if n not in out:
                out.append(n)
    return out


def used_evidence(answer: str, evidence: list[dict]) -> tuple[list[dict], list[int]]:
    """(답변에 실제로 인용된 근거, 근거 목록에 없는 잘못된 번호)"""
    by_n = {e["n"]: e for e in evidence}
    nums = cited_numbers(answer)
    used = [by_n[n] for n in nums if n in by_n]
    invalid = [n for n in nums if n not in by_n]
    return used, invalid
