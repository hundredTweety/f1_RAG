"""개발·테스트용 가짜 모델 (API 키 없이 화면과 파이프라인 흐름을 확인할 때만 사용).

F1RAG_FAKE_MODELS=1 일 때만 쓰인다. 답변 내용은 실제 사실이 아니며, 화면 상단에 '가짜 모델 모드'가 표시된다.
- HashingEmbeddings: 글자 묶음(n-gram)을 해시해 만든 벡터. 같은 단어가 많이 겹치면 가깝게 나온다.
- RuleBasedChatModel: 프롬프트의 '작업:' 줄을 보고 형식만 맞춘 응답을 돌려준다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


class HashingEmbeddings(Embeddings):
    def __init__(self, size: int = 384):
        self.size = size

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.size
        for word in _WORD.findall(text.lower()):
            grams = [word] + [word[i : i + 2] for i in range(len(word) - 1)]
            for g in grams:
                h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
                v[h % self.size] += 1.0 if (h >> 8) % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class RuleBasedChatModel(BaseChatModel):
    """프롬프트 종류별로 형식만 맞춘 가짜 응답."""

    calls: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "rule-based-fake"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        system = "\n".join(str(m.content) for m in messages if m.type == "system")
        human = "\n".join(str(m.content) for m in messages if m.type == "human")
        task = re.search(r"작업:\s*(.+)", system)
        task = task.group(1).strip() if task else ""
        self.calls.append(task)
        text = self._respond(task, system, human)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _respond(self, task: str, system: str, human: str) -> str:
        if task.startswith("검색어 추출"):
            q = re.search(r"질문:\s*(.+)", human)
            words = [w for w in _WORD.findall(q.group(1) if q else "") if len(w) >= 2][:3]
            return json.dumps({"wiki": words or ["포뮬러 원"], "english": "safety car"}, ensure_ascii=False)
        if task.startswith("후보 문서 선별"):
            nums = [int(n) for n in re.findall(r"^\[(\d+)\]", human, re.MULTILINE)]
            return json.dumps({"selected": nums[:2]})
        if task.startswith("조건에 맞는"):
            ids = re.findall(r"^- id:\s*(\S+)", human, re.MULTILINE)
            return json.dumps({"matches": [{"id": i, "reason": "가짜 모델: 후보 상위 결과"} for i in ids[:3]]}, ensure_ascii=False)
        if task.startswith("근거 기반 답변"):
            nums = re.findall(r"^\[근거 (\d+)\]", human, re.MULTILINE)
            cites = "".join(f"[근거 {n}]" for n in nums[:2])
            head = "판정: 확인 불가\n" if "판정: 사실" in system else ""
            if not nums:
                return head + "제공된 문서에서 확인되지 않습니다."
            return head + f"(가짜 모델 응답) 제공된 근거를 바탕으로 정리한 예시 답변입니다. {cites}"
        if task.startswith("답변 채점"):
            return json.dumps({"accuracy": "부분", "reason": "가짜 모델 채점"}, ensure_ascii=False)
        return "(가짜 모델 응답) RAG 없이 기억만으로 쓴 예시 답변입니다."
