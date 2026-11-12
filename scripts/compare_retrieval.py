"""검색 설정 비교 (유사도 vs MMR λ 0.5·0.7, Top-k).

이미 색인된 문서만 쓰고 외부 수집은 하지 않는다. 비교할 경기에 대해 먼저 질문을 한 번씩 해 두거나
scripts/verify.py 를 실행해 문서를 수집해 둔다.

지표
  - 근거 목록 (제목 › 섹션 / 조항)
  - 서로 다른 문서 수: 클수록 여러 출처에서 근거를 가져옴
  - 근거끼리 평균 코사인 유사도: 낮을수록 내용이 덜 겹침
  - 출처 종류 분포

사용법
  python scripts/compare_retrieval.py
  python scripts/compare_retrieval.py --race 2021_22 --question "세이프티카 재출발 규정"
"""

import argparse
import itertools
import math
from collections import Counter
from datetime import datetime

import _bootstrap  # noqa: F401

from f1rag import config
from f1rag.domain.races import label as race_label
from f1rag.rag.retriever import retrieve
from f1rag.runtime import build_runtime

DEFAULT_CASES = [
    ("2021_22", "마지막 랩 세이프티카 재출발 절차는 규정에 맞았나?"),
    ("2023_02", "알론소가 받은 페널티와 재심 결과는?"),
]


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def diversity(rt, evidence: list[dict]) -> tuple[int, float, Counter]:
    ext = evidence[1:]  # 1번은 항상 메타데이터
    docs = {e["chunk_id"].rsplit(":", 1)[0] for e in ext}
    vecs = rt.embeddings.embed_documents([e["text"] for e in ext]) if len(ext) > 1 else []
    pairs = list(itertools.combinations(vecs, 2))
    avg = sum(cosine(a, b) for a, b in pairs) / len(pairs) if pairs else 0.0
    return len(docs), avg, Counter(e["source_label"] for e in ext)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--race")
    parser.add_argument("--question")
    parser.add_argument("--out", default=str(config.ROOT / "docs" / "results" / "retrieval_comparison.md"))
    args = parser.parse_args()
    rt = build_runtime()
    cases = [(args.race, args.question)] if args.race and args.question else DEFAULT_CASES

    lines = [
        "# 검색 설정 비교 (Top-k)",
        "",
        f"실행: {datetime.now().strftime('%Y-%m-%d %H:%M')} / fetch_k {rt.settings.get('retrieval.fetch_k')}, "
        f"설정 mmr_lambda {rt.settings.get('retrieval.mmr_lambda')} (Top-k 표에 사용)"
        + (" / **가짜 모델 모드(형식 확인용)**" if config.fake_models_enabled() else ""),
        "",
    ]
    for race_id, question in cases:
        item = rt.stores.get_item(race_id)
        if item is None:
            print(f"{race_id} 경기가 색인에 없어 건너뜀")
            continue
        label = race_label(rt.races[race_id]) if race_id in rt.races else race_id
        lines += [f"## {label}: {question}", ""]

        # 1) 유사도 vs MMR (같은 k, λ 0.5와 0.7)
        k = int(rt.settings.get("retrieval.top_k", 6))
        methods = [("유사도", "similarity", None), ("MMR λ0.5", "mmr", 0.5), ("MMR λ0.7", "mmr", 0.7)]
        lines += [f"### 유사도 검색 vs MMR (Top-k {k})", "", "| 방식 | 서로 다른 문서 수 | 근거 간 평균 유사도 | 출처 종류 | 유사도 검색에 없는 근거 |", "| --- | --- | --- | --- | --- |"]
        lists = {}
        for name, st, lam in methods:
            lists[name] = retrieve(rt, item, question, "ruling", top_k=k, search_type=st, mmr_lambda=lam)["evidence"]
        base_ids = {e["chunk_id"] for e in lists["유사도"]}
        for name, _, _ in methods:
            ev = lists[name]
            n_docs, avg, kinds = diversity(rt, ev)
            new_n = len({e["chunk_id"] for e in ev} - base_ids)
            lines.append(f"| {name} | {n_docs} | {avg:.3f} | {', '.join(f'{k2} {v}' for k2, v in kinds.items())} | {new_n if name != '유사도' else '-'} |")
        names = [m[0] for m in methods]
        lines += ["", "| 순위 | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
        for i in range(max(len(v) for v in lists.values())):
            row = [lists[n][i]["title"] if i < len(lists[n]) else "" for n in names]
            lines.append(f"| {i + 1} | " + " | ".join(row) + " |")
        lines.append("")

        # 2) Top-k 바꾸기
        lines += ["### Top-k 바꾸기 (설정의 검색 방식)", "", "| Top-k | 근거 수(메타 포함) | 서로 다른 문서 수 | 평균 유사도 |", "| --- | --- | --- | --- |"]
        for tk in (3, 6, 10):
            ev = retrieve(rt, item, question, "qa", top_k=tk)["evidence"]
            n_docs, avg, _ = diversity(rt, ev)
            lines.append(f"| {tk} | {len(ev)} | {n_docs} | {avg:.3f} |")
        lines.append("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"저장: {args.out}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # GMS 일시 장애는 추적 정보 대신 다시 실행 안내만 보인다
        import openai

        if isinstance(exc, (openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError)):
            raise SystemExit(f"GMS 서버가 응답하지 않았습니다 ({type(exc).__name__}). 재시도 후에도 실패했으니 잠시 뒤 다시 실행하세요.")
        raise
