"""실행에 필요한 객체(설정, LLM, 임베딩, 컬렉션, 문서 출처, 캐시)를 한곳에서 만든다.

화면(Streamlit, FastAPI)과 스크립트가 같은 조립 방법을 쓰도록 하고,
테스트에서는 가짜 모델·임시 Chroma·오프라인 문서 묶음을 끼워 넣을 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from f1rag import config
from f1rag.domain.races import load_races
from f1rag.errors import MissingContactError
from f1rag.logging_setup import setup_logging
from f1rag.rag.cache import CollectCache
from f1rag.rag.llm import effective_settings, get_chat_model, get_embeddings, uses_separate_fast_model
from f1rag.sources.http import PoliteSession
from f1rag.sources.offline import OfflineSource
from f1rag.sources.wiki import DocumentSource, WikipediaSource
from f1rag.store.vectorstore import Stores


@dataclass
class Runtime:
    settings: config.Settings
    embeddings: Embeddings
    stores: Stores
    cache: CollectCache
    _llm: BaseChatModel | None = None
    _source: DocumentSource | None = None
    _llm_fast: BaseChatModel | None = None
    _llm_injected: bool = False  # 테스트처럼 모델을 직접 넣었으면 두 역할 모두 그 모델을 쓴다
    races: dict[str, dict] = field(default_factory=dict)
    _car_images: Any = None  # 우승 차 사진 찾기 (화면용). 테스트에서 직접 넣을 수 있다

    @property
    def llm(self) -> BaseChatModel:
        if self._llm is None:
            self._llm = get_chat_model(self.settings)
        return self._llm

    @property
    def llm_fast(self) -> BaseChatModel:
        """검색어 추출·후보 선별·조건 탐색용. 설정에서 따로 정하지 않았으면 답변 모델과 같다."""
        if self._llm_fast is not None:
            return self._llm_fast
        if self._llm_injected or not uses_separate_fast_model(self.settings) or config.fake_models_enabled():
            return self.llm
        self._llm_fast = get_chat_model(self.settings, role="fast")
        return self._llm_fast

    @property
    def source(self) -> DocumentSource:
        """외부 문서 출처. 설정이 offline 이면 문서 묶음, 아니면 위키백과."""
        if self._source is None:
            if self.settings.get("collection.offline"):
                self._source = OfflineSource(self.settings.path("offline_docs_dir"))
            else:
                contact = config.wiki_contact()
                if not contact:
                    raise MissingContactError()
                self._source = WikipediaSource(PoliteSession.from_settings(self.settings, contact))
        return self._source

    @property
    def car_images(self):
        """우승 차 사진 찾기. 가짜 모델·오프라인 설정이거나 위키 연락처가 없으면 None (사진 없이 화면 표시)."""
        if self._car_images is None:
            if config.fake_models_enabled() or self.settings.get("collection.offline") or not config.wiki_contact():
                return None
            from f1rag.sources.car_images import CarImageFinder

            self._car_images = CarImageFinder(
                PoliteSession.from_settings(self.settings, config.wiki_contact()),
                self.settings.path("cache_dir") / "car_images.json",
            )
        return self._car_images

    @cached_property
    def collector(self):
        from f1rag.rag.collector import Collector

        return Collector(self)

    @cached_property
    def graph(self):
        from f1rag.rag.graph import build_graph

        return build_graph(self)


def build_runtime(
    settings: config.Settings | None = None,
    *,
    llm: BaseChatModel | None = None,
    embeddings: Embeddings | None = None,
    source: DocumentSource | None = None,
    chroma_client: Any = None,
    car_images: Any = None,
) -> Runtime:
    settings = effective_settings(settings or config.load_settings())
    setup_logging(settings.path("logs_dir"))
    embeddings = embeddings or get_embeddings(settings)
    stores = Stores(settings, embeddings, client=chroma_client)
    races = {r["race_id"]: r for r in load_races(settings.path("races_json"))}
    return Runtime(
        settings=settings,
        embeddings=embeddings,
        stores=stores,
        cache=CollectCache(settings.path("cache_dir") / "collect_cache.json"),
        _llm=llm,
        _llm_injected=llm is not None,
        _source=source,
        races=races,
        _car_images=car_images,
    )
