"""LLM·임베딩 생성. 키는 환경변수에서만 읽는다 (NF2003).

모델 두 개를 나눠 쓴다.
- chat: 답변 생성(프롬프트 3)과 평가 채점(F2015). 품질이 가장 중요하다.
- chat_fast: 검색어 추출·후보 선별·조건 탐색. 짧은 JSON만 내므로 가볍고 빠른 모델이면 충분하다.
"""

from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from f1rag import config
from f1rag.errors import MissingApiKeyError


def get_chat_model(settings, role: str = "main") -> BaseChatModel:
    if config.fake_models_enabled():
        from f1rag.devtools.fakes import RuleBasedChatModel

        return RuleBasedChatModel()
    key = config.openai_api_key()
    if not key:
        raise MissingApiKeyError()
    from langchain_openai import ChatOpenAI

    model = settings.get("models.chat_fast") if role == "fast" else None
    kwargs = dict(
        model=model or settings.get("models.chat"),
        api_key=key,
        timeout=float(settings.get("models.timeout_sec", 120)),
        max_retries=int(settings.get("models.max_retries", 4)),
    )
    if config.openai_base_url():
        kwargs["base_url"] = config.openai_base_url()
    if settings.get("models.use_responses_api"):
        kwargs["use_responses_api"] = True
    if settings.get("models.reasoning_effort"):
        kwargs["reasoning_effort"] = settings.get("models.reasoning_effort")
    temperature = settings.get("models.temperature")
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    return ChatOpenAI(**kwargs)


def uses_separate_fast_model(settings) -> bool:
    fast = settings.get("models.chat_fast")
    return bool(fast) and fast != settings.get("models.chat")


def get_embeddings(settings) -> Embeddings:
    if config.fake_models_enabled():
        from f1rag.devtools.fakes import HashingEmbeddings

        return HashingEmbeddings()
    key = config.openai_api_key()
    if not key:
        raise MissingApiKeyError()
    from langchain_openai import OpenAIEmbeddings

    kwargs = dict(
        model=settings.get("models.embedding"),
        api_key=key,
        timeout=float(settings.get("models.timeout_sec", 120)),
        max_retries=int(settings.get("models.max_retries", 4)),
    )
    if config.openai_base_url():
        kwargs["base_url"] = config.openai_base_url()
    return CachedQueryEmbeddings(OpenAIEmbeddings(**kwargs))


class CachedQueryEmbeddings(Embeddings):
    """같은 검색 문장의 임베딩을 다시 요청하지 않는다.

    한 질문에 용어별 검색·일반 검색·규정 검색이 같은 영어 검색 문장을 여러 번 쓰고,
    compare_retrieval.py는 같은 문장으로 수십 번 검색한다. GMS가 잠깐 느려질 때
    (실제 실행에서 임베딩 요청이 504 Gateway Timeout) 요청 수를 줄이는 효과도 있다.
    """

    def __init__(self, inner: Embeddings, max_items: int = 512):
        self.inner, self.max_items, self._cache = inner, max_items, {}

    def embed_query(self, text: str) -> list[float]:
        if text not in self._cache:
            if len(self._cache) >= self.max_items:
                self._cache.pop(next(iter(self._cache)))
            self._cache[text] = self.inner.embed_query(text)
        return self._cache[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents(texts)


def effective_settings(settings):
    """가짜 모델 모드에서는 실제 색인·수집 캐시와 섞이지 않도록 폴더를 따로 쓴다 (벡터 차원도 다름)."""
    if config.fake_models_enabled():
        return settings.with_overrides(
            {
                "paths.chroma_dir": str(settings.get("paths.chroma_dir")) + "_fake",
                "paths.cache_dir": str(settings.get("paths.cache_dir")) + "_fake",
            }
        )
    return settings
