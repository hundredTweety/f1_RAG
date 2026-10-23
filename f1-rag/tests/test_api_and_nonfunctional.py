"""F2011 조건 탐색, F2012 안내, F2013 API, NF2003·NF2004·NF2006."""

import json

import pytest
import requests
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from f1rag import config
from f1rag.api import server
from f1rag.devtools.secret_scan import scan
from f1rag.errors import EmptyIndexError, MissingApiKeyError
from f1rag.rag.llm import get_chat_model
from f1rag.services.explore import explore
from f1rag.services.qa import ask
from f1rag.sources.http import PoliteSession


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr(server, "runtime", lambda: rt)
    return TestClient(server.app)


def read_stream(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_status_and_races(client):
    s = client.get("/api/status").json()
    assert s["ready"] and s["items"] == 3
    assert {m["key"] for m in s["modes"]} == {"qa", "ruling", "factcheck"}
    races = client.get("/api/races").json()
    assert [r["item_id"] for r in races] == ["2021_10", "2021_22", "2023_02"]
    abu = next(r for r in races if r["item_id"] == "2021_22")
    # 차량 패널용 값: 우승 팀, 랩 수, 패스티스트 랩, 출전·리타이어 수
    assert abu["winner_team"] and abu["laps"] and abu["starters"] and abu["retirements"] is not None


def test_ask_streams_progress_then_result(client):
    resp = client.post("/api/ask", json={"item_id": "2021_22", "question": "세이프티카 재출발", "mode": "ruling"})
    events = read_stream(resp)
    assert events[0]["type"] == "progress" and events[-1]["type"] == "result"
    stages = [e["stage"] for e in events if e["type"] == "progress"]
    assert "collect" in stages and "generate" in stages
    data = events[-1]["data"]
    assert data["used_evidence"] and data["mode_notice"]
    assert "all_evidence" not in data and "prompt" not in json.dumps(data)  # 내부 정보 비노출


def test_ask_validation(client):
    assert client.post("/api/ask", json={"item_id": "2021_22", "question": ""}).status_code == 422
    assert client.post("/api/ask", json={"item_id": "2021_22", "question": "x", "mode": "hack"}).status_code == 422


def test_unexpected_error_is_masked(client, monkeypatch):
    def boom(*a, **k):
        fake_key = "sk-" + "x" * 24  # 키 검사(test_no_api_keys_in_source)에 걸리지 않도록 실행 중에 만든다
        raise RuntimeError(f"내부 정보 {fake_key} Traceback")

    monkeypatch.setattr(server, "ask", boom)
    events = read_stream(client.post("/api/ask", json={"item_id": "2021_22", "question": "q"}))
    err = events[-1]
    assert err["type"] == "error" and "오류 번호" in err["message"]
    assert "sk-" not in json.dumps(err) and "Traceback" not in json.dumps(err)


def test_unknown_item_message(client):
    events = read_stream(client.post("/api/ask", json={"item_id": "1999_01", "question": "q"}))
    assert events[-1]["type"] == "error" and "찾을 수 없습니다" in events[-1]["message"]


def test_status_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("F1RAG_FAKE_MODELS", raising=False)
    with pytest.raises(MissingApiKeyError):
        get_chat_model(config.load_settings())

    def no_key():
        raise MissingApiKeyError()

    monkeypatch.setattr(server, "runtime", no_key)
    s = TestClient(server.app).get("/api/status").json()
    assert s["ready"] is False and "OPENAI_API_KEY" in s["message"]


def test_empty_index_message(rt):
    rt.stores.items.delete(ids=["2021_10", "2021_22", "2023_02"])
    with pytest.raises(EmptyIndexError) as e:
        ask(rt, "2021_22", "q")
    assert "index_items.py" in e.value.message


def test_explore_returns_only_candidates(rt):
    res = explore(rt, "1번 그리드에서 출발했지만 우승하지 못한 경기")
    assert res["matches"] and all(m["item_id"] in {"2021_10", "2021_22", "2023_02"} for m in res["matches"])
    rt._llm = FakeListChatModel(responses=['{"matches": [{"id": "2099_01", "reason": "지어낸 경기"}, {"id": "2021_10", "reason": "맞음"}]}'])
    res = explore(rt, "아무 조건")
    assert [m["item_id"] for m in res["matches"]] == ["2021_10"]


def test_explore_year_filter(rt):
    res = explore(rt, "2023 시즌 경기")
    assert res["season_filter"] == {"season": 2023} and res["candidates"] == 1


def test_explore_api(client):
    data = client.post("/api/explore", json={"condition": "2021 시즌 최종전"}).json()
    assert data["candidates"] == 2 and data["matches"]


def test_polite_session_retries_and_user_agent(monkeypatch):
    http = PoliteSession(contact="me@example.com", min_interval_sec=0, backoff_sec=0)
    assert "me@example.com" in http._session.headers["User-Agent"]
    calls = []

    class Resp:
        def __init__(self, code):
            self.status_code, self.headers = code, {"Retry-After": "0"}

        def json(self):
            return {"ok": True}

    def fake_get(url, params=None, timeout=None, stream=False):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("끊김")
        return Resp(429) if len(calls) == 2 else Resp(200)

    monkeypatch.setattr(http._session, "get", fake_get)
    assert http.get_json("https://example.org/x") == {"ok": True}
    assert len(calls) == 3


def test_no_api_keys_in_source():
    assert scan(config.ROOT) == []


def test_missing_wiki_contact_is_reported(settings, sample_races, monkeypatch):
    from f1rag.devtools.fakes import HashingEmbeddings, RuleBasedChatModel
    from f1rag.errors import MissingContactError
    from f1rag.runtime import build_runtime
    from tests.conftest import index_items

    monkeypatch.delenv("WIKI_CONTACT", raising=False)
    online = settings.with_overrides({"collection.offline": False})
    runtime = build_runtime(online, llm=RuleBasedChatModel(), embeddings=HashingEmbeddings())
    index_items(runtime, sample_races)
    with pytest.raises(MissingContactError):  # 수집 단계가 삼키지 않고 화면 안내로 올라온다
        ask(runtime, "2021_22", "세이프티카")


def test_gms_model_settings_build_responses_request(monkeypatch):
    monkeypatch.delenv("F1RAG_FAKE_MODELS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GMS_KEY", "test-key")  # GMS 안내의 변수 이름도 받는다
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1")
    s = config.load_settings()
    llm = get_chat_model(s)
    payload = llm._get_request_payload([("human", "hi")])
    assert payload["model"] == s.get("models.chat")
    assert payload["reasoning"] == {"effort": s.get("models.reasoning_effort")}
    assert "temperature" not in payload and llm.use_responses_api
    assert str(llm.openai_api_base).startswith("https://gms.ssafy.io/gmsapi/")


def test_fast_model_is_separate_when_configured(monkeypatch):
    monkeypatch.delenv("F1RAG_FAKE_MODELS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from f1rag.rag.llm import uses_separate_fast_model

    s = config.load_settings()
    assert uses_separate_fast_model(s)
    fast = get_chat_model(s, role="fast")
    assert fast.model_name == s.get("models.chat_fast") and get_chat_model(s).model_name == s.get("models.chat")


def test_explore_always_checks_season_finales(rt):
    """'최종전' 조건이면 의미 검색 순위와 상관없이 각 시즌 마지막 라운드가 후보에 들어간다."""
    got = rt.stores.items.get(include=["metadatas"])
    last = {}
    for meta in got["metadatas"]:
        if meta["round"] > last.get(meta["season"], (0, ""))[0]:
            last[meta["season"]] = (meta["round"], meta["race_id"])
    expected = {rid for _, rid in last.values()}
    res = explore(rt, "1번 그리드에서 출발했지만 우승하지 못한 시즌 최종전", k=1)
    assert {r["item_id"] for r in res["required"]} == expected
    assert res["candidates"] >= len(expected)
    assert all({"pole", "grid_one", "winner"} <= set(r) for r in res["required"])
    assert explore(rt, "2023 시즌 경기")["required"] == []


def test_explore_checks_every_race_where_pole_differs_from_grid_one(rt):
    """'예선 1위가 아닌 드라이버가 1번 그리드' 조건이면 두 값이 다른 경기를 모두 후보에 넣는다."""
    got = rt.stores.items.get(include=["metadatas"])
    expected = {m["race_id"] for m in got["metadatas"] if m.get("pole") and m.get("grid_one") and m["pole"] != m["grid_one"]}
    assert expected, "샘플 데이터에 예선 1위와 1번 그리드가 다른 경기가 있어야 한다"
    res = explore(rt, "예선 1위가 아닌 드라이버가 1번 그리드에서 출발한 경기", k=1)
    assert expected <= {r["item_id"] for r in res["required"]}


def test_same_search_sentence_is_embedded_once():
    """같은 검색 문장은 임베딩을 한 번만 요청한다 (GMS 요청 수 줄이기)."""
    from f1rag.rag.llm import CachedQueryEmbeddings

    calls = []

    class Inner:
        def embed_query(self, text):
            calls.append(text)
            return [float(len(text))]

        def embed_documents(self, texts):
            return [[1.0] for _ in texts]

    emb = CachedQueryEmbeddings(Inner())
    for _ in range(5):
        emb.embed_query("safety car restart lapped cars")
    assert calls == ["safety car restart lapped cars"]
    assert emb.embed_documents(["a", "b"]) == [[1.0], [1.0]]


class FakeWikiHttp:
    """위키백과 API 응답을 흉내 낸다: 챔피언십 문서의 링크 → 차 문서 첫 문장 → 대표 사진 → 사진 파일 정보."""

    PAGES = {
        "Red Bull Racing RB16": ("Formula One racing car", "The Red Bull Racing RB16 is a Formula One racing car to compete in the 2020 Formula One World Championship. It was updated as the RB16B for 2021."),
        "Red Bull Racing RB16B": ("Formula One racing car", "The Red Bull Racing RB16B is a Formula One racing car to compete in the 2021 Formula One World Championship."),
        "Mercedes-AMG F1 W12 E Performance": ("Formula One racing car", "The W12 competed in the 2021 Formula One World Championship."),
        "Max Verstappen": ("Dutch racing driver", "Max Emilian Verstappen ..."),
        "RB VCARB 01": ("Formula One racing car", "The VCARB 01 ... 2024 Formula One World Championship."),
    }

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get_json(self, url, params=None):
        self.calls.append(params)
        if self.fail:
            raise requests.ConnectionError("끊김")
        if params.get("generator") == "links":
            # 두 번에 나눠 오는 링크 목록
            first = "continue" not in str(params.get("gplcontinue", ""))  and "gplcontinue" not in params
            names = ["Max Verstappen", "Red Bull Racing RB16"] if first else ["Red Bull Racing RB16B", "Mercedes-AMG F1 W12 E Performance", "RB VCARB 01"]
            data = {"query": {"pages": [{"title": n, "pageprops": {"wikibase-shortdesc": self.PAGES[n][0]}} for n in names]}}
            if first:
                data["continue"] = {"gplcontinue": "next", "continue": "gplcontinue||"}
            return data
        if params.get("prop") == "imageinfo":
            return {"query": {"pages": [{"imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/x/960px-RB16B.jpg", "thumbwidth": 960, "thumbheight": 540,
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:RB16B.jpg",
                "extmetadata": {"Artist": {"value": '<a href="//x">Jane Doe</a>'}, "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]}]}}
        if params.get("prop") == "pageimages":
            return {"query": {"pages": [{"title": params["titles"], "pageimage": params["titles"].replace(" ", "_") + ".jpg"}]}}
        titles = params["titles"].split("|")
        return {"query": {"pages": [{"title": t, "pageprops": {"wikibase-shortdesc": self.PAGES[t][0]}, "extract": self.PAGES[t][1]} for t in titles]}}


def test_car_photo_is_season_car_with_free_license_and_cached(tmp_path):
    from f1rag.sources.car_images import CarImageFinder

    http = FakeWikiHttp()
    finder = CarImageFinder(http, tmp_path / "car_images.json")
    res = finder.find(2021, "Red Bull")
    # 챔피언십 문서의 링크 중 그 팀 차, 같은 팀 차가 둘이면(RB16, RB16B) 그 시즌 차를 고르고,
    # 작성자·라이선스를 출처로 남긴다 (HTML 태그 제거)
    assert res["found"] and res["car"] == "Red Bull Racing RB16B"
    assert res["artist"] == "Jane Doe" and res["license"] == "CC BY-SA 4.0" and res["image"].startswith("https://upload.wikimedia.org/")
    assert any(c.get("pilicense") == "free" for c in http.calls)
    n = len(http.calls)
    assert CarImageFinder(http, tmp_path / "car_images.json").find(2021, "Red Bull") == res and len(http.calls) == n  # 캐시
    # 네트워크 오류는 캐시하지 않는다
    with pytest.raises(requests.ConnectionError):
        CarImageFinder(FakeWikiHttp(fail=True), tmp_path / "other.json").find(2022, "Ferrari")
    assert not (tmp_path / "other.json").exists()
    # 팀 이름 일치: 'RB F1 Team'은 'RB VCARB 01'과 맞고 'Red Bull Racing ...'과는 맞지 않는다
    from f1rag.sources.car_images import title_matches_team
    assert title_matches_team("RB VCARB 01", "RB F1 Team") and not title_matches_team("Red Bull Racing RB20", "RB F1 Team")
    assert title_matches_team("Mercedes-AMG F1 W12 E Performance", "Mercedes") and title_matches_team("Alpine A521", "Alpine F1 Team")


def test_car_endpoint_without_finder_is_quiet(client):
    assert client.get("/api/races/2021_22/car").json() == {"found": False}


class RedirectWikiHttp(FakeWikiHttp):
    """2021 맥라렌: 챔피언십 문서는 'McLaren MCL35M'을 링크하지만 위키백과에서는 'McLaren MCL35'로 넘겨준다."""

    def get_json(self, url, params=None):
        self.calls.append(params)
        if params.get("generator") == "links":
            return {"query": {
                "redirects": [{"from": "McLaren MCL35M", "to": "McLaren MCL35"}],
                "pages": [{"title": "McLaren MCL35", "pageprops": {"wikibase-shortdesc": "Formula One racing car"}}],
            }}
        if params.get("prop") == "images":
            return {"query": {"pages": [{"title": "McLaren MCL35", "images": [
                {"title": "File:McLaren MCL35 Barcelona 2020.jpg"}, {"title": "File:Norris McLaren MCL35M Monza 2021.jpg"}]}]}}
        if params.get("prop") == "imageinfo":
            data = super().get_json(url, params)
            data["query"]["pages"][0]["imageinfo"][0]["thumburl"] = "https://upload.wikimedia.org/x/" + params["titles"].split(":", 1)[1]
            return data
        return super().get_json(url, params)


def test_car_photo_uses_variant_name_and_variant_photo(tmp_path):
    from f1rag.sources.car_images import CarImageFinder

    res = CarImageFinder(RedirectWikiHttp(), tmp_path / "c.json").find(2021, "McLaren")
    assert res["found"] and res["car"] == "McLaren MCL35M" and res["photo_of"] == ""
    assert res["image"].endswith("Norris McLaren MCL35M Monza 2021.jpg")


def test_long_official_name_is_not_a_variant():
    """'Mercedes-AMG F1 W12 E Performance' → 'Mercedes W12' 넘겨주기는 같은 차라 기본형 안내를 붙이지 않는다."""
    from f1rag.sources.car_images import CarImageFinder

    class LongName(FakeWikiHttp):
        def get_json(self, url, params=None):
            if params.get("generator") == "links":
                self.calls.append(params)
                return {"query": {"redirects": [{"from": "Mercedes-AMG F1 W12 E Performance", "to": "Mercedes W12"}],
                                  "pages": [{"title": "Mercedes W12", "pageprops": {"wikibase-shortdesc": "Formula One racing car"}}]}}
            return super().get_json(url, params)

    import tempfile, pathlib
    res = CarImageFinder(LongName(), pathlib.Path(tempfile.mkdtemp()) / "c.json").find(2021, "Mercedes")
    assert res["found"] and res["car"] == "Mercedes W12" and res["photo_of"] == ""


def test_cache_save_survives_windows_file_lock(tmp_path, monkeypatch):
    """윈도우에서 다른 프로그램이 캐시 파일을 열고 있으면 바꾸기가 거부된다(WinError 5). 다시 시도하고, 끝내 실패해도 답변은 계속된다."""
    import os
    from f1rag.rag.cache import CollectCache
    from f1rag.store import jsonfile

    real_replace, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "액세스가 거부되었습니다")
        return real_replace(src, dst)

    monkeypatch.setattr(jsonfile.os, "replace", flaky)
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    cache = CollectCache(tmp_path / "collect_cache.json")
    cache.mark_query("2021_22", "safety car", ["en:1"], 3)  # 두 번 거부된 뒤 저장
    assert CollectCache(tmp_path / "collect_cache.json").query_done("2021_22", "safety car")

    monkeypatch.setattr(jsonfile.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(5, "거부")))
    cache.mark_query("2021_22", "red flag", [], 0)  # 예외가 올라오지 않는다
    assert cache.query_done("2021_22", "red flag")
    assert not list(tmp_path.glob("*.tmp"))  # 임시 파일을 남기지 않는다


def test_two_servers_do_not_erase_each_others_cache(tmp_path):
    """Streamlit과 FastAPI 서버를 같이 켜 두면 둘이 같은 캐시 파일을 쓴다. 저장할 때 상대가 쓴 항목을 지우지 않는다."""
    from f1rag.rag.cache import CollectCache

    a, b = CollectCache(tmp_path / "c.json"), CollectCache(tmp_path / "c.json")
    a.mark_query("2021_22", "safety car", [], 1)
    b.mark_query("2023_02", "alonso penalty", [], 1)
    fresh = CollectCache(tmp_path / "c.json")
    assert fresh.query_done("2021_22", "safety car") and fresh.query_done("2023_02", "alonso penalty")
