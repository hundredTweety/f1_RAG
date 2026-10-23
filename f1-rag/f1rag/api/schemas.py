"""화면-서버 요청·응답 형식 (F2013)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    item_id: str = Field(..., description="경기 ID, 예: 2021_22")
    question: str = Field(..., min_length=1, max_length=500)
    mode: Literal["qa", "ruling", "factcheck"] = "qa"


class ExploreRequest(BaseModel):
    condition: str = Field(..., min_length=1, max_length=300)


class Evidence(BaseModel):
    n: int
    source_type: str
    source_label: str
    title: str
    location: str
    url: str
    text: str


class AskResult(BaseModel):
    item_id: str
    item_label: str
    question: str
    mode: str
    mode_label: str
    answer: str
    used_evidence: list[Evidence]
    evidence_count: int
    notices: list[str]
    mode_notice: str
    risk_disclaimer: str
    collected_titles: list[str]
    reused_queries: int
    timings: dict[str, float]


class ExploreMatch(BaseModel):
    item_id: str
    label: str
    date: str
    circuit: str
    winner: str
    pole: str
    grid_one: str = ""
    reason: str


class ExploreChecked(BaseModel):
    """조건에 '최종전'·'개막전'이 있어 반드시 확인한 경기 (결과가 0건일 때 이유를 보여 주려고)."""

    item_id: str
    label: str
    pole: str = ""
    grid_one: str = ""
    winner: str = ""


class ExploreResult(BaseModel):
    condition: str
    candidates: int
    matches: list[ExploreMatch]
    required: list[ExploreChecked] = []


class RaceSummary(BaseModel):
    item_id: str
    label: str
    season: int
    round: int
    title: str
    date: str
    circuit: str
    country: str
    winner: str
    pole: str       # 예선 1위
    grid_one: str   # 1번 그리드 출발
    # 아래는 화면의 차량 패널(우승 차 주변 숫자)에 쓰는 값. races.json 에 없으면 빈 값
    winner_team: str = ""
    laps: int | None = None
    fastest_lap: str = ""
    starters: int | None = None
    retirements: int | None = None


class Status(BaseModel):
    service_name: str
    tagline: str
    items: int
    docs: int
    ready: bool
    has_api_key: bool
    fake_models: bool
    message: str
    modes: list[dict]
    source_labels: dict[str, str]
    examples: list[str]
    explore_examples: list[str]


# 스트리밍 응답(/api/ask)은 한 줄에 JSON 하나씩 보낸다 (NDJSON).
#   {"type": "progress", "stage": "collect", "message": "..."}
#   {"type": "result", "data": AskResult}
#   {"type": "error", "message": "...", "error_id": "..."}
