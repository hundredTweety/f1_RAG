"""4.2 문서 수집과 색인 (F2003~F2005), 4.3 답변과 근거 (F2006~F2008), NF2002 캐시."""

from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from f1rag.domain.races import build_item_text
from f1rag.rag.citations import cited_numbers, used_evidence
from f1rag.rag.prompts import parse_json
from f1rag.rag.retriever import merge
from f1rag.services.qa import ask
from f1rag.sources.offline import OfflineSource
from f1rag.store.chunking import chunk_wiki_page, split_sections


def test_ask_collects_item_doc_and_cites(rt):
    res = ask(rt, "2021_22", "마지막 랩 세이프티카 재출발은 왜 논란이 됐나?")
    col = res["collection"]
    item_docs = [d for d in col["indexed"] if d["is_item_doc"]]
    assert {d["title"] for d in item_docs} == {"2021년 아부다비 그랑프리", "2021 Abu Dhabi Grand Prix"}
    assert col["queries"] and all(not q["cached"] for q in col["queries"])
    assert res["all_evidence"][0]["source_type"] == "race_meta"  # 메타데이터 청크는 항상 1번
    assert res["used_evidence"], "인용된 근거만 남아야 함"
    assert {e["n"] for e in res["used_evidence"]} <= {e["n"] for e in res["all_evidence"]}
    assert set(res["timings"]) >= {"collect", "retrieve", "generate", "total"}


def test_same_question_does_not_collect_again(rt):
    q = "세이프티카 규정이 궁금해"
    first = ask(rt, "2021_22", q)
    chunks = rt.stores.counts()["docs"]
    second = ask(rt, "2021_22", q)
    assert second["collection"]["queries_from_cache"] is True
    assert all(qr["cached"] for qr in second["collection"]["queries"])
    assert second["collection"]["indexed"] == []
    assert rt.stores.counts()["docs"] == chunks
    assert first["collection"]["indexed"]


def test_item_doc_found_by_search_when_not_predefined(rt):
    res = ask(rt, "2021_10", "첫 랩 충돌 페널티는?")
    titles = [d["title"] for d in res["collection"]["indexed"] if d["is_item_doc"]]
    assert titles == ["2021 British Grand Prix"]  # 한국어 문서가 없으면 영어 문서


def test_selection_rejects_are_reported(rt):
    res = ask(rt, "2021_22", "2021년 포뮬러 원 시즌 세이프티카 사우디아라비아")
    cands = res["collection"]["candidates"]
    assert any(c["selected"] for c in cands)
    assert all(c["reason"] for c in cands if not c["selected"])


def test_chunk_ids_do_not_collide_across_items(settings):
    page = OfflineSource(settings.path("offline_docs_dir")).fetch("세이프티카", "ko")
    a = chunk_wiki_page(page, "2021_22", 2021, chunk_size=200, chunk_overlap=20)
    b = chunk_wiki_page(page, "2023_02", 2023, chunk_size=200, chunk_overlap=20)
    assert a[0].id.startswith("2021_22:kowiki-1002:") and b[0].id.startswith("2023_02:kowiki-1002:")
    assert not {d.id for d in a} & {d.id for d in b}


def test_chunk_size_setting_changes_chunk_count(settings):
    page = OfflineSource(settings.path("offline_docs_dir")).fetch("2021년 아부다비 그랑프리", "ko")
    big = chunk_wiki_page(page, "x", 2021, chunk_size=1000, chunk_overlap=200)
    small = chunk_wiki_page(page, "x", 2021, chunk_size=80, chunk_overlap=10)
    assert len(small) > len(big)
    assert all("각주" not in d.metadata["section"] for d in big)  # 근거가 안 되는 섹션 제외


def test_split_sections():
    secs = split_sections("머리말\n== 경기 ==\n본문1\n=== 세부 ===\n본문2\n== 각주 ==\n버림")
    assert [t for t, _ in secs] == ["개요", "경기", "세부"]


def test_top_k_changes_evidence(rt):
    ask(rt, "2021_22", "세이프티카 재출발 랩 다운 차량")  # 문서 수집
    item = rt.stores.get_item("2021_22")
    from f1rag.rag.retriever import retrieve

    few = retrieve(rt, item, "세이프티카", top_k=1, search_type="similarity")
    many = retrieve(rt, item, "세이프티카", top_k=5, search_type="similarity")
    assert len(few["evidence"]) == 2 and len(many["evidence"]) > 2
    mmr = retrieve(rt, item, "세이프티카", top_k=3, search_type="mmr")
    assert mmr["search_type"] == "mmr" and mmr["evidence"][0]["source_type"] == "race_meta"


def test_retrieval_stays_inside_selected_race(rt):
    ask(rt, "2021_22", "세이프티카 재출발")
    ask(rt, "2023_02", "알론소 페널티")
    from f1rag.rag.retriever import retrieve

    ev = retrieve(rt, rt.stores.get_item("2023_02"), "세이프티카 재출발 아부다비", top_k=10)["evidence"]
    assert all(not e["chunk_id"].startswith("2021_22") for e in ev[1:])


