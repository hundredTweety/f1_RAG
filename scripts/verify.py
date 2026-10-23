"""명세서 4장 검증을 실행 결과로 남긴다 → docs/results/verification_report.md

자동으로 확인할 수 있는 항목(4.1~4.3, 4.4 일부, 4.7 일부)을 실제 색인·모델로 실행한다.
화면 캡처가 필요한 항목은 '미확인(화면 캡처)'으로 남기고, 캡처 후 docs/03_검증_결과.md 에 기록한다.

사용법
  python scripts/verify.py
  python scripts/verify.py --race 2021_22 --question "마지막 랩 세이프티카 재출발은 왜 논란이 됐나?"
"""

import argparse
import time
from datetime import datetime

import _bootstrap  # noqa: F401
import chromadb
from langchain_core.documents import Document

from f1rag import config
from f1rag.devtools.secret_scan import scan
from f1rag.domain import profile
from f1rag.domain.races import build_item_text, item_metadata, load_races
from f1rag.rag.retriever import retrieve
from f1rag.runtime import build_runtime
from f1rag.services.explore import explore
from f1rag.services.qa import ask
from f1rag.store.chunking import chunk_wiki_page
from f1rag.store.vectorstore import Stores

PASS, FAIL, NA = "통과", "실패", "미확인"


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str]] = []
        self.details: list[str] = []

    def check(self, section: str, item: str, ok: bool | None, evidence: str) -> None:
        status = NA if ok is None else (PASS if ok else FAIL)
        self.rows.append((section, item, status, evidence.replace("\n", " ").replace("|", "/")))
        print(f"[{status}] {section} {item}: {evidence[:120]}")

    def detail(self, title: str, body: str) -> None:
        self.details += [f"### {title}", "", "```text", body.strip(), "```", ""]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--race", default="2021_22")
    parser.add_argument("--question", default="마지막 랩 세이프티카 재출발은 왜 논란이 됐나?")
    parser.add_argument("--out", default=str(config.ROOT / "docs" / "results" / "verification_report.md"))
    args = parser.parse_args()

    rt = build_runtime()
    rep = Report()
    s = rt.settings

    # ------------------------------------------------------------ 4.1 데이터와 색인
    races = load_races(s.path("races_json"))
    n_items = rt.stores.counts()["items"]
    rep.check("4.1", "로드한 항목 수 = 색인 청크 수", len(races) == n_items and n_items >= 100, f"경기 {len(races)}건, 청크 {n_items}개")
    docs = [Document(id=r["race_id"], page_content=build_item_text(r), metadata=item_metadata(r)) for r in races]
    rt.stores.upsert(rt.stores.items, docs)
    again = rt.stores.counts()["items"]
    rep.check("4.1", "색인을 다시 실행해도 청크 수 유지", again == n_items, f"재실행 전 {n_items} → 후 {again}")
    race = rt.races.get(args.race)
    text = build_item_text(race) if race else ""
    needed = ["시즌", "라운드", "서킷", "우승", "2위", "3위", "예선 1위", "1번 그리드 출발", "완주하지 못한 차"]
    rep.check("4.1", "항목 1건의 색인 텍스트에 속성이 모두 있음", all(w in text for w in needed), f"{args.race}: " + ", ".join(w for w in needed if w in text))
    rep.detail(f"4.1 색인 텍스트 ({args.race})", text)

    # ------------------------------------------------------------ 4.2 수집과 색인
    item = rt.stores.get_item(args.race)
    t0 = time.perf_counter()
    report = rt.collector.collect(item, args.question)
    t_first = time.perf_counter() - t0
    rep.check("4.2", "프롬프트 1이 뽑은 검색어", bool(report.queries), ", ".join(q["query"] for q in report.queries))
    selected = [c for c in report.candidates if c.selected]
    dropped = [c for c in report.candidates if not c.selected]
    if report.candidates:
        rep.check("4.2", "선별된 문서와 버려진 문서 구분", True, f"색인 {len(selected)}건, 제외 {len(dropped)}건")
    else:
        rep.check("4.2", "선별된 문서와 버려진 문서 구분", None, "이미 수집한 질문이라 새 후보가 없음. --question 에 처음 묻는 질문을 넣어 다시 실행")
    rep.detail(
        "4.2 후보 선별 결과",
        "\n".join(f"{'색인' if c.selected else '제외'} | {c.title} ({c.lang}) | 검색어: {c.query} | {c.reason}" for c in report.candidates)
        or "새 후보 없음",
    )
    t0 = time.perf_counter()
    report2 = rt.collector.collect(item, args.question)
    t_second = time.perf_counter() - t0
    rep.check(
        "4.2", "같은 질문을 다시 물으면 수집하지 않음 (NF2002)",
        not report2.indexed and all(q["cached"] for q in report2.queries),
        f"첫 수집 {t_first:.1f}초, 재질문 {t_second:.1f}초, 새로 색인 {len(report2.indexed)}건",
    )
    item_docs = [d for d in rt.stores.docs_for_item(args.race) if d["is_item_doc"]]
    rep.check("4.2", "선택한 항목 자체 문서가 색인됨", bool(item_docs), ", ".join(d["title"] for d in item_docs) or "없음")

    target = (race or {}).get("item_docs", [{}])[0] if race and race.get("item_docs") else None
    page = rt.source.fetch(target["title"], target["lang"]) if target else None
    if page:
        counts = []
        temp = Stores(s.with_overrides({"collections.docs": "verify_tmp_docs", "collections.items": "verify_tmp_items"}),
                      rt.embeddings, client=chromadb.EphemeralClient())
        for size, overlap in ((500, 100), (1000, 200), (2000, 300)):
            chunks = chunk_wiki_page(page, args.race, int(item.metadata["season"]), chunk_size=size, chunk_overlap=overlap)
            temp.delete_docs({"race_id": args.race})
            temp.upsert(temp.docs, chunks)
            counts.append((size, overlap, temp.counts()["docs"]))
        rep.check("4.2", "청크 크기·중복 길이를 바꾸면 청크 수가 달라짐", len({c for *_, c in counts}) > 1,
                  "; ".join(f"{a}/{b}자 → {c}개" for a, b, c in counts))
    else:
        rep.check("4.2", "청크 크기·중복 길이를 바꾸면 청크 수가 달라짐", None, "항목 자체 문서를 받지 못함")

    # ------------------------------------------------------------ 4.3 답변과 근거
    lens = []
    for k in (3, 6, 10):
        ev = retrieve(rt, item, args.question, top_k=k)["evidence"]
        lens.append((k, len(ev)))
    rep.check("4.3", "Top-k를 바꾸면 근거 목록이 달라짐", len({n for _, n in lens}) > 1, "; ".join(f"k={k} → 근거 {n}개" for k, n in lens))
    nodes = [n for n in rt.graph.get_graph().nodes if not n.startswith("__")]
    rep.check("4.3", "LangGraph가 retrieve·generate 두 노드", nodes == ["retrieve", "generate"], " → ".join(nodes))
    rep.detail("4.3 그래프 구조 (Mermaid)", rt.graph.get_graph().draw_mermaid())

    res = ask(rt, args.race, args.question, "qa")
    import re as _re

    body_nums = sorted({int(n) for n in _re.findall(r"근거\s*(\d+)", res["answer"])})
    shown = sorted(e["n"] for e in res["used_evidence"])
    rep.check("4.3", "답변의 근거 번호 = 화면 근거 목록", bool(shown) and set(shown) <= set(body_nums), f"본문 {body_nums}, 표시 {shown}")
    kinds = {e["source_type"] for e in res["all_evidence"]}
    rep.check("4.3", "근거에 메타데이터와 외부 문서가 구분됨", "race_meta" in kinds and len(kinds) > 1,
              ", ".join(profile.SOURCE_LABELS.get(k, k) for k in sorted(kinds)))
    rep.detail("4.3 답변", res["answer"] + "\n\n" + "\n".join(f"[근거 {e['n']}] {e['title']} ({e['source_label']}) {e['location']}" for e in res["used_evidence"]))
    unknown = ask(rt, args.race, "이 경기 관중석 입장권의 평균 가격은 얼마였나?", "qa")
    said = profile.NOT_FOUND_PHRASE[:6] in unknown["answer"] or "확인되지 않" in unknown["answer"]
    rep.check("4.3", "문서에 없는 질문에 확인되지 않는다고 답함", said, unknown["answer"][:150])
    rep.check("4.3", "인용 근거가 없을 때 안내 표시", None, "tests/test_collect_and_answer.py::test_no_citation_shows_notice 로 확인 + 화면 캡처")

    # ------------------------------------------------------------ 4.4 서비스 기능 (자동 확인 가능한 부분)
    ruling = ask(rt, args.race, args.question, "ruling")
    rep.check("4.4", "도메인 특화 기능(판정 해설)에 근거가 함께 제시됨", bool(ruling["used_evidence"]),
              f"근거 {len(ruling['used_evidence'])}건, 규정 근거 {sum(e['source_type'] == 'fia_regulation' for e in ruling['used_evidence'])}건")
    # 결과가 있는 조건과, 결과가 0건이 맞는지 확인할 조건(최종전)을 함께 실행한다.
    # 사람이 확인하기 쉽게 각 경기의 예선 1위·1번 그리드·우승을 함께 적는다.
    def facts(m):
        return f"예선 1위 {m.get('pole') or '-'}, 1번 그리드 {m.get('grid_one') or '-'}, 우승 {m.get('winner') or '-'}"

    for cond in (profile.EXPLORE_EXAMPLES[1], profile.EXPLORE_EXAMPLES[0]):
        ex = explore(rt, cond)
        rep.check("4.4", f"조건 탐색 '{cond}' (조건에 맞지 않는 항목이 없는지 사람이 확인)", None,
                  f"후보 {ex['candidates']}건 중 {len(ex['matches'])}건: " + (", ".join(m["label"] for m in ex["matches"]) or "없음"))
        out = [f"{m['label']} ({facts(m)}): {m['reason']}" for m in ex["matches"]] or ["결과 없음"]
        if ex.get("required"):
            out += ["", "후보에 반드시 넣어 확인한 경기:", *[f"- {r['label']} ({facts(r)})" for r in ex["required"]]]
        rep.detail(f"4.4 조건 탐색: {cond}", "\n".join(out))
    for item_txt in ("항목을 바꿨을 때 이전 대화가 섞이지 않음", "진행 상태 표시", "색인 없음·API 키 없음 안내"):
        rep.check("4.4", item_txt, None, "화면 캡처로 확인")
    rep.check("4.4", "위험 도메인 면책 안내", True if not profile.RISKY_DOMAIN else None, "F1은 위험 도메인이 아님(해당 없음). 판정 해설에 참고 안내 표시")

    # ------------------------------------------------------------ 4.7 비기능 (자동 확인 가능한 부분)
    runs = [("질문하기", res), ("판정 해설", ruling), ("문서에 없는 질문", unknown)]
    times = [r["timings"].get("total", 0) for _, r in runs]
    rep.check("4.7", "NF2001 대표 질문 처리 시간", max(times) < 30,
              ", ".join(f"{name} {r['timings'].get('total', 0):.1f}초" for name, r in runs) + " (기준: 30초 미만)")
    rep.detail("4.7 단계별 처리 시간 (초)", "\n".join(
        f"{name}: 수집 {r['timings'].get('collect', 0)} / 검색 {r['timings'].get('retrieve', 0)} / "
        f"답변 생성 {r['timings'].get('generate', 0)} / 전체 {r['timings'].get('total', 0)}" for name, r in runs))
    rep.check("4.7", "NF2002 수집 캐시", not report2.indexed, f"재질문 시 새 색인 {len(report2.indexed)}건")
    hits = scan(config.ROOT)
    rep.check("4.7", "NF2003 소스에 API 키 없음", not hits, "; ".join(hits) or "검출 없음")

    # ------------------------------------------------------------ 저장
    lines = [
        "# 검증 실행 결과 (scripts/verify.py)",
        "",
        f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M')} / 모델 {s.get('models.chat')} / 경기 {args.race} / 질문 \"{args.question}\""
        + (" / **가짜 모델 모드(형식 확인용)**" if config.fake_models_enabled() else ""),
        "",
        "| 절 | 확인 항목 | 결과 | 근거 |",
        "| --- | --- | --- | --- |",
        *[f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rep.rows],
        "",
        "## 실행 출력",
        "",
        *rep.details,
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
