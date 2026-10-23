"""테스트 공통 준비: 가짜 모델 + 임시 Chroma + 오프라인 문서 묶음.

실제 OpenAI·위키백과를 부르지 않고 파이프라인의 흐름과 규칙(ID, 캐시, 필터, 근거 표시)을 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from f1rag import config
from f1rag.devtools.fakes import HashingEmbeddings, RuleBasedChatModel
from f1rag.domain import races as R
from f1rag.runtime import build_runtime
from f1rag.sources.offline import OfflineSource

FIX = Path(__file__).parent / "fixtures"


class FakeJolpica:
    """Jolpica 응답 형식을 흉내 내는 가짜 HTTP 세션."""

    def __init__(self):
        self.data = json.loads((FIX / "jolpica_sample.json").read_text(encoding="utf-8"))
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append(url)
        season = url.rstrip("/").split("/")[-2]
        block = self.data[season]
        if url.endswith("/races/"):
            return {"MRData": {"total": str(len(block["schedule"])), "RaceTable": {"Races": block["schedule"]}}}
        if url.endswith("/qualifying/"):
            races = [{"round": rnd, "QualifyingResults": res} for rnd, res in block.get("qualifying", {}).items()]
            return {"MRData": {"total": str(len(races)), "RaceTable": {"Races": races}}}
        races = [{"round": rnd, "Results": res} for rnd, res in block["results"].items()]
        return {"MRData": {"total": str(sum(len(r["Results"]) for r in races)), "RaceTable": {"Races": races}}}


@pytest.fixture
def sample_races():
    http = FakeJolpica()
    races = R.fetch_season(http, 2021) + R.fetch_season(http, 2023)
    docs = {
        "2021_22": [{"lang": "ko", "title": "2021년 아부다비 그랑프리"}],
        "2021_10": [],  # 자체 문서가 정해지지 않은 경기: 검색으로 찾는 경로를 시험
        "2023_02": [{"lang": "ko", "title": "2023년 사우디아라비아 그랑프리"}],
    }
    for r in races:
        r["item_docs"] = docs[r["race_id"]]
    return races


@pytest.fixture
def settings(tmp_path, sample_races, monkeypatch):
    monkeypatch.delenv("F1RAG_FAKE_MODELS", raising=False)
    s = config.load_settings().with_overrides(
        {
            "paths.chroma_dir": str(tmp_path / "chroma"),
            "paths.cache_dir": str(tmp_path / "cache"),
            "paths.races_json": str(tmp_path / "races.json"),
            "paths.logs_dir": str(tmp_path / "logs"),
            "paths.offline_docs_dir": str(FIX / "offline_docs"),
            "paths.regulations_dir": str(tmp_path / "regs"),
            "collection.offline": True,
            "retrieval.top_k": 4,
            "collection.min_doc_chars": 50,  # 테스트 문서는 짧다
            "collection.languages": ["ko", "en"],  # 테스트 문서 묶음은 한국어 위주라 두 언어 모드로 시험
        }
    )
    R.save_races(sample_races, s.path("races_json"))
    return s


def index_items(rt, races):
    docs = [Document(id=r["race_id"], page_content=R.build_item_text(r), metadata=R.item_metadata(r)) for r in races]
    rt.stores.upsert(rt.stores.items, docs)


@pytest.fixture
def rt(settings, sample_races):
    runtime = build_runtime(
        settings,
        llm=RuleBasedChatModel(),
        embeddings=HashingEmbeddings(),
        source=OfflineSource(settings.path("offline_docs_dir")),
    )
    index_items(runtime, sample_races)
    return runtime


def make_regulation_pdf(path: Path, season: int = 2021) -> Path:
    """FIA 규정과 같은 형식(목차, 쪽마다 머리말·꼬리말, '48) SAFETY CAR' 조항)의 작은 PDF."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.set_font("Helvetica", size=9)

    def page(lines):
        pdf.add_page()
        pdf.set_xy(10, 8)
        pdf.cell(0, 5, f"{season} Formula 1 Sporting Regulations", new_x="LMARGIN", new_y="NEXT")
        for line in lines:
            pdf.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
        pdf.set_xy(10, 285)
        pdf.cell(0, 5, f"(c){season} Federation Internationale de l'Automobile  Page {pdf.page_no()}")

    page(["CONTENTS", "1 REGULATIONS 2", "6 WORLD CHAMPIONSHIP 2", "48 SAFETY CAR 3"])
    page(
        [
            "1) REGULATIONS",
            "1.1 The FIA will establish the following regulations.",
            "2) GENERAL UNDERTAKING",
            "2.1 All drivers undertake to observe all provisions.",
            "6) WORLD CHAMPIONSHIP",
            "6.4 Points for both titles will be awarded at each Competition according to the scale.",
            "a) 1st 25 points",
            "1) this numbered list item must not become an article",
        ]
    )
    words = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split()
    long_sub = [f"48.{i} Safety car step {words[i - 1]}. " + f"Cars must follow the safety car in phase {words[i - 1]}. " * 3
                for i in range(1, 16)]
    page(["7) DANGEROUS CONDUCT", "7.1 Drivers must not drive dangerously.", "48) SAFETY CAR"] + long_sub[:8])
    page(long_sub[8:] + ["49) VIRTUAL SAFETY CAR", "49.1 The VSC may be used when double yellow flags are needed."])
    page([
        "50) PRE-SPRINT AND PRE-RACE PARC FERMÉ",  # 실제 2023 규정 40조처럼 악센트 문자가 있는 제목
        "50.1 Cars are held in parc ferme conditions before the race.",
        "51) The grid",  # 제목 인식에 실패하는 경우 (소문자)
        "51.1 The starting grid will be published by the stewards.",
        "51.2 Any car unable to start must be reported.",
    ])
    pdf.output(str(path))
    return path
