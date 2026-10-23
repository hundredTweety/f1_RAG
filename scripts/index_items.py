"""항목 메타데이터 색인 (F2001, F2002, RAG 1).

경기 1건 = 청크 1개, 청크 ID = 경기 ID(예: 2021_22). 다시 실행해도 덮어쓰기만 일어난다.

사용법
  python scripts/index_items.py                # 색인
  python scripts/index_items.py --show 2021_22 # 경기 1건의 색인 텍스트 출력 (4.1 확인용)
"""

import argparse

import _bootstrap  # noqa: F401
from langchain_core.documents import Document

from f1rag.domain.races import build_item_text, item_metadata, load_races
from f1rag.runtime import build_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", help="색인 텍스트를 출력할 경기 ID")
    args = parser.parse_args()

    rt = build_runtime()
    races = load_races(rt.settings.path("races_json"))
    if not races:
        raise SystemExit("data/processed/races.json 이 없습니다. 먼저 python scripts/build_races.py 를 실행하세요.")

    if args.show:
        race = next((r for r in races if r["race_id"] == args.show), None)
        if not race:
            raise SystemExit(f"{args.show} 경기를 찾지 못했습니다.")
        print(build_item_text(race))
        print("\n메타데이터:", item_metadata(race))
        return

    before = rt.stores.counts()["items"]
    docs = [Document(id=r["race_id"], page_content=build_item_text(r), metadata=item_metadata(r)) for r in races]
    rt.stores.upsert(rt.stores.items, docs)
    after = rt.stores.counts()["items"]
    print(f"로드한 경기 {len(races)}건 → 컬렉션 '{rt.settings.get('collections.items')}' 청크 {before} → {after}개")
    if after != len(races):
        print("주의: 로드한 경기 수와 청크 수가 다릅니다. 이전 시즌 설정으로 색인한 청크가 남아 있을 수 있습니다.")


if __name__ == "__main__":
    main()
