"""질의응답·판정 해설·사실 판정 서비스 (F2009, F2010, F2012).

화면(Streamlit, FastAPI)은 이 함수만 부른다. 진행 상태는 progress(stage, message) 콜백으로 알린다.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from f1rag.domain import profile
from f1rag.domain.races import label as race_label
from f1rag.errors import EmptyIndexError, UnknownItemError

Progress = Callable[[str, str], None]


def _noop(stage: str, message: str) -> None:
    pass


def item_label(rt, item_id: str, meta: dict) -> str:
    race = rt.races.get(item_id)
    return race_label(race) if race else meta.get("title", item_id)


def ask(
    rt,
    item_id: str,
    question: str,
    mode: str = "qa",
    *,
    progress: Progress = _noop,
    collect: bool = True,
    top_k: int | None = None,
    search_type: str | None = None,
) -> dict[str, Any]:
    t_start = time.perf_counter()
    if mode not in profile.MODES:
        mode = "qa"
    question = question.strip()

    item = rt.stores.get_item(item_id)
    if item is None:
        if rt.stores.counts()["items"] == 0:
            raise EmptyIndexError()
        raise UnknownItemError(item_id)

    report = None
    t_collect = 0.0
    if collect:
        t0 = time.perf_counter()
        report = rt.collector.collect(item, question, progress)
        t_collect = round(time.perf_counter() - t0, 2)
        n_new = sum(1 for d in report.indexed)
        if n_new:
            progress("collect_done", f"새 문서 {n_new}건 수집·색인 완료: " + ", ".join(d.title for d in report.indexed))
        else:
            docs = rt.stores.docs_for_item(item_id)
            progress("collect_done", f"이미 수집된 문서 {len(docs)}건 사용")

    progress("retrieve", "근거 검색 중")
    state: dict[str, Any] = {}
    initial = {
        "item": item,
        "item_label": item_label(rt, item_id, item.metadata),
        "question": question,
        "mode": mode,
        "top_k": top_k,
        "search_type": search_type,
        "english_query": report.english_query if report else "",
        "timings": {"collect": t_collect},
    }
    for update in rt.graph.stream(initial, stream_mode="updates"):
        for node, values in update.items():
            state.update(values)
            if node == "retrieve":
                progress("generate", f"근거 {len(values['evidence'])}건으로 답변 생성 중")

    used = state.get("used", [])
    retrieval = state.get("retrieval", {})
    timings = state.get("timings", {})
    timings["total"] = round(time.perf_counter() - t_start, 2)

    notices = []
    if not used:
        notices.append("답변에 인용된 근거가 없습니다. 이 답변은 문서로 확인되지 않은 내용일 수 있습니다.")
    if retrieval.get("race_doc_count", 0) == 0:
        notices.append("이 경기의 위키백과·결정문에서 근거를 찾지 못해 경기 메타데이터와 규정만으로 답했습니다.")
    if report and report.errors:
        notices.append(f"외부 문서 {len(report.errors)}건을 가져오지 못해 이미 색인된 문서로만 답했습니다. 잠시 후 다시 질문하면 수집을 다시 시도합니다.")
    if state.get("invalid_citations"):
        notices.append("답변에 근거 목록에 없는 번호가 있어 표시하지 않았습니다: " + ", ".join(map(str, state["invalid_citations"])))

    m = profile.MODES[mode]
    return {
        "item_id": item_id,
        "item_label": initial["item_label"],
        "question": question,
        "mode": mode,
        "mode_label": m.label,
        "answer": state.get("answer", ""),
        "used_evidence": used,
        "evidence_count": len(state.get("evidence", [])),
        "all_evidence": state.get("evidence", []),
        "retrieval": {k: v for k, v in retrieval.items() if k != "evidence"},
        "notices": notices,
        "mode_notice": m.notice,
        "risk_disclaimer": profile.RISKY_DISCLAIMER if profile.RISKY_DOMAIN else "",
        "collection": report.to_dict() if report else None,
        "timings": timings,
    }
