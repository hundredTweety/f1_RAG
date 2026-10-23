"""Streamlit 화면 (F2009 항목 선택·질의응답, F2010 특화 기능, F2011 조건 탐색, F2012 진행 상태·안내).

실행: streamlit run app.py
"""

from __future__ import annotations

import logging

import streamlit as st

from f1rag import config
from f1rag.domain import profile
from f1rag.domain.races import label as race_label
from f1rag.errors import EmptyIndexError, UserFacingError
from f1rag.logging_setup import log_exception
from f1rag.runtime import build_runtime
from f1rag.services.explore import explore
from f1rag.services.qa import ask

logger = logging.getLogger("f1rag.ui")


@st.cache_resource(show_spinner="색인을 불러오는 중…")
def get_runtime():
    return build_runtime()


def friendly_error(exc: Exception, context: str) -> str:
    """화면에는 안내 문구나 오류 번호만 보여 준다 (NF2004)."""
    if isinstance(exc, UserFacingError):
        return exc.message
    error_id = log_exception(logger, context)
    return f"요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요. (오류 번호 {error_id})"


def render_answer(result: dict) -> None:
    st.markdown(result["answer"])
    for notice in result["notices"]:
        st.warning(notice)
    if result.get("mode_notice"):
        st.caption(result["mode_notice"])
    if result.get("risk_disclaimer"):
        st.info(result["risk_disclaimer"])
    used = result["used_evidence"]
    if used:
        with st.expander(f"답변에 사용한 근거 {len(used)}건 (검색된 근거 {result['evidence_count']}건 중)", expanded=True):
            for e in used:
                link = f" · [원문]({e['url']})" if e["url"].startswith("http") else ""
                where = f" · {e['location']}" if e["source_type"].startswith("fia") and e["location"] else ""
                st.markdown(f"**[근거 {e['n']}]** {e['title']} · {e['source_label']}{where}{link}")
                body = e["text"].split("\n", 1)[-1]
                st.caption(body[:300] + ("…" if len(body) > 300 else ""))
    col = result.get("collection") or {}
    parts = [f"처리 {result['timings'].get('total', '-')}초"]
    if col.get("indexed"):
        parts.append("새로 수집: " + ", ".join(d["title"] for d in col["indexed"]))
    reused = sum(1 for q in col.get("queries", []) if q.get("cached"))
    if reused:
        parts.append(f"이미 수집한 검색어 {reused}개 재사용")
    st.caption(" / ".join(parts))


