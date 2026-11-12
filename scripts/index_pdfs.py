"""FIA 스포팅 규정·심판 결정문 PDF 색인.

- 규정: data/raw/regulations/{시즌}.pdf → 조항 단위 청크, source_type=fia_regulation, 시즌 필터용 season
- 결정문: manifest 의 decisions 항목 → source_type=fia_decision, 경기 필터용 race_id

사용법
  python scripts/index_pdfs.py
  python scripts/index_pdfs.py --show 2021 48   # 2021 규정 48조 청크 출력
  python scripts/index_pdfs.py --find "SAFETY CAR"  # 제목에 이 말이 들어간 조항 찾기 (시즌별 조항 번호 확인)
"""

import argparse

import _bootstrap  # noqa: F401

from f1rag.domain.regulations import load_decision, load_manifest, load_regulation
from f1rag.runtime import build_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", nargs=2, metavar=("SEASON", "ARTICLE"))
    parser.add_argument("--find", metavar="TITLE")
    args = parser.parse_args()

    rt = build_runtime()
    s = rt.settings
    manifest = load_manifest(s.path("pdf_manifest"))

    if args.find:
        got = rt.stores.docs.get(where={"source_type": "fia_regulation"}, include=["metadatas"])
        found = sorted({(m["season"], int(m["article_no"]) if m["article_no"].isdigit() else 0, m["article_title"])
                        for m in got["metadatas"] if args.find.lower() in m.get("article_title", "").lower()})
        for season, no, title in found:
            print(f"{season} 제{no}조 {title}")
        if not found:
            print("제목에 그 말이 들어간 조항이 없습니다. 조항 인식이 실패했을 수 있으니 --show 로 확인하세요.")
        return

    if args.show:
        season, article = int(args.show[0]), args.show[1]
        got = rt.stores.docs.get(
            where={"$and": [{"source_type": "fia_regulation"}, {"season": season}, {"article_no": article}]}
        )
        for cid, text in zip(got["ids"], got["documents"]):
            print(f"--- {cid}\n{text}\n")
        if not got["ids"]:
            print("해당 조항 청크가 없습니다.")
        return

    total = 0
    for entry in manifest["regulations"]:
        path = s.path("regulations_dir") / entry["file"]
        if not path.exists():
            print(f"없음(건너뜀): {path} — python scripts/download_pdfs.py 로 받으세요.")
            continue
        season = int(entry["season"])
        docs = load_regulation(path, season, entry.get("url", ""), s)
        rt.stores.delete_docs({"$and": [{"source_type": "fia_regulation"}, {"season": season}]})
        rt.stores.upsert(rt.stores.docs, docs)
        articles = len({d.metadata["article_no"] for d in docs})
        print(f"{season} 규정: 조항 {articles}개 → 청크 {len(docs)}개")
        total += len(docs)

    for entry in manifest["decisions"]:
        path = s.path("decisions_dir") / entry["file"]
        if not path.exists():
            print(f"없음(건너뜀): {path}")
            continue
        race_id = entry["race_id"]
        docs = load_decision(path, race_id, int(race_id.split("_")[0]), entry.get("url", ""), s)
        rt.stores.upsert(rt.stores.docs, docs)
        print(f"{race_id} 결정문 {path.name}: 청크 {len(docs)}개")
        total += len(docs)

    print(f"PDF 청크 {total}개 색인. 외부 문서 컬렉션 전체 {rt.stores.counts()['docs']}개")


if __name__ == "__main__":
    main()
