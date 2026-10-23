"""RAG 사용 전후 답변 비교 (F2015, 4.6).

같은 질문을 ① RAG 없이(기억만으로) ② 근거를 붙여(RAG) 한 번씩 답하게 하고
세 기준으로 비교해 docs/04_RAG_전후_비교.md 를 만든다.
  - 사실 정확성: 참고 답안과 비교한 LLM 채점(정확/부분/틀림/답 회피). 사람이 최종 확인한다.
  - 근거 제시: 답변에 근거 번호가 있고 그 근거가 실제 근거 목록에 있는가
  - 모르는 것을 모른다고 답함: expect_unknown 질문에서 '확인되지 않는다'고 답했는가

사용법
  python scripts/evaluate_rag.py
  python scripts/evaluate_rag.py --only q1 q5   # 일부 질문만 다시 실행 (나머지는 이전 결과 유지)
  python scripts/evaluate_rag.py --rescore   # LLM을 다시 부르지 않고 저장된 답변(rag_eval.json)으로 판정만 다시 계산
"""

import argparse
import json
import re
from datetime import datetime

import _bootstrap  # noqa: F401
import yaml
from langchain_core.output_parsers import StrOutputParser

from f1rag import config
from f1rag.domain import profile
from f1rag.domain.races import label as race_label
from f1rag.rag import prompts
from f1rag.rag.citations import cited_numbers
from f1rag.runtime import build_runtime
from f1rag.services.qa import ask

# '모른다고 답함' 판정. "공식적으로 공표된 기록이 없다", "하나의 평균 가격을 확정할 수 없다"처럼
# 근거가 없다고 밝힌 표현을 모두 포함한다.
UNKNOWN_RE = re.compile(
    r"확인되지 않|확인할 수 없|확인 불가|알 수 없|알려지지 않|문서에 없|정보가 없|정보는 없|기록이 없|기록은 없"
    r"|공표되지 않|공개되지 않|확정할 수 없|단정할 수 없"
)
JSON_PATH = config.ROOT / "docs" / "results" / "rag_eval.json"


def find_race(rt, season: int, gp: str) -> str | None:
    for rid, race in rt.races.items():
        if race["season"] == season and race["name_en"] == gp:
            return rid
    return None


