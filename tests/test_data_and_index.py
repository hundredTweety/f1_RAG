"""데이터와 색인 확인 + 규정 PDF."""

from f1rag.domain import races as R
from f1rag.domain.regulations import chunk_regulation, parse_articles
from f1rag.sources.pdf import extract_pages
from tests.conftest import index_items, make_regulation_pdf


def test_summarize_race_fields(sample_races):
    abu = next(r for r in sample_races if r["race_id"] == "2021_22")
    assert abu["name_ko"] == "아부다비 그랑프리"
    assert abu["winner"]["driver"] == "막스 베르스타펜"
    assert abu["pole_to_win"] is True
    assert abu["is_season_finale"] is True
    assert abu["retirements"] == 1  # "Oil pressure" 는 완주가 아님
    gb = next(r for r in sample_races if r["race_id"] == "2021_10")
    assert gb["has_sprint"] is True
    assert gb["pole_to_win"] is False and gb["winner"]["grid"] == 2
    # 예선 1위(해밀턴)와 1번 그리드 출발(베르스타펜)을 따로 기록
    assert gb["quali_pole"]["driver"] == "루이스 해밀턴" and gb["grid_one"]["driver"] == "막스 베르스타펜"
    text = R.build_item_text(gb)
    assert "예선 1위와 1번 그리드 출발 드라이버가 다름" in text
    assert "예선 1위 드라이버의 우승(폴 투 윈): 예" in text
    meta = R.item_metadata(gb)
    assert meta["pole"] == "루이스 해밀턴" and meta["grid_one"] == "막스 베르스타펜"
    assert "다름" not in R.build_item_text(abu)


def test_item_text_has_all_search_attributes(sample_races):
    text = R.build_item_text(next(r for r in sample_races if r["race_id"] == "2021_22"))
    for expected in ["2021 시즌", "22라운드", "아부다비 그랑프리", "Abu Dhabi Grand Prix", "Yas Marina", "아랍에미리트",
                     "시즌 최종전", "우승 막스 베르스타펜(레드불)", "2위 루이스 해밀턴", "예선 1위", "1번 그리드 출발",
                     "폴 투 윈", "완주하지 못한 차"]:
        assert expected in text, expected


def test_item_metadata_has_no_none(sample_races):
    for r in sample_races:
        meta = R.item_metadata(r)
        assert all(v is not None for v in meta.values())
        assert meta["source_type"] == "race_meta" and meta["race_id"] == r["race_id"]


def test_index_twice_does_not_duplicate(rt, sample_races):
    assert rt.stores.counts()["items"] == len(sample_races)
    index_items(rt, sample_races)
    index_items(rt, sample_races)
    assert rt.stores.counts()["items"] == len(sample_races)


def test_regulation_pdf_articles(tmp_path):
    pages = extract_pages(make_regulation_pdf(tmp_path / "2021.pdf"))
    arts = parse_articles(pages, 2021)
    numbers = [a.number for a in arts]
    assert numbers == [1, 2, 6, 7, 48, 49, 50, 51]  # 목차와 본문 속 "1) ..." 목록은 조항이 아님
    assert arts[-2].title == "PRE-SPRINT AND PRE-RACE PARC FERMÉ"  # 악센트 문자가 있는 제목
    assert "51.1" in arts[-1].text and "51.1" not in arts[-2].text  # 제목을 놓쳐도 하위 조항 번호로 나뉨
    joined = "\n".join(a.text for a in arts)
    assert "Sporting Regulations" not in joined  # 쪽마다 반복되는 머리말 제거
    assert "Federation Internationale" not in joined  # 꼬리말 제거
    safety = next(a for a in arts if a.number == 48)
    for i in range(1, 16):  # 쪽을 넘어가도 하위 조항이 빠짐없이 같은 조항으로 이어짐
        assert f"48.{i} Safety car step" in safety.text


def test_regulation_chunks_split_long_article_at_sub_articles(tmp_path):
    arts = parse_articles(extract_pages(make_regulation_pdf(tmp_path / "2021.pdf")), 2021)
    docs = chunk_regulation(arts, url="https://example.org/r.pdf", file_name="2021.pdf",
                            max_article_chars=800, chunk_size=800, chunk_overlap=0)
    ids = [d.id for d in docs]
    assert len(ids) == len(set(ids))
    sc = [d for d in docs if d.metadata["article_no"] == "48"]
    assert len(sc) > 1  # 긴 조항은 여러 청크
    for d in sc:
        assert d.page_content.startswith("[2021 FIA F1 스포팅 규정 제48조 SAFETY CAR]")
        assert d.page_content.split("\n", 1)[1].lstrip().startswith("48")  # 하위 조항 경계에서 시작
    short = [d for d in docs if d.metadata["article_no"] == "7"]
    assert len(short) == 1 and short[0].metadata["season"] == 2021
    assert short[0].metadata["url"].endswith("#page=3")