def main() -> None:
    st.set_page_config(page_title=profile.SERVICE_NAME, page_icon="🏁", layout="wide")
    ss = st.session_state
    ss.setdefault("histories", {})
    ss.setdefault("selected", None)
    ss.setdefault("explore_result", None)

    try:
        rt = get_runtime()
        counts = rt.stores.counts()
    except Exception as exc:
        st.title(profile.SERVICE_NAME)
        st.error(friendly_error(exc, "runtime"))
        st.stop()

    if config.fake_models_enabled():
        st.warning("가짜 모델 모드입니다. 답변은 화면 확인용 예시이며 사실이 아닙니다.")

    # ---------------------------------------------------------- 사이드바: 경기 선택
    with st.sidebar:
        st.header("경기 선택")
        c1, c2 = st.columns(2)
        c1.metric("경기 색인", f"{counts['items']}건", help="경기 메타데이터 컬렉션에 색인된 경기 수")
        c2.metric("문서 청크", f"{counts['docs']}개", help="위키백과·FIA 문서 컬렉션의 청크 수")
        if counts["items"] == 0:
            st.error(EmptyIndexError().message)
            st.stop()

        items = rt.stores.list_items()
        labels = {m["race_id"]: (race_label(rt.races[m["race_id"]]) if m["race_id"] in rt.races else m["title"]) for m in items}
        seasons = sorted({m["season"] for m in items})
        selected = ss.selected or items[-1]["race_id"]
        season = st.selectbox("시즌", seasons, index=seasons.index(int(selected.split("_")[0])))
        season_ids = [m["race_id"] for m in items if m["season"] == season]
        if selected not in season_ids:
            selected = season_ids[0]
        race_id = st.selectbox("질문할 경기", season_ids, index=season_ids.index(selected), format_func=labels.get)
        ss.selected = race_id
        meta = next(m for m in items if m["race_id"] == race_id)
        st.caption(f"{meta['date']} · {meta['circuit']}")
        st.caption(f"우승: {meta.get('winner') or '-'} / 예선 1위: {meta.get('pole') or '-'} / 1번 그리드: {meta.get('grid_one') or '-'}")
        docs = rt.stores.docs_for_item(race_id)
        st.markdown(f"**이 경기에 수집된 문서 {len(docs)}건**")
        if docs:
            st.caption(" · ".join(("★ " if d["is_item_doc"] else "") + d["title"] for d in docs))
        else:
            st.caption("아직 없습니다. 질문하면 관련 문서를 수집합니다.")

        with st.expander("조건으로 경기 찾기", expanded=bool(ss.explore_result)):
            condition = st.text_area("조건", placeholder=profile.EXPLORE_EXAMPLES[0], label_visibility="collapsed")
            if st.button("찾기", use_container_width=True) and condition.strip():
                with st.spinner("후보 경기를 찾고 조건에 맞는지 확인하는 중…"):
                    try:
                        ss.explore_result = explore(rt, condition)
                    except Exception as exc:
                        ss.explore_result = {"error": friendly_error(exc, "explore")}
            res = ss.explore_result
            if res:
                if res.get("error"):
                    st.error(res["error"])
                elif not res["matches"]:
                    st.info(f"후보 {res['candidates']}건 중 조건에 맞는 경기를 찾지 못했습니다.")
                    for r in res.get("required", []):
                        st.caption(f"확인한 경기 {r['label']}: 예선 1위 {r['pole'] or '-'}, 1번 그리드 {r['grid_one'] or '-'}, 우승 {r['winner'] or '-'}")
                else:
                    st.caption(f"후보 {res['candidates']}건 중 {len(res['matches'])}건이 조건에 맞습니다.")
                    for m in res["matches"]:
                        if st.button(m["label"], key=f"pick-{m['item_id']}", help=m["reason"], use_container_width=True):
                            ss.selected = m["item_id"]
                            st.rerun()
                        st.caption(m["reason"])

        with st.expander("검색 설정 (비교 실험용)"):
            top_k = st.slider("외부 문서 근거 수 (Top-k)", 1, 15, int(rt.settings.get("retrieval.top_k", 6)))
            search_type = st.radio(
                "검색 방식", ["mmr", "similarity"], horizontal=True,
                index=0 if rt.settings.get("retrieval.search_type") == "mmr" else 1,
            )

    # ---------------------------------------------------------- 본문: 대화
    st.title(labels[race_id])
    mode_key = st.radio(
        "기능", list(profile.MODES), horizontal=True, format_func=lambda k: profile.MODES[k].label, label_visibility="collapsed"
    )
    mode = profile.MODES[mode_key]
    if mode.notice:
        st.caption(mode.notice)

    history = ss.histories.setdefault(race_id, [])  # 경기별로 대화 기록을 따로 둔다 (F2009)
    for msg in history:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(f"`{msg['mode_label']}` {msg['content']}")
            elif msg.get("error"):
                st.error(msg["error"])
            else:
                render_answer(msg["result"])

    question = st.chat_input(mode.placeholder)
    if question:
        history.append({"role": "user", "content": question, "mode_label": mode.label})
        with st.chat_message("user"):
            st.markdown(f"`{mode.label}` {question}")
        with st.chat_message("assistant"):
            with st.status("처리 중…", expanded=True) as status:
                def progress(stage: str, message: str) -> None:
                    status.update(label=message)
                    st.write(message)

                try:
                    result = ask(rt, race_id, question, mode_key, progress=progress, top_k=top_k, search_type=search_type)
                    status.update(label=f"완료 ({result['timings'].get('total', '-')}초)", state="complete", expanded=False)
                except Exception as exc:
                    result = None
                    message = friendly_error(exc, "ask")
                    status.update(label="처리하지 못했습니다", state="error")
            if result:
                render_answer(result)
                history.append({"role": "assistant", "result": result})
            else:
                st.error(message)
                history.append({"role": "assistant", "error": message})
