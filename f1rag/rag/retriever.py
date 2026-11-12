"""근거 검색과 병합.

- 선택한 경기의 메타데이터 청크는 검색하지 않고 ID로 꺼내 항상 근거 1번에 둔다.
- 경기 문서(위키백과·심판 결정문)는 그 경기 필터로 top_k개를 의미 검색한다.
  search_type=mmr 이면 fetch_k개 후보 중 내용이 겹치지 않게 top_k개를 고른다.
  한국어 질문 검색 결과와 영어 검색 문장 결과를 번갈아 섞어 영어 문서가 밀리지 않게 한다.
- 그 시즌 규정 조항은 따로 검색해 기능별 개수만큼 더한다(질문하기 regulation_k_qa, 판정 regulation_k).
  한 번에 검색하면 수가 훨씬 많은 규정 청크가 경기 문서 자리를 차지하기 때문이다.
  규정은 영어라 영어 검색 문장과 용어 사전(search_regulations)으로 찾는다.
- 두 컬렉션 결과를 합치고, 청크 ID와 본문이 같은 청크를 제거한다.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from langchain_core.documents import Document

from f1rag.domain import profile


def _search(store, query: str, k: int, search_type: str, flt: dict, fetch_k: int, lam: float) -> list[Document]:
    if k <= 0:
        return []
    if search_type == "mmr":
        return store.max_marginal_relevance_search(query, k=k, fetch_k=max(fetch_k, k), lambda_mult=lam, filter=flt)
    return store.similarity_search(query, k=k, filter=flt)


def _body_key(doc: Document) -> str:
    body = doc.page_content.split("\n", 1)[-1]  # 첫 줄의 [제목 › 섹션] 머리표는 빼고 비교
    return hashlib.sha1(re.sub(r"\s+", " ", body).strip().encode("utf-8")).hexdigest()


def merge(groups: list[list[Document]]) -> tuple[list[Document], int]:
    """순서를 유지하며 중복 청크 제거. (결과, 제거한 수)"""
    seen_ids, seen_bodies, out, removed = set(), set(), [], 0
    for group in groups:
        for doc in group:
            key = _body_key(doc)
            if (doc.id and doc.id in seen_ids) or key in seen_bodies:
                removed += 1
                continue
            seen_ids.add(doc.id)
            seen_bodies.add(key)
            out.append(doc)
    return out, removed


def _contains_any(term: str) -> dict:
    """Chroma 본문 포함 검색은 대소문자를 구분하므로 흔한 표기를 모두 넣는다."""
    variants = list(dict.fromkeys([term, term.lower(), term.upper(), term.title(), term.capitalize()]))
    return {"$or": [{"$contains": v} for v in variants]} if len(variants) > 1 else {"$contains": term}


def _gated_english(
    rt, query: str, terms: list[str], flt: dict, k: int, st: str = "similarity", fetch_k: int = 30, lam: float = 0.5
) -> list[Document]:
    """영어 용어가 실제로 들어 있는 청크 안에서 먼저 찾고, 그다음 일반 검색 결과를 붙인다.

    st=mmr 이면 용어가 든 청크 안에서도 MMR로 고른다. 예전에는 이 단계가 항상 유사도 검색이라
    용어가 든 청크만으로 k개가 채워져 search_type 설정이 결과에 영향을 주지 못했다
    (실제 실행 compare_retrieval.py에서 유사도·MMR 근거가 완전히 같았음).
    """
    store = rt.stores.docs

    def one(where_document):
        extra = {"where_document": where_document} if where_document else {}
        if st == "mmr":
            return store.max_marginal_relevance_search(query, k=k, fetch_k=max(fetch_k, k), lambda_mult=lam, filter=flt, **extra)
        return store.similarity_search(query, k=k, filter=flt, **extra)

    groups = [one(_contains_any(t)) for t in terms[:2]]
    groups.append(one(None))
    merged, _ = merge(groups)
    return merged


def english_query_for(question: str, english_query: str) -> str:
    """영어 검색 문장: 프롬프트 1이 만든 것, 없으면 용어 사전으로 만든 것."""
    return english_query or " ".join(profile.english_terms(question))


def search_race_docs(rt, question: str, english_query: str, meta: dict, k: int, st: str, fetch_k: int, lam: float) -> list[Document]:
    """그 경기 문서 k개.

    - 영어 원문만 쓰는 기본 설정: 영어 검색 문장(용어가 든 문단 우선) 결과를 먼저 쓴다.
    - 한국어 문서도 쓰는 설정: 한국어 질문 검색 결과와 영어 검색 결과를 번갈아 섞는다.

    한국어 질문으로만 검색하면 한국어 문서가 유사도에서 이겨, 경기 중 사건이 자세한 영어 문서가 밀린다
    (2023 호주 GP 레드 플래그 질문에서 한국어 예선 요약만 근거로 잡힘).
    """
    langs = list(rt.settings.get("collection.languages", ["en"]))
    eq = english_query_for(question, english_query)
    if "en" in langs and eq:
        english = _gated_english(rt, eq, profile.english_terms(question), profile.english_doc_filter(meta), k, st, fetch_k, lam)
    else:
        english = []
    if langs == ["en"]:
        # 영어 원문만 쓰는 기본 설정: 영어 검색 문장 결과를 먼저, 부족하면 질문 그대로 검색한 결과로 채운다
        rest = _search(rt.stores.docs, eq or question, k, st, profile.doc_filter(meta, langs), fetch_k, lam)
        merged, _ = merge([english, rest])
        return merged[:k]
    native = _search(rt.stores.docs, question, k, st, profile.doc_filter(meta, langs), fetch_k, lam)
    interleaved = [d for pair in zip(native, english) for d in pair]
    interleaved += native[len(english):] + english[len(native):]
    merged, _ = merge([interleaved])
    return merged[:k]


def search_regulations(rt, question: str, english_query: str, meta: dict, k: int) -> list[Document]:
    """그 시즌 규정에서 관련 조항 k개.

    규정은 영어라 한국어 질문으로 의미 검색하면 점수가 거의 무작위에 가깝다. 그래서 영어 검색 문장으로,
    질문의 F1 용어(세이프티카 -> safety car)가 실제로 들어 있는 조항 안에서 먼저 찾는다.
    고르는 수가 2~3개라 다양성보다 관련도가 중요해 MMR이 아니라 유사도 검색을 쓴다.
    """
    if k <= 0:
        return []
    query = english_query_for(question, english_query) or question
    return _gated_english(rt, query, profile.english_terms(question), profile.regulation_filter(meta), k)[:k]


def to_evidence(docs: list[Document]) -> list[dict[str, Any]]:
    out = []
    for n, doc in enumerate(docs, start=1):
        meta = doc.metadata
        src = meta.get("source_type", "")
        out.append(
            {
                "n": n,
                "chunk_id": doc.id,
                "source_type": src,
                "source_label": profile.SOURCE_LABELS.get(src, src),
                "title": profile.evidence_title(meta),
                "location": profile.evidence_location(meta),
                "url": meta.get("url", ""),
                "text": doc.page_content,
            }
        )
    return out


def retrieve(
    rt,
    item: Document,
    question: str,
    mode: str = "qa",
    *,
    top_k: int | None = None,
    search_type: str | None = None,
    english_query: str = "",
    mmr_lambda: float | None = None,
) -> dict[str, Any]:
    s = rt.settings
    k = int(top_k if top_k is not None else s.get("retrieval.top_k", 6))
    st = (search_type or s.get("retrieval.search_type", "similarity")).lower()
    fetch_k = int(s.get("retrieval.fetch_k", 30))
    lam = float(mmr_lambda if mmr_lambda is not None else s.get("retrieval.mmr_lambda", 0.7))
    meta = item.metadata

    m = profile.MODES.get(mode, profile.MODES["qa"])
    reg_k = int(s.get(m.regulation_setting, 0) or 0)
    race_docs = search_race_docs(rt, question, english_query, meta, k, st, fetch_k, lam)
    regulations = search_regulations(rt, question, english_query, meta, reg_k)
    merged, removed = merge([[item], race_docs, regulations])
    return {
        "evidence": to_evidence(merged),
        "external_count": len(merged) - 1,
        "race_doc_count": len(race_docs),
        "duplicates_removed": removed,
        "search_type": st,
        "top_k": k,
        "english_query": english_query_for(question, english_query),
    }