def test_merge_removes_duplicates():
    a = Document(id="1", page_content="[제목 › 개요]\n같은 본문", metadata={})
    b = Document(id="2", page_content="[다른 제목 › 개요]\n같은  본문", metadata={})
    c = Document(id="1", page_content="[제목 › 개요]\n같은 본문", metadata={})
    out, removed = merge([[a], [b, c]])
    assert [d.id for d in out] == ["1"] and removed == 2


def test_graph_has_two_nodes(rt):
    nodes = set(rt.graph.get_graph().nodes)
    assert nodes == {"__start__", "retrieve", "generate", "__end__"}


def test_citation_parsing():
    assert cited_numbers("A [근거 2] B [근거 1, 3] C [근거 4-5][근거 2]") == [2, 1, 3, 4, 5]
    # 실제 실행에서 모델이 쓴 변형 표기 (q2·q5·q9)
    assert cited_numbers("[FIA 스포팅 규정 2021 제6조 6.5][위키백과 근거 4][경기 메타데이터 근거 1]") == [4, 1]
    assert cited_numbers("[위키백과 레이스 리포트, 근거 5][근거 6]") == [5, 6]
    assert cited_numbers("[경기 메타데이터·근거 1][위키백과·근거 7] [근거 2, 근거 3]") == [1, 7, 2, 3]
    assert cited_numbers("[링크](https://x) 근거와 무관한 [대괄호 3]") == []
    ev = [{"n": 1}, {"n": 2}]
    used, invalid = used_evidence("x [근거 2][근거 9]", ev)
    assert [e["n"] for e in used] == [2] and invalid == [9]


def test_no_citation_shows_notice(rt):
    rt._llm = FakeListChatModel(responses=['["세이프티카"]', '{"selected": []}', "근거 없이 쓴 답"])
    res = ask(rt, "2021_22", "근거 없는 답을 유도하는 질문")
    assert res["used_evidence"] == []
    assert any("인용된 근거가 없습니다" in n for n in res["notices"])


def test_ruling_mode_adds_notice(rt):
    res = ask(rt, "2021_22", "재출발 절차는 규정에 맞았나?", mode="ruling")
    assert res["mode_notice"] and "공식 판정" in res["mode_notice"]


def test_parse_json_variants():
    assert parse_json('```json\n["a", "b"]\n```') == ["a", "b"]
    assert parse_json('결과: {"selected": [1, 2]} 입니다') == {"selected": [1, 2]}
    assert parse_json("형식 없음") is None


def test_item_text_used_in_query_prompt(rt, sample_races):
    race = next(r for r in sample_races if r["race_id"] == "2023_02")
    assert "세르히오 페레스" in build_item_text(race)


def test_failed_search_is_retried_next_time(rt):
    class Flaky:
        name = "flaky"

        def __init__(self, inner):
            self.inner, self.fail = inner, True

        def search(self, q, lang, limit):
            if self.fail:
                raise ConnectionError("일시 장애")
            return self.inner.search(q, lang, limit)

        def fetch(self, title, lang):
            return self.inner.fetch(title, lang)

    flaky = Flaky(rt.source)
    rt._source = flaky
    first = ask(rt, "2021_22", "세이프티카 재출발 랩 다운")
    assert any("가져오지 못해" in n for n in first["notices"])
    flaky.fail = False
    second = ask(rt, "2021_22", "세이프티카 재출발 랩 다운")
    assert not all(q["cached"] for q in second["collection"]["queries"])  # 실패한 검색어는 다시 수집
    assert any(not d["is_item_doc"] for d in second["collection"]["indexed"])



def test_regulations_do_not_crowd_out_race_docs(rt, tmp_path):
    from f1rag.domain.regulations import chunk_regulation, parse_articles
    from f1rag.rag.retriever import retrieve
    from f1rag.sources.pdf import extract_pages
    from tests.conftest import make_regulation_pdf

    arts = parse_articles(extract_pages(make_regulation_pdf(tmp_path / "2021.pdf")), 2021)
    rt.stores.upsert(rt.stores.docs, chunk_regulation(arts, url="", file_name="2021.pdf",
                                                       max_article_chars=300, chunk_size=300, chunk_overlap=0))
    ask(rt, "2021_22", "세이프티카 재출발 랩 다운")
    item = rt.stores.get_item("2021_22")
    race_chunks = len(rt.stores.docs.get(where={"race_id": "2021_22"}, include=[])["ids"])
    for mode, reg_k in (("qa", 2), ("ruling", 3)):
        ev = retrieve(rt, item, "세이프티카 재출발", mode, top_k=4)["evidence"]
        kinds = [e["source_type"] for e in ev]
        assert kinds.count("fia_regulation") == reg_k
        assert kinds.count("wikipedia") == min(4, race_chunks)  # 규정이 경기 문서 자리를 빼앗지 않음


