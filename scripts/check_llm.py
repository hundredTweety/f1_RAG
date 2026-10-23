"""LLM·임베딩 연결 점검. 색인 전에 한 번 실행한다.

.env 의 키·주소와 settings.yaml 의 모델 이름으로 짧은 요청을 보내고 응답 시간과 오류를 알려 준다.

사용법
  python scripts/check_llm.py
"""

import time

import _bootstrap  # noqa: F401

from f1rag import config
from f1rag.rag.llm import get_chat_model, get_embeddings, uses_separate_fast_model

HINTS = {
    "401": "키가 틀렸습니다. .env 의 OPENAI_API_KEY(또는 GMS_KEY)를 확인하세요.",
    "404": "주소나 모델 이름이 맞지 않습니다. OPENAI_BASE_URL 과 settings.yaml 의 models 를 확인하세요.",
    "model": "모델 이름을 GMS 모델 목록과 비교하세요.",
    "reasoning": "이 모델이 reasoning_effort 값을 받지 않습니다. settings.yaml 의 models.reasoning_effort 를 null 로 바꾸세요.",
    "temperature": "이 모델이 temperature 를 받지 않습니다. settings.yaml 의 models.temperature 를 null 로 두세요.",
    "responses": "Responses API 를 쓸 수 없는 주소입니다. settings.yaml 의 models.use_responses_api 를 false 로 바꿔 보세요.",
}


def hint(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for key, msg in HINTS.items():
        if key in text.lower() or key in text:
            return msg
    return "오류 내용을 확인하세요."


def main() -> None:
    s = config.load_settings()
    print(f"주소: {config.openai_base_url() or 'OpenAI 기본 주소'}")
    print(f"키: {'있음' if config.openai_api_key() else '없음'}")
    if not config.openai_api_key():
        raise SystemExit(".env 에 OPENAI_API_KEY(또는 GMS_KEY)를 넣으세요.")

    roles = [("main", s.get("models.chat"))]
    if uses_separate_fast_model(s):
        roles.append(("fast", s.get("models.chat_fast")))
    ok = True
    for role, name in roles:
        try:
            t0 = time.perf_counter()
            msg = get_chat_model(s, role).invoke('JSON 배열 ["확인"] 만 출력하라.')
            print(f"[통과] 채팅 {name}: {time.perf_counter() - t0:.1f}초, 응답 {msg.text[:40]!r}")
        except Exception as exc:  # noqa: BLE001 - 점검 스크립트는 모든 오류를 보여 준다
            ok = False
            print(f"[실패] 채팅 {name}: {type(exc).__name__}: {str(exc)[:300]}\n  → {hint(exc)}")
    try:
        t0 = time.perf_counter()
        vec = get_embeddings(s).embed_query("2021 아부다비 그랑프리")
        print(f"[통과] 임베딩 {s.get('models.embedding')}: {time.perf_counter() - t0:.1f}초, {len(vec)}차원")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[실패] 임베딩 {s.get('models.embedding')}: {type(exc).__name__}: {str(exc)[:300]}\n  → {hint(exc)}")
    print("모두 통과. 색인을 시작해도 됩니다." if ok else "실패 항목을 고친 뒤 다시 실행하세요.")


if __name__ == "__main__":
    main()
