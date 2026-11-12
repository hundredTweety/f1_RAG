"""LangGraph RAG 파이프라인: retrieve → generate 두 노드.

외부 문서 수집은 그래프 앞 단계(services.qa)에서 끝내고,
그래프는 '이미 색인된 문서에서 근거를 찾고 그 근거로만 답한다'는 책임만 진다.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, START, StateGraph

from f1rag.domain import profile
from f1rag.rag import prompts
from f1rag.rag.citations import used_evidence
from f1rag.rag.retriever import retrieve


class RAGState(TypedDict, total=False):
    item: Document
    item_label: str
    question: str
    mode: str
    top_k: int | None
    search_type: str | None
    english_query: str
    evidence: list[dict]
    retrieval: dict[str, Any]
    answer: str
    used: list[dict]
    invalid_citations: list[int]
    timings: dict[str, float]


def format_evidence(evidence: list[dict]) -> str:
    blocks = []
    for e in evidence:
        head = f"[근거 {e['n']}] ({e['source_label']}) {e['title']}"
        blocks.append(f"{head}\n{e['text']}")
    return "\n\n".join(blocks)


def build_graph(rt):
    parser = StrOutputParser()

    def retrieve_node(state: RAGState) -> dict:
        t0 = time.perf_counter()
        result = retrieve(
            rt,
            state["item"],
            state["question"],
            state.get("mode", "qa"),
            top_k=state.get("top_k"),
            search_type=state.get("search_type"),
            english_query=state.get("english_query", ""),
        )
        timings = dict(state.get("timings", {}))
        timings["retrieve"] = round(time.perf_counter() - t0, 2)
        return {"evidence": result["evidence"], "retrieval": result, "timings": timings}

    def generate_node(state: RAGState) -> dict:
        t0 = time.perf_counter()
        mode = profile.MODES.get(state.get("mode", "qa"), profile.MODES["qa"])
        chain = prompts.ANSWER_GENERATION | rt.llm | parser
        answer = chain.invoke(
            {
                **prompts.base_vars(),
                "mode_instructions": mode.instructions,
                "item_label": state["item_label"],
                "question": state["question"],
                "evidence": format_evidence(state["evidence"]),
            }
        ).strip()
        used, invalid = used_evidence(answer, state["evidence"])
        timings = dict(state.get("timings", {}))
        timings["generate"] = round(time.perf_counter() - t0, 2)
        return {"answer": answer, "used": used, "invalid_citations": invalid, "timings": timings}

    graph = StateGraph(RAGState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
