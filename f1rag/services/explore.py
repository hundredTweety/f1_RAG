"""조건 기반 항목 탐색.

1. 조건 문장으로 메타데이터 컬렉션을 의미 검색해 후보를 모은다(연도가 있으면 그 시즌으로 먼저 좁힘).
   조건에 '최종전'·'개막전'이 있으면 각 시즌의 마지막·첫 라운드 경기를, '예선 1위가 아닌 … 1번 그리드'처럼
   메타데이터 두 값을 비교하는 조건이면 그 값이 다른 경기를 의미 검색 순위와 상관없이 후보에 넣는다.
2. LLM이 후보 정보만 보고 조건을 모두 만족하는 경기만 고른다.
3. 후보에 없는 ID를 지어내면 버린다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser

from f1rag.domain import profile
from f1rag.domain.races import label as race_label
from f1rag.errors import EmptyIndexError
from f1rag.rag import prompts


def position_candidates(rt, condition: str, flt: dict | None) -> list[Document]:
    """반드시 확인할 경기: 각 시즌의 최종전·개막전, 메타데이터 비교 규칙에 맞는 경기 (조건에 해당 표현이 있을 때만)."""
    positions = profile.explore_positions(condition)
    rules = profile.explore_fact_rules(condition)
    if not positions and not rules:
        return []
    got = rt.stores.items.get(where=flt, include=["metadatas", "documents"]) if flt else rt.stores.items.get(include=["metadatas", "documents"])
    by_season: dict[int, list] = {}
    for cid, meta, text in zip(got["ids"], got["metadatas"], got["documents"]):
        by_season.setdefault(int(meta["season"]), []).append((int(meta["round"]), cid, meta, text))
    out = []
    for season in sorted(by_season):
        rows = sorted(by_season[season], key=lambda r: r[0])
        picks = ([rows[0]] if "first" in positions else []) + ([rows[-1]] if "last" in positions else [])
        picks += [r for r in rows if r not in picks and any(rule(r[2]) for rule in rules)]
        out += [Document(id=cid, page_content=text, metadata=meta) for _, cid, meta, text in picks]
    return out


def explore(rt, condition: str, k: int | None = None) -> dict[str, Any]:
    condition = condition.strip()
    if rt.stores.counts()["items"] == 0:
        raise EmptyIndexError()
    k = int(k or rt.settings.get("retrieval.explore_candidates", 30))
    flt = profile.explore_filter(condition)
    required = position_candidates(rt, condition, flt)
    searched = rt.stores.items.similarity_search(condition, k=k, filter=flt)
    req_ids = {d.metadata["race_id"] for d in required}
    candidates = required + [d for d in searched if d.metadata["race_id"] not in req_ids]
    if not candidates:
        return {"condition": condition, "candidates": 0, "matches": [], "required": []}

    by_id = {d.metadata["race_id"]: d for d in candidates}
    lines = [f"- id: {d.metadata['race_id']}\n  " + d.page_content.replace("\n", "\n  ") for d in candidates]
    chain = prompts.EXPLORE_FILTER | rt.llm_fast | StrOutputParser()
    raw = chain.invoke({**prompts.base_vars(), "condition": condition, "candidates": "\n".join(lines)})
    parsed = prompts.parse_json(raw)
    items = parsed.get("matches", []) if isinstance(parsed, dict) else []

    matches, seen = [], set()
    for m in items:
        rid = str(m.get("id", "")).strip() if isinstance(m, dict) else ""
        if rid not in by_id or rid in seen:
            continue  # 후보에 없는 ID(지어낸 결과)는 버린다
        seen.add(rid)
        meta = by_id[rid].metadata
        race = rt.races.get(rid, {})
        matches.append(
            {
                "item_id": rid,
                "label": race_label(race) if race else meta.get("title", rid),
                "date": meta.get("date", ""),
                "circuit": meta.get("circuit", ""),
                "winner": meta.get("winner", ""),
                "pole": meta.get("pole", ""),
                "grid_one": meta.get("grid_one", ""),
                "reason": str(m.get("reason", "")),
            }
        )
    required_info = [
        {
            "item_id": d.metadata["race_id"],
            "label": race_label(rt.races[d.metadata["race_id"]]) if d.metadata["race_id"] in rt.races else d.metadata.get("title", ""),
            "pole": d.metadata.get("pole", ""),
            "grid_one": d.metadata.get("grid_one", ""),
            "winner": d.metadata.get("winner", ""),
        }
        for d in required
    ]
    return {"condition": condition, "candidates": len(candidates), "season_filter": flt, "matches": matches, "required": required_info}
