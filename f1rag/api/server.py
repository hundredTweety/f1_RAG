"""FastAPI 서버: HTML/CSS/JS 화면(web/)과 RAG 서비스를 잇는다.

실행: uvicorn f1rag.api.server:app --port 8000  → http://localhost:8000

화면에는 API 키, 프롬프트 전문, 오류 추적 정보를 보내지 않는다.
예상하지 못한 오류는 logs/app.log 에 남기고 화면에는 오류 번호만 보낸다.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from f1rag import config
from f1rag.api.schemas import AskRequest, AskResult, ExploreRequest, ExploreResult, RaceSummary, Status
from f1rag.domain import profile
from f1rag.domain.races import label as race_label
from f1rag.errors import EmptyIndexError, MissingApiKeyError, UserFacingError
from f1rag.logging_setup import log_exception
from f1rag.services.explore import explore
from f1rag.services.qa import ask

logger = logging.getLogger("f1rag.api")
WEB = config.ROOT / "web"

app = FastAPI(title=profile.SERVICE_NAME, docs_url="/api/docs", redoc_url=None)


@lru_cache(maxsize=1)
def runtime():
    from f1rag.runtime import build_runtime

    return build_runtime()


def _user_error(exc: Exception, context: str) -> dict:
    if isinstance(exc, UserFacingError):
        return {"message": exc.message, "code": exc.code}
    error_id = log_exception(logger, context)
    return {"message": f"요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요. (오류 번호 {error_id})", "error_id": error_id}


# ---------------------------------------------------------------- 상태·목록


@app.get("/api/status", response_model=Status)
def status() -> Status:
    base = dict(
        service_name=profile.SERVICE_NAME,
        tagline=profile.SERVICE_TAGLINE,
        has_api_key=bool(config.openai_api_key()) or config.fake_models_enabled(),
        fake_models=config.fake_models_enabled(),
        modes=[{"key": m.key, "label": m.label, "placeholder": m.placeholder, "notice": m.notice} for m in profile.MODES.values()],
        source_labels=profile.SOURCE_LABELS,
        examples=profile.QUESTION_EXAMPLES,
        explore_examples=profile.EXPLORE_EXAMPLES,
    )
    try:
        counts = runtime().stores.counts()
    except MissingApiKeyError as exc:
        return Status(**base, items=0, docs=0, ready=False, message=exc.message)
    except Exception as exc:
        return Status(**base, items=0, docs=0, ready=False, message=_user_error(exc, "status")["message"])
    if counts["items"] == 0:
        return Status(**base, items=0, docs=counts["docs"], ready=False, message=EmptyIndexError().message)
    return Status(**base, items=counts["items"], docs=counts["docs"], ready=True, message="")


@app.get("/api/races", response_model=list[RaceSummary])
def races() -> list[RaceSummary]:
    try:
        rt = runtime()
        metas = rt.stores.list_items()
    except Exception as exc:
        raise HTTPException(503, _user_error(exc, "races")["message"]) from None
    out = []
    for m in metas:
        race = rt.races.get(m["race_id"])
        out.append(
            RaceSummary(
                item_id=m["race_id"],
                label=race_label(race) if race else m.get("title", m["race_id"]),
                season=int(m.get("season", 0)),
                round=int(m.get("round", 0)),
                title=m.get("title", ""),
                date=m.get("date", ""),
                circuit=m.get("circuit", ""),
                country=m.get("country", ""),
                winner=m.get("winner", ""),
                pole=m.get("pole", ""),
                grid_one=m.get("grid_one", ""),
                winner_team=((race or {}).get("winner") or {}).get("team", ""),
                laps=(race or {}).get("laps"),
                fastest_lap=((race or {}).get("fastest_lap") or {}).get("driver", ""),
                starters=(race or {}).get("starters"),
                retirements=(race or {}).get("retirements"),
            )
        )
    return out


@app.get("/api/races/{item_id}/car")
def race_car(item_id: str) -> dict:
    """우승 차 사진(위키미디어 공용, 자유 라이선스)과 출처. 못 찾거나 오류면 found=false 만 보낸다."""
    try:
        rt = runtime()
        race = rt.races.get(item_id)
        finder = rt.car_images
        if not race or not finder or not race.get("winner"):
            return {"found": False}
        res = finder.find(int(race["season"]), race["winner"].get("team_en", ""))
        return {**res, "season": race["season"], "team": race["winner"].get("team", "")}
    except Exception as exc:  # 사진은 부가 기능이라 실패해도 화면은 그대로 쓴다
        log_exception(logging.getLogger(__name__), f"race_car: {type(exc).__name__}")
        return {"found": False}


@app.get("/api/races/{item_id}/docs")
def race_docs(item_id: str) -> list[dict]:
    try:
        return runtime().stores.docs_for_item(item_id)
    except Exception as exc:
        raise HTTPException(503, _user_error(exc, "race_docs")["message"]) from None


# ---------------------------------------------------------------- 질의응답 (진행 상태를 줄 단위로 스트리밍)


def to_public(result: dict) -> dict:
    """화면에 필요한 값만 남긴다 (전체 근거·수집 내부 정보는 보내지 않음)."""
    col = result.get("collection") or {}
    public = AskResult(
        item_id=result["item_id"],
        item_label=result["item_label"],
        question=result["question"],
        mode=result["mode"],
        mode_label=result["mode_label"],
        answer=result["answer"],
        used_evidence=[{k: e[k] for k in ("n", "source_type", "source_label", "title", "location", "url", "text")} for e in result["used_evidence"]],
        evidence_count=result["evidence_count"],
        notices=result["notices"],
        mode_notice=result["mode_notice"],
        risk_disclaimer=result["risk_disclaimer"],
        collected_titles=[d["title"] for d in col.get("indexed", [])],
        reused_queries=sum(1 for q in col.get("queries", []) if q.get("cached")),
        timings=result["timings"],
    )
    return public.model_dump()


@app.post("/api/ask")
def ask_stream(req: AskRequest) -> StreamingResponse:
    events: queue.Queue = queue.Queue()

    def progress(stage: str, message: str) -> None:
        events.put({"type": "progress", "stage": stage, "message": message})

    def work() -> None:
        try:
            result = ask(runtime(), req.item_id, req.question, req.mode, progress=progress)
            events.put({"type": "result", "data": to_public(result)})
        except Exception as exc:
            events.put({"type": "error", **_user_error(exc, "ask")})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/explore", response_model=ExploreResult)
def explore_items(req: ExploreRequest):
    try:
        res = explore(runtime(), req.condition)
    except Exception as exc:
        return JSONResponse(status_code=400 if isinstance(exc, UserFacingError) else 500, content=_user_error(exc, "explore"))
    return ExploreResult(condition=res["condition"], candidates=res["candidates"], matches=res["matches"], required=res.get("required", []))


# ---------------------------------------------------------------- 정적 화면


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=Path(WEB)), name="static")