def test_english_race_article_is_always_a_target(rt):
    race = dict(rt.races["2021_22"], wiki_en_exists=True)
    targets = rt.collector.item_doc_targets(rt.stores.get_item("2021_22").metadata, race)
    assert {t["lang"] for t in targets} == {"ko", "en"}


def _index_fixture_regulation(rt, tmp_path):
    from f1rag.domain.regulations import chunk_regulation, parse_articles
    from f1rag.sources.pdf import extract_pages
    from tests.conftest import make_regulation_pdf

    arts = parse_articles(extract_pages(make_regulation_pdf(tmp_path / "2021.pdf")), 2021)
    rt.stores.upsert(rt.stores.docs, chunk_regulation(arts, url="", file_name="2021.pdf",
                                                       max_article_chars=2500, chunk_size=1500, chunk_overlap=0))


def test_korean_question_finds_english_safety_car_article(rt, tmp_path):
    from f1rag.rag.retriever import retrieve

    _index_fixture_regulation(rt, tmp_path)
    item = rt.stores.get_item("2021_22")
    # 프롬프트 1의 영어 검색어가 없어도(이전 캐시) 용어 사전으로 세이프티카 조항을 찾는다
    res = retrieve(rt, item, "마지막 랩 세이프티카 재출발은 왜 논란이 됐나?", "ruling")
    regs = [e for e in res["evidence"] if e["source_type"] == "fia_regulation"]
    assert regs and "SAFETY CAR" in regs[0]["title"]
    assert res["english_query"] == "safety car lapped"  # 용어 사전으로 만든 영어 검색어
    # 용어가 없는 질문은 규정을 억지로 걸러내지 않고 일반 검색
    plain = retrieve(rt, item, "이 경기 분위기는 어땠나", "qa")
    assert sum(e["source_type"] == "fia_regulation" for e in plain["evidence"]) == 2


def test_prompt1_regulation_query_is_cached_and_old_cache_still_works(rt):
    first = ask(rt, "2021_22", "세이프티카 랩 다운 차량")
    assert first["retrieval"]["english_query"] == "safety car"  # 프롬프트 1의 영어 검색어
    again = ask(rt, "2021_22", "세이프티카 랩 다운 차량")
    assert again["collection"]["queries_from_cache"] and again["retrieval"]["english_query"] == "safety car"
    rt.cache.set_queries("2021_22", "예전 형식 질문", ["세이프티카"])  # 이전 버전 캐시(목록)
    old = ask(rt, "2021_22", "예전 형식 질문")
    assert old["collection"]["queries"][0]["query"] == "세이프티카"



def test_english_only_mode_uses_english_sources(rt):
    from f1rag.rag.retriever import retrieve

    ask(rt, "2021_22", "세이프티카 재출발 랩 다운")  # 두 언어 모드로 한국어·영어 문서 모두 색인된 상태
    rt.settings = rt.settings.with_overrides({"collection.languages": ["en"]})
    rt.collector.s = rt.settings
    item = rt.stores.get_item("2021_22")
    targets = rt.collector.item_doc_targets(item.metadata, rt.races["2021_22"])
    assert [t["lang"] for t in targets] == ["en"]  # 항목 자체 문서도 영어만
    ev = retrieve(rt, item, "마지막 랩 세이프티카 재출발은 왜 논란이 됐나?", "qa", top_k=3)["evidence"]
    wiki = [e for e in ev if e["source_type"] == "wikipedia"]
    assert wiki and all(":enwiki-" in e["chunk_id"] for e in wiki)  # 예전에 색인한 한국어 청크는 근거에서 제외
    assert "safety car" in wiki[0]["text"].lower()  # 한국어 질문으로 영어 원문의 해당 문단을 찾음


def test_mmr_setting_applies_to_english_term_search(rt, monkeypatch):
    """영어 용어 우선 검색 단계도 search_type을 따른다 (실제 실행에서 유사도·MMR 결과가 같았던 문제)."""
    ask(rt, "2021_22", "세이프티카 재출발 랩 다운 차량")
    item = rt.stores.get_item("2021_22")
    from f1rag.rag.retriever import retrieve

    store = rt.stores.docs
    calls = []
    real_mmr, real_sim = store.max_marginal_relevance_search, store.similarity_search

    def spy_mmr(*a, **kw):
        calls.append(("mmr", "where_document" in kw))
        return real_mmr(*a, **kw)

    def spy_sim(*a, **kw):
        calls.append(("similarity", "where_document" in kw))
        return real_sim(*a, **kw)

    monkeypatch.setattr(store, "max_marginal_relevance_search", spy_mmr)
    monkeypatch.setattr(store, "similarity_search", spy_sim)
    retrieve(rt, item, "세이프티카 재출발", "qa", top_k=3, search_type="mmr")
    assert ("mmr", True) in calls  # 용어가 든 청크 안에서 MMR
    calls.clear()
    retrieve(rt, item, "세이프티카 재출발", "qa", top_k=3, search_type="similarity")
    assert all(kind == "similarity" for kind, _ in calls)
