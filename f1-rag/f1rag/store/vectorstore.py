"""Vector Store 영역: 두 컬렉션 생성·조회·색인 (F2002, F2005).

- items 컬렉션(f1_races): 항목 메타데이터. 항목당 청크 1개, 청크 ID = 항목 ID.
- docs 컬렉션(f1_docs): 외부 문서(위키백과, FIA 규정, 심판 결정문) 청크.
두 컬렉션을 나눈 이유: 메타데이터 청크는 검색 없이 ID로 바로 꺼내 항상 근거에 넣고(F2006),
외부 문서는 경기·시즌 필터를 걸어 의미 검색하기 때문이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

BATCH = 100


class Stores:
    def __init__(self, settings, embeddings: Embeddings, client=None):
        self.settings = settings
        if client is None:
            path = settings.path("chroma_dir")
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
        self.client = client
        meta = {"hnsw:space": "cosine"}
        self.items = Chroma(
            collection_name=settings.get("collections.items"),
            embedding_function=embeddings,
            client=client,
            collection_metadata=meta,
        )
        self.docs = Chroma(
            collection_name=settings.get("collections.docs"),
            embedding_function=embeddings,
            client=client,
            collection_metadata=meta,
        )

    # ------------------------------------------------------------ 조회

    def counts(self) -> dict[str, int]:
        return {"items": self.items._collection.count(), "docs": self.docs._collection.count()}

    def get_item(self, item_id: str) -> Document | None:
        got = self.items.get(ids=[item_id])
        if not got["ids"]:
            return None
        return Document(id=got["ids"][0], page_content=got["documents"][0], metadata=got["metadatas"][0])

    def list_items(self) -> list[dict]:
        got = self.items.get(include=["metadatas"])
        return sorted(got["metadatas"], key=lambda m: (m.get("season", 0), m.get("round", 0)))

    def docs_for_item(self, item_id: str) -> list[dict]:
        """그 경기에 수집된 문서 목록 (청크를 문서 단위로 묶음)."""
        got = self.docs.get(where={"race_id": item_id}, include=["metadatas"])
        by_doc: dict[str, dict] = {}
        for meta in got["metadatas"]:
            entry = by_doc.setdefault(
                meta.get("doc_id", ""),
                {
                    "doc_id": meta.get("doc_id", ""),
                    "title": meta.get("title", ""),
                    "url": meta.get("url", ""),
                    "source_type": meta.get("source_type", ""),
                    "is_item_doc": bool(meta.get("is_item_doc")),
                    "chunks": 0,
                },
            )
            entry["chunks"] += 1
        return sorted(by_doc.values(), key=lambda d: (not d["is_item_doc"], d["title"]))

    def has_doc(self, item_id: str, doc_id: str) -> bool:
        got = self.docs.get(where={"$and": [{"race_id": item_id}, {"doc_id": doc_id}]}, limit=1, include=[])
        return bool(got["ids"])

    def count_where(self, where: dict) -> int:
        return len(self.docs.get(where=where, include=[])["ids"])

    # ------------------------------------------------------------ 색인

    @staticmethod
    def upsert(store: Chroma, docs: Iterable[Document]) -> int:
        """ID 기준 덮어쓰기. 같은 청크를 다시 넣어도 개수가 늘지 않는다."""
        docs = list(docs)
        for i in range(0, len(docs), BATCH):
            batch = docs[i : i + BATCH]
            store.add_texts(
                texts=[d.page_content for d in batch],
                metadatas=[_clean(d.metadata) for d in batch],
                ids=[d.id for d in batch],
            )
        return len(docs)

    def delete_docs(self, where: dict) -> None:
        ids = self.docs.get(where=where, include=[])["ids"]
        if ids:
            self.docs.delete(ids=ids)


def _clean(meta: dict) -> dict:
    """Chroma 메타데이터는 None·리스트를 허용하지 않는다."""
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


def reset_collection(stores: Stores, name: str) -> None:
    try:
        stores.client.delete_collection(name)
    except Exception:  # 없으면 무시
        pass


def chroma_path_exists(settings) -> bool:
    return Path(settings.path("chroma_dir")).exists()
