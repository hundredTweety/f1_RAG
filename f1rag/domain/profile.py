"""도메인 프로필: F1에 묶인 문구·규칙을 한곳에 모은다 (NF2005).

RAG 파이프라인(f1rag.rag), 화면(f1rag.ui, web/)은 이 파일의 값만 읽는다.
다른 도메인으로 바꿀 때는 f1rag/domain 폴더만 교체하면 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SERVICE_NAME = "F1 레이스 노트"
SERVICE_TAGLINE = "경기 기록 · 위키백과 · FIA 규정을 근거로 답하는 F1 지식 서비스"
ITEM_NOUN = "경기"

# 프롬프트 1·3에서 쓰는 역할 (F2003 "도메인 전문가 역할")
EXPERT_ROLE = (
    "너는 2021~2025 시즌 F1 경기 기록과 FIA 스포팅 규정을 잘 아는 F1 전문 해설위원이다. "
    "경기 결과, 레이스 운영(세이프티카, 페널티, 포인트), 규정 조항을 정확하게 구분해서 말한다."
)

SOURCE_LABELS = {
    "race_meta": "경기 메타데이터",
    "wikipedia": "위키백과",
    "fia_regulation": "FIA 스포팅 규정",
    "fia_decision": "FIA 심판 결정문",
}

# 판단이 위험한 도메인(건강·금융)인가? F1은 아니므로 필수 면책은 없다 (2.1).
RISKY_DOMAIN = False
RISKY_DISCLAIMER = ""

# 판정 해설·사실 판정 결과가 공식 판정처럼 읽히지 않도록 붙이는 안내
RULING_NOTICE = "이 해설은 공개된 규정과 문서를 근거로 한 참고 설명이며, FIA의 공식 판정이나 해석이 아닙니다."

NOT_FOUND_PHRASE = "제공된 문서에서 확인되지 않습니다"


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    placeholder: str
    instructions: str
    regulation_setting: str  # 이 기능에서 따로 확보할 규정 조항 수를 읽을 설정 키
    notice: str = ""


MODES: dict[str, Mode] = {
    "qa": Mode(
        key="qa",
        label="질문하기",
        placeholder="예: 이 경기에서 세이프티카가 나온 이유는?",
        instructions=(
            "질문에 바로 답한다. 숫자·이름·날짜는 근거에 적힌 그대로 쓴다. "
            "두세 문단 이내로 쓴다."
        ),
        regulation_setting="retrieval.regulation_k_qa",
    ),
    "ruling": Mode(
        key="ruling",
        label="판정 해설",
        placeholder="예: 마지막 랩 세이프티카 재출발 절차는 규정에 맞았나?",
        instructions=(
            "경기 중 사건을 그 시즌 FIA 스포팅 규정에 비추어 해설한다. 아래 네 소제목을 그대로 쓴다.\n"
            "### 상황\n무슨 일이 있었는지 근거로 요약한다.\n"
            "### 관련 규정\n적용되는 조항 번호와 내용을 근거 번호와 함께 쓴다. 근거에 규정 조항이 없으면 그렇다고 쓴다.\n"
            "### 실제 판정과 해석\n실제로 내려진 판정과, 규정에 비추어 논점이 무엇인지 쓴다.\n"
            "### 문서로 확인되지 않는 부분\n근거만으로는 판단할 수 없는 점을 쓴다. 없으면 '없음'."
        ),
        regulation_setting="retrieval.regulation_k",
        notice=RULING_NOTICE,
    ),
    "factcheck": Mode(
        key="factcheck",
        label="사실 판정",
        placeholder="예: 이 경기에서 우승자는 폴 포지션에서 출발했다",
        instructions=(
            "사용자의 문장(F1에 관한 주장)이 사실인지 근거로 판정한다.\n"
            "첫 줄은 반드시 '판정: 사실', '판정: 거짓', '판정: 일부 사실', '판정: 확인 불가' 중 하나로 쓴다.\n"
            "그다음 판정 이유를 근거 번호와 함께 쓴다. 근거로 확인할 수 없으면 '확인 불가'로 판정한다."
        ),
        regulation_setting="retrieval.regulation_k",
        notice=RULING_NOTICE,
    ),
}

QUESTION_EXAMPLES = [
    "2021 아부다비 그랑프리 마지막 랩 세이프티카 재출발은 왜 논란이 됐나?",
    "이 경기의 우승자는 몇 번 그리드에서 출발했나?",
    "이 시즌 스프린트에서는 몇 위까지 포인트를 받았나?",
]

EXPLORE_EXAMPLES = [
    "1번 그리드에서 출발했지만 우승하지 못한 시즌 최종전",
    "예선 1위가 아닌 드라이버가 1번 그리드에서 출발한 경기",
    "2024 시즌 스프린트가 열린 미국 경기",
    "페르난도 알론소가 포디움에 오른 경기",
]


# ---------------------------------------------------------------- 검색 범위 규칙


def doc_filter(item_meta: dict, languages: list[str] | None = None) -> dict:
    """경기 문서 검색 범위: 선택한 경기에 수집된 위키백과·심판 결정문만.

    race_id 필터가 빠지면 다른 경기의 문서가 근거에 섞인다 (2.3 예시와 같은 이유).
    규정은 아래 regulation_filter 로 따로 검색한다. 한 번에 검색하면 시즌 규정 청크(100개 이상)가
    경기 문서(수십 개)보다 많아 Top-k 자리를 차지하고, 질문과 무관한 조항이 근거에 들어온다.
    """
    if not languages:
        return {"race_id": item_meta["race_id"]}
    # 설정에서 뺀 언어의 청크(예: 예전에 수집한 한국어 문서)는 근거에 넣지 않는다
    return {"$and": [{"race_id": item_meta["race_id"]}, {"lang": {"$in": list(languages)}}]}


def english_doc_filter(item_meta: dict) -> dict:
    """그 경기의 영어 문서(영어 위키백과·심판 결정문)만."""
    return {"$and": [{"race_id": item_meta["race_id"]}, {"lang": "en"}]}


def regulation_filter(item_meta: dict) -> dict:
    """규정은 경기와 무관하게 시즌 단위. 다른 시즌 규정이 섞이면 틀린 조항을 인용한다."""
    return {"$and": [{"source_type": "fia_regulation"}, {"season": int(item_meta["season"])}]}


# 한국어 F1 용어 -> 영어 문서(영어 위키백과, FIA 규정·결정문)에 나오는 용어.
# 질문은 한국어인데 영어 문서를 한국어 질문으로 의미 검색하면 한국어 문서에 밀리거나 점수가 무작위에 가깝다.
# 질문에 이 용어가 있으면 그 영어 단어가 실제로 들어 있는 영어 청크 안에서 먼저 찾는다.
ENGLISH_TERMS: list[tuple[str, str]] = [
    (r"가상\s*세이프티|버추얼\s*세이프티|VSC", "virtual safety car"),
    (r"세이프티\s*카|SC\b", "safety car"),
    (r"재출발|재시작|리스타트|언랩|랩\s*다운|랩다운|랩드", "lapped"),
    (r"레드\s*플래그|적기|경기\s*중단", "red flag"),
    (r"블루\s*플래그", "blue flag"),
    (r"포인트|점수|득점", "points"),
    (r"스프린트", "sprint"),
    (r"페널티|패널티|벌칙|징계|강등", "penalty"),
    (r"트랙\s*리밋|트랙\s*한계|코스\s*이탈", "track limits"),
    (r"피트\s*레인|피트\s*스톱|피트스톱|피트", "pit lane"),
    (r"그리드|출발\s*위치", "grid"),
    (r"예선|퀄리파잉", "qualifying"),
    (r"파크\s*페르메|파르크\s*페르메|파크\s*퍼메", "parc ferm"),
    (r"타이어", "tyres"),
    (r"우천|빗|폭우|웨트", "wet"),
    (r"순위\s*인정|완주\s*인정|분류|클래시", "classified"),
]


def english_terms(text: str) -> list[str]:
    """질문 속 한국어 F1 용어를 영어 문서에 나오는 용어로 바꾼다 (중복 없이, 나온 순서대로)."""
    terms: list[str] = []
    for pattern, english in ENGLISH_TERMS:
        if re.search(pattern, text, re.IGNORECASE) and english not in terms:
            terms.append(english)
    return terms


_YEAR_RE = re.compile(r"(20(?:2[1-5]))")


# 조건 탐색에서 의미 검색 순위와 상관없이 후보에 반드시 넣을 시즌 안 위치 (F2011).
# "시즌 최종전" 조건은 의미 검색 상위 30건에 최종전 5건이 다 들어온다는 보장이 없어 따로 넣는다.
EXPLORE_SEASON_POSITIONS = {"최종전": "last", "개막전": "first"}


def explore_positions(condition: str) -> set[str]:
    return {pos for word, pos in EXPLORE_SEASON_POSITIONS.items() if word in condition}


def explore_fact_rules(condition: str) -> list:
    """메타데이터 두 값을 비교해야 하는 조건 (Chroma 필터로는 두 필드를 비교할 수 없어 직접 확인한다).

    "예선 1위가 아닌 드라이버가 1번 그리드에서 출발한 경기"는 실제 실행에서 의미 검색 상위 30건에
    해당 경기가 일부만 들어와(2022 상파울루·2024 벨기에 누락) 결과가 4건에 그쳤다.
    """
    rules = []
    if re.search(r"예선\s*1위|폴", condition) and "1번 그리드" in condition and re.search(r"아닌|다른|다름|못", condition):
        rules.append(lambda m: bool(m.get("pole")) and bool(m.get("grid_one")) and m["pole"] != m["grid_one"])
    return rules


def explore_filter(condition: str) -> dict | None:
    """조건 문장에 시즌 연도가 있으면 그 시즌으로 후보를 좁힌다 (F2011)."""
    years = sorted({int(y) for y in _YEAR_RE.findall(condition)})
    if not years:
        return None
    if len(years) == 1:
        return {"season": years[0]}
    return {"season": {"$in": years}}


def evidence_title(meta: dict) -> str:
    """근거 목록에 보여 줄 제목."""
    src = meta.get("source_type")
    if src == "fia_regulation":
        sub = meta.get("sub_articles")
        suffix = f" ({sub})" if sub and sub != meta.get("article_no") else ""
        return f"{meta.get('title', '')}{suffix}"
    if src == "wikipedia" and meta.get("section"):
        return f"{meta.get('title', '')} › {meta['section']}"
    return meta.get("title", "")


def evidence_location(meta: dict) -> str:
    """원문 위치 표시 (파일명과 쪽 또는 URL)."""
    if meta.get("source_type") in {"fia_regulation", "fia_decision"}:
        return f"{meta.get('file', '')} {meta.get('page', '')}쪽".strip()
    return meta.get("url", "")