def judge(rt, question: str, reference: str, answer: str) -> dict:
    # 채점은 정확해야 하므로 경량 모델(chat_fast)이 아니라 답변 모델(chat)을 쓴다
    raw = (prompts.JUDGE | rt.llm | StrOutputParser()).invoke({"question": question, "reference": reference, "answer": answer})
    parsed = prompts.parse_json(raw)
    if isinstance(parsed, dict) and parsed.get("accuracy"):
        return {"accuracy": str(parsed["accuracy"]), "reason": str(parsed.get("reason", ""))}
    return {"accuracy": "채점 실패", "reason": raw[:120]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--out", default=str(config.ROOT / "docs" / "04_RAG_전후_비교.md"))
    parser.add_argument("--rescore", action="store_true")
    args = parser.parse_args()

    if args.rescore:
        rescore(args.out)
        return

    rt = build_runtime()
    qs = yaml.safe_load((config.ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"]
    if args.only:
        qs = [q for q in qs if q["id"] in args.only]

    rows = []
    for q in qs:
        rid = find_race(rt, int(q["season"]), q["gp"])
        if not rid:
            print(f"{q['id']}: {q['season']} {q['gp']} 경기를 races.json 에서 찾지 못해 건너뜀")
            continue
        mode = profile.MODES[q.get("mode", "qa")]
        label = race_label(rt.races[rid])
        print(f"{q['id']} {label}: {q['question']}")

        baseline = (prompts.NO_RAG_BASELINE | rt.llm | StrOutputParser()).invoke(
            {**prompts.base_vars(), "mode_instructions": mode.instructions, "item_label": label, "question": q["question"]}
        ).strip()
        rag = ask(rt, rid, q["question"], mode.key)

        expect_unknown = bool(q.get("expect_unknown"))
        rows.append(
            {
                "id": q["id"],
                "label": label,
                "mode": mode.label,
                "question": q["question"],
                "reference": q["reference"].strip(),
                "expect_unknown": expect_unknown,
                "baseline": {
                    "answer": baseline,
                    "cited": False,
                    "said_unknown": bool(UNKNOWN_RE.search(baseline)),
                    **({} if expect_unknown else judge(rt, q["question"], q["reference"], baseline)),
                },
                "rag": {
                    "answer": rag["answer"],
                    "cited": bool(rag["used_evidence"]),
                    "said_unknown": bool(UNKNOWN_RE.search(rag["answer"])),
                    "evidence": [f"[근거 {e['n']}] {e['title']} ({e['source_label']})" for e in rag["used_evidence"]],
                    "all_evidence": [
                        {"n": e["n"], "title": e["title"], "source_label": e["source_label"]} for e in rag["all_evidence"]
                    ],
                    "seconds": rag["timings"].get("total"),
                    "timings": rag["timings"],
                    **({} if expect_unknown else judge(rt, q["question"], q["reference"], rag["answer"])),
                },
            }
        )

    note = f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if args.only and JSON_PATH.exists():
        # 일부 질문만 다시 돌렸으면 기존 결과의 나머지 질문은 그대로 두고 해당 질문만 바꾼다
        new_by_id = {r["id"]: r for r in rows}
        old = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        merged = [new_by_id.pop(r["id"], r) for r in old]
        rows = merged + list(new_by_id.values())
        order = {q["id"]: i for i, q in enumerate(yaml.safe_load((config.ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"])}
        rows.sort(key=lambda r: order.get(r["id"], 999))
        note += f" (다시 실행한 질문: {', '.join(args.only)}, 나머지는 이전 결과)"
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(rows, rt.settings, args.out, note)
    print(f"저장: {args.out}, {JSON_PATH}")


def rescore(out_path: str) -> None:
    """저장된 답변으로 근거 표기·'모른다고 답함' 판정만 다시 계산한다 (LLM 호출 없음, 정확성 채점은 유지)."""
    if not JSON_PATH.exists():
        raise SystemExit(f"{JSON_PATH} 가 없습니다. 먼저 python scripts/evaluate_rag.py 를 실행하세요.")
    rows = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    for r in rows:
        rag = r["rag"]
        nums = cited_numbers(rag["answer"])
        known = {e["n"]: e for e in rag.get("all_evidence", [])}
        if known:
            rag["cited"] = any(n in known for n in nums)
            rag["evidence"] = [f"[근거 {n}] {known[n]['title']} ({known[n]['source_label']})" for n in nums if n in known]
        else:
            # 이전 버전 결과 파일: 전체 근거 목록이 없어 번호만 확인한다
            rag["cited"] = bool(nums)
            listed = set()
            for line in rag.get("evidence", []):
                m = re.match(r"\[근거 (\d+)\]", line)
                if m:
                    listed.add(int(m.group(1)))
            rag["evidence"] = list(rag.get("evidence", [])) + [
                f"[근거 {n}] (원래 실행에서 표기 형식 때문에 제목을 기록하지 못함)" for n in nums if n not in listed
            ]
        r["baseline"]["said_unknown"] = bool(UNKNOWN_RE.search(r["baseline"]["answer"]))
        rag["said_unknown"] = bool(UNKNOWN_RE.search(rag["answer"]))
    JSON_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    note = (
        f"재채점: {datetime.now().strftime('%Y-%m-%d %H:%M')} (저장된 답변으로 근거 표기와 '모른다고 답함' 판정만 다시 계산."
        " 답변 원문과 정확성 채점은 원래 실행 결과 그대로)"
    )
    write_markdown(rows, config.load_settings(), out_path, note)
    print(f"저장: {out_path}, {JSON_PATH}")


def _ok(v: bool) -> str:
    return "예" if v else "아니오"


def _timing(rag: dict) -> str:
    t = rag.get("timings") or {}
    if not t:
        return f"{rag.get('seconds', '-')}초"
    return f"{t.get('total', '-')}초 (수집 {t.get('collect', '-')} / 검색 {t.get('retrieve', '-')} / 생성 {t.get('generate', '-')})"


def write_markdown(rows: list[dict], settings, path: str, run_note: str) -> None:
    fact = [r for r in rows if not r["expect_unknown"]]
    unk = [r for r in rows if r["expect_unknown"]]

    def count(side: str, key: str, val) -> int:
        return sum(1 for r in fact if r[side].get(key) == val)

    lines = [
        "# RAG 사용 전후 답변 비교 (F2015)",
        "",
        f"{run_note} / 모델 {settings.get('models.chat')} / "
        f"검색 {settings.get('retrieval.search_type')}, Top-k {settings.get('retrieval.top_k')}"
        + (" / **가짜 모델 모드(형식 확인용, 결과는 의미 없음)**" if config.fake_models_enabled() else ""),
        "",
        "사실 정확성은 참고 답안과 비교한 LLM 채점이며, 표의 각 행은 사람이 답변 원문을 읽고 최종 확인한다.",
        "",
        "## 요약",
        "",
        "| 기준 | RAG 없이 | RAG 사용 |",
        "| --- | --- | --- |",
        f"| 사실 정확성: 정확 (질문 {len(fact)}개 중) | {count('baseline', 'accuracy', '정확')} | {count('rag', 'accuracy', '정확')} |",
        f"| 사실 정확성: 틀림 | {count('baseline', 'accuracy', '틀림')} | {count('rag', 'accuracy', '틀림')} |",
        f"| 근거 제시 (질문 {len(rows)}개 중) | 0 | {sum(r['rag']['cited'] for r in rows)} |",
        f"| 모르는 것을 모른다고 답함 (질문 {len(unk)}개 중) | {sum(r['baseline']['said_unknown'] for r in unk)} | {sum(r['rag']['said_unknown'] for r in unk)} |",
        "",
        "## 질문별 결과",
        "",
        "| ID | 경기 | 기능 | RAG 없이 | RAG 사용 | RAG 근거 제시 | RAG 처리 시간 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        if r["expect_unknown"]:
            b = "모른다고 답함" if r["baseline"]["said_unknown"] else "지어냄 의심"
            g = "모른다고 답함" if r["rag"]["said_unknown"] else "지어냄 의심"
        else:
            b, g = r["baseline"].get("accuracy", "-"), r["rag"].get("accuracy", "-")
        lines.append(f"| {r['id']} | {r['label']} | {r['mode']} | {b} | {g} | {_ok(r['rag']['cited'])} | {_timing(r['rag'])} |")
    lines += ["", "## 답변 원문", ""]
    for r in rows:
        base_score = f" (채점: {r['baseline'].get('accuracy')}, {r['baseline'].get('reason', '')})" if not r["expect_unknown"] else ""
        rag_score = f" (채점: {r['rag'].get('accuracy')}, {r['rag'].get('reason', '')})" if not r["expect_unknown"] else ""
        lines += [
            f"### {r['id']}. {r['label']} — {r['question']}",
            "",
            f"**참고 답안**: {r['reference']}",
            "",
            "**RAG 없이**" + base_score,
            "",
            "> " + r["baseline"]["answer"].replace("\n", "\n> "),
            "",
            "**RAG 사용**" + rag_score + f", {_timing(r['rag'])}",
            "",
            "> " + r["rag"]["answer"].replace("\n", "\n> "),
            "",
            "사용한 근거: " + ("; ".join(r["rag"]["evidence"]) or "없음"),
            "",
        ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
