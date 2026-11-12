"""모든 경기의 '항목 자체 문서'를 미리 수집·색인한다 (선택, 첫 질문 응답 시간 단축).

--export-offline 을 주면 받은 문서를 data/offline_docs 에도 저장해,
외부 호출이 막힌 환경에서 settings.yaml 의 collection.offline: true 로 같은 문서를 쓸 수 있다.

사용법
  python scripts/collect_item_docs.py
  python scripts/collect_item_docs.py --limit 5 --export-offline
"""

import argparse

import _bootstrap  # noqa: F401

from f1rag.rag.collector import CollectionReport
from f1rag.runtime import build_runtime
from f1rag.sources.offline import export_page


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--export-offline", action="store_true")
    args = parser.parse_args()

    rt = build_runtime()
    items = rt.stores.list_items()
    if args.limit:
        items = items[: args.limit]
    out_dir = rt.settings.path("offline_docs_dir")
    for meta in items:
        item = rt.stores.get_item(meta["race_id"])
        report = CollectionReport()
        rt.collector.ensure_item_docs(item.metadata, rt.races.get(meta["race_id"], {}), report)
        status = ", ".join(f"{d.title}({d.chunks}청크)" for d in report.indexed) or (
            "이미 수집됨" if report.item_docs_cached else "문서 없음"
        )
        print(f"{meta['race_id']} {meta['title']}: {status}")
        if args.export_offline:
            for target in rt.collector.item_doc_targets(item.metadata, rt.races.get(meta["race_id"], {})):
                page = rt.source.fetch(target["title"], target["lang"])
                if page:
                    export_page(out_dir, page)
    print(f"외부 문서 컬렉션 청크 {rt.stores.counts()['docs']}개")


if __name__ == "__main__":
    main()
