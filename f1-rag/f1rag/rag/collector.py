"""외부 문서 수집과 색인 (F2003 검색어 추출, F2004 수집, F2005 색인, NF2002 캐시).

흐름
 1. 항목 자체 문서: 선택한 경기의 위키 문서는 선별·길이 규칙보다 우선해 항상 색인한다.
 2. 프롬프트 1로 질문에서 검색어를 뽑는다. 같은 (경기, 질문)이면 저장된 검색어를 쓴다.
 3. 이미 수집한 검색어는 건너뛰고, 새 검색어만 위키백과에서 후보(제목+요약)를 모은다.
 4. 프롬프트 2로 색인할 후보를 고른다. 본문은 고른 문서만 받는다 (외부 호출 절약).
 5. 본문을 청킹해 docs 컬렉션에 색인한다. 청크 ID = 경기ID:문서ID:순번.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Callable

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser

from f1rag.domain.races import label as race_label
from f1rag.errors import UserFacingError
from f1rag.rag import prompts
from f1rag.sources.wiki import Candidate, Page
from f1rag.store.chunking import chunk_wiki_page

logger = logging.getLogger("f1rag.collector")

Progress = Callable[[str, str], None]


def _noop(stage: str, message: str) -> None:
    pass


@dataclass
class CandidateResult:
    title: str
    lang: str
    url: str
    query: str
    selected: bool
    reason: str = ""


@dataclass
class IndexedDoc:
    title: str
    url: str
    lang: str
    chunks: int
    is_item_doc: bool
    query: str = ""


@dataclass
class CollectionReport:
    queries: list[dict] = field(default_factory=list)  # {"query", "cached"}
    candidates: list[CandidateResult] = field(default_factory=list)
    indexed: list[IndexedDoc] = field(default_factory=list)
    item_docs_cached: int = 0
    queries_from_cache: bool = False
    errors: list[str] = field(default_factory=list)
    failed_queries: list[str] = field(default_factory=list)  # 다음 질문 때 다시 수집할 검색어
    english_query: str = ""  # 프롬프트 1이 만든 영어 검색 문장 (영어 위키·규정 검색용)

    @property
    def new_queries(self) -> list[str]:
        return [q["query"] for q in self.queries if not q["cached"]]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["new_queries"] = self.new_queries
        return d


class Collector:
    def __init__(self, rt):
        self.rt = rt
        self.s = rt.settings
        self.parser = StrOutputParser()

    @property
    def languages(self) -> list[str]:
        return list(self.s.get("collection.languages", ["en"]))

    # ------------------------------------------------------------ 진입점

    def collect(self, item: Document, question: str, progress: Progress = _noop) -> CollectionReport:
        report = CollectionReport()
        meta = item.metadata
        race = self.rt.races.get(meta["race_id"], {})

        progress("collect", "경기 자체 문서 확인 중")
        self.ensure_item_docs(meta, race, report)

        progress("collect", "검색어 추출 중 (프롬프트 1)")
        queries = self._queries(item, question, report)
        new = [q for q in queries if not self.rt.cache.query_done(meta["race_id"], q)]
        report.queries = [{"query": q, "cached": q not in new} for q in queries]
        if not new:
            progress("collect", "이미 수집한 검색어라 외부 수집을 건너뜀")
            return report

        progress("collect", f"후보 문서 검색 중: {', '.join(new)}")
        candidates = self._search(meta, new, report)
        if not candidates:
            for q in new:
                if q not in report.failed_queries:
                    self.rt.cache.mark_query(meta["race_id"], q, [], 0)
            return report

        progress("collect", f"후보 {len(candidates)}건 중 색인할 문서 선별 중 (프롬프트 2)")
        chosen = self._select(meta, question, candidates)

        selected_ids: dict[str, list[str]] = {q: [] for q in new}
        for idx, cand in enumerate(candidates, start=1):
            if idx not in chosen:
                report.candidates.append(CandidateResult(cand.title, cand.lang, cand.url, cand.query, False, "선별에서 제외"))
                continue
            progress("collect", f"본문 수집 중: {cand.title}")
            page = self._fetch(cand.title, cand.lang, report)
            min_chars = int(self.s.get("collection.min_doc_chars", 300))
            if page is None:
                report.candidates.append(CandidateResult(cand.title, cand.lang, cand.url, cand.query, False, "본문을 받지 못함"))
                report.failed_queries.append(cand.query)
                continue
            if len(page.text) < min_chars:
                report.candidates.append(CandidateResult(cand.title, cand.lang, cand.url, cand.query, False, f"본문이 {min_chars}자 미만"))
                continue
            n = self._index_page(page, meta, query=cand.query, is_item_doc=False)
            report.candidates.append(CandidateResult(cand.title, cand.lang, page.url, cand.query, True))
            report.indexed.append(IndexedDoc(page.title, page.url, page.lang, n, False, cand.query))
            selected_ids.setdefault(cand.query, []).append(page.doc_id)

        for q in new:
            if q in report.failed_queries:
                continue  # 실패한 검색어는 다음 질문 때 다시 수집한다
            self.rt.cache.mark_query(meta["race_id"], q, selected_ids.get(q, []), sum(c.query == q for c in candidates))
        return report

    # ------------------------------------------------------------ 1. 항목 자체 문서

    def item_doc_targets(self, meta: dict, race: dict) -> list[dict]:
        """races.json 에 정해 둔 항목 자체 문서 중 설정 언어(collection.languages)의 것.
        없으면 검색으로 찾는다 (결과는 기억해 두고 재사용)."""
        langs = self.languages
        targets = list(race.get("item_docs") or [])
        # 이전 버전 races.json 은 한국어 문서가 길면 영어 문서를 뺐다. 영어 문서를 여기서 보충한다
        # (build_races.py 를 다시 돌리지 않아도 됨).
        en_title = race.get("wiki_en_title")
        if en_title and "en" in langs and not any(t["lang"] == "en" for t in targets):
            if targets or race.get("wiki_en_exists") or "ko" not in langs:
                targets.append({"lang": "en", "title": en_title})
        targets = [t for t in targets if t["lang"] in langs]
        if targets:
            return targets
        memo = self.__dict__.setdefault("_target_memo", {})
        if meta["race_id"] not in memo:
            memo[meta["race_id"]] = self._find_item_doc(meta, race)
        return memo[meta["race_id"]]

    def _find_item_doc(self, meta: dict, race: dict) -> list[dict]:
        season, name_ko, name_en = meta.get("season"), race.get("name_ko", ""), meta.get("name_en", "")
        candidates = [("ko", f"{season}년 {name_ko}"), ("en", f"{season} {name_en}")]
        for lang, query in [c for c in candidates if c[0] in self.languages]:
            try:
                hits = self.rt.source.search(query, lang, 3)
            except UserFacingError:
                raise
            except Exception:
                logger.exception("항목 자체 문서 검색 실패: %s", query)
                continue
            for hit in hits:
                core = name_ko.replace(" 그랑프리", "") if lang == "ko" else name_en.replace(" Grand Prix", "")
                if str(season) in hit.title and core and core in hit.title:
                    return [{"lang": lang, "title": hit.title}]
        return []

    def ensure_item_docs(self, meta: dict, race: dict, report: CollectionReport) -> None:
        for target in self.item_doc_targets(meta, race):
            lang, title = target["lang"], target["title"]
            if self.rt.cache.item_doc(meta["race_id"], lang, title):
                report.item_docs_cached += 1
                continue
            errors_before = len(report.errors)
            page = self._fetch(title, lang, report)
            if page is None:
                if len(report.errors) == errors_before:  # 문서가 없는 경우만 기록, 오류였다면 다음에 다시 시도
                    self.rt.cache.mark_item_doc(meta["race_id"], lang, title, None, 0)
                continue
            # 항목 자체 문서는 선별·길이 규칙보다 우선해 항상 색인한다 (F2004)
            n = self._index_page(page, meta, query="(항목 자체 문서)", is_item_doc=True)
            self.rt.cache.mark_item_doc(meta["race_id"], lang, title, page.doc_id, n)
            report.indexed.append(IndexedDoc(page.title, page.url, page.lang, n, True))

    # ------------------------------------------------------------ 2. 검색어 (프롬프트 1)

    def extract_queries(self, item: Document, question: str) -> tuple[list[str], str]:
        """(위키 검색어 목록, 영어 검색 문장)"""
        chain = prompts.QUERY_EXTRACTION | self.rt.llm_fast | self.parser
        raw = chain.invoke(
            {
                **prompts.base_vars(),
                "max_queries": int(self.s.get("collection.max_queries", 4)),
                "wiki_language": "한국어" if self.languages[0] == "ko" else "영어",
                "item_text": item.page_content,
                "question": question,
            }
        )
        parsed = prompts.parse_json(raw)
        english = ""
        if isinstance(parsed, dict):
            english = str(parsed.get("english") or parsed.get("regulation") or "").strip()
            parsed = parsed.get("wiki")
        if not isinstance(parsed, list):
            logger.warning("검색어 추출 결과를 읽지 못해 질문을 그대로 검색어로 씀: %r", raw[:200])
            parsed = [question]
        seen, queries = set(), []
        for q in parsed:
            q = str(q).strip()
            if q and q.lower() not in seen:
                seen.add(q.lower())
                queries.append(q)
        return queries[: int(self.s.get("collection.max_queries", 4))], english

    def _queries(self, item: Document, question: str, report: CollectionReport) -> list[str]:
        cached = self.rt.cache.get_queries(item.metadata["race_id"], question)
        if cached is not None:
            report.queries_from_cache = True
            if isinstance(cached, dict):
                report.english_query = cached.get("english") or cached.get("regulation", "")
                return list(cached.get("wiki", []))
            return list(cached)  # 이전 버전 캐시: 위키 검색어 목록만 있음
        queries, english = self.extract_queries(item, question)
        report.english_query = english
        self.rt.cache.set_queries(item.metadata["race_id"], question, {"wiki": queries, "english": english})
        return queries

    # ------------------------------------------------------------ 3. 후보 검색

    def _search(self, meta: dict, queries: list[str], report: CollectionReport) -> list[Candidate]:
        limit = int(self.s.get("collection.results_per_query", 5))
        langs = self.languages
        own_titles = {t["title"] for t in self.item_doc_targets(meta, self.rt.races.get(meta["race_id"], {}))}
        seen: set[str] = set()
        out: list[Candidate] = []
        for q in queries:
            hits: list[Candidate] = []
            for lang in langs:
                try:
                    hits = self.rt.source.search(q, lang, limit)
                except UserFacingError:
                    raise  # 연락처 누락 같은 설정 문제는 화면에 안내한다
                except Exception as exc:
                    logger.exception("검색 실패: %s", q)
                    report.errors.append(f"'{q}' 검색 실패: {type(exc).__name__}")
                    report.failed_queries.append(q)
                    hits = []
                if hits:
                    break  # 앞 언어에서 결과가 있으면 다음 언어는 검색하지 않는다
            for h in hits:
                if h.doc_id in seen or h.title in own_titles:
                    continue
                seen.add(h.doc_id)
                if self.rt.stores.has_doc(meta["race_id"], h.doc_id):
                    continue  # 이 경기에 이미 색인한 문서
                out.append(h)
        return out

    # ------------------------------------------------------------ 4. 후보 선별 (프롬프트 2)

    def _select(self, meta: dict, question: str, candidates: list[Candidate]) -> set[int]:
        lines = [f"[{i}] {c.title} ({c.lang}) — {c.snippet[:200]}" for i, c in enumerate(candidates, start=1)]
        chain = prompts.CANDIDATE_SELECTION | self.rt.llm_fast | self.parser
        raw = chain.invoke(
            {
                **prompts.base_vars(),
                "max_selected": int(self.s.get("collection.max_selected_docs", 4)),
                "item_label": race_label(self.rt.races[meta["race_id"]]) if meta["race_id"] in self.rt.races else meta.get("title", ""),
                "question": question,
                "candidates": "\n".join(lines),
            }
        )
        parsed = prompts.parse_json(raw)
        nums = parsed.get("selected", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
        chosen = []
        for n in nums:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(candidates) and n not in chosen:
                chosen.append(n)
        return set(chosen[: int(self.s.get("collection.max_selected_docs", 4))])

    # ------------------------------------------------------------ 5. 본문 수집과 색인

    def _fetch(self, title: str, lang: str, report: CollectionReport) -> Page | None:
        try:
            return self.rt.source.fetch(title, lang)
        except UserFacingError:
            raise
        except Exception as exc:
            logger.exception("본문 수집 실패: %s (%s)", title, lang)
            report.errors.append(f"'{title}' 본문 수집 실패: {type(exc).__name__}")
            return None

    def _index_page(self, page: Page, meta: dict, *, query: str, is_item_doc: bool) -> int:
        docs = chunk_wiki_page(
            page,
            meta["race_id"],
            int(meta["season"]),
            chunk_size=int(self.s.get("chunking.wiki.chunk_size", 1000)),
            chunk_overlap=int(self.s.get("chunking.wiki.chunk_overlap", 200)),
            query=query,
            is_item_doc=is_item_doc,
        )
        # 청크 크기를 바꿔 다시 색인하면 순번 개수가 달라지므로, 같은 경기의 같은 문서 청크를 먼저 지운다
        self.rt.stores.delete_docs({"$and": [{"race_id": meta["race_id"]}, {"doc_id": page.doc_id}]})
        return self.rt.stores.upsert(self.rt.stores.docs, docs)
