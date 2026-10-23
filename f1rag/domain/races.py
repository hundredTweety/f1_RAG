"""항목 메타데이터: F1 그랑프리 경기 (데이터 준비 영역, 도메인 전용).

- Jolpica-F1 API에서 시즌별 일정과 결선 결과를 받아 경기 1건 = 항목 1건으로 정리한다.
- 경기마다 위키백과 문서(한국어 우선, 없거나 짧으면 영어)를 찾아 '항목 자체 문서'로 지정한다.
- 항목 1건을 검색용 텍스트 하나로 만든다 (F2001, 2.2 항목 텍스트 설계).
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from f1rag.domain import names
from f1rag.sources.http import PoliteSession
from f1rag.sources.wiki import WikipediaSource, page_url

logger = logging.getLogger("f1rag.races")

JOLPICA = "https://api.jolpi.ca/ergast/f1"
JOLPICA_HOST_INTERVAL = 0.5  # 공개 한도: 초당 4회, 시간당 500회

# 커버리지 표에서 한국어 문서를 '충분/짧음'으로 나누는 기준(바이트). 한국어는 글자당 약 3바이트라 ≈ 1000자.
# 위키 원문 길이에는 표도 포함되므로 참고용이며, 영어 문서는 길이와 관계없이 항상 함께 색인한다.
KO_STUB_BYTES = 3000

FINISHED = {"Finished", "Lapped"}


# ---------------------------------------------------------------- 수집


def fetch_season(http: PoliteSession, season: int) -> list[dict]:
    schedule = http.get_json(f"{JOLPICA}/{season}/races/", {"limit": 100})
    races = {int(r["round"]): r for r in schedule["MRData"]["RaceTable"]["Races"]}

    results: dict[int, list[dict]] = {rnd: [] for rnd in races}
    offset, total = 0, None
    while total is None or offset < total:
        page = http.get_json(f"{JOLPICA}/{season}/results/", {"limit": 100, "offset": offset})
        total = int(page["MRData"]["total"])
        for race in page["MRData"]["RaceTable"]["Races"]:
            results.setdefault(int(race["round"]), []).extend(race.get("Results", []))
        offset += 100

    # 예선 결과: 예선 1위와 1번 그리드 출발은 다를 수 있다 (그리드 페널티, 2021 스프린트 예선 등)
    qualifying: dict[int, list[dict]] = {}
    offset, total = 0, None
    while total is None or offset < total:
        page = http.get_json(f"{JOLPICA}/{season}/qualifying/", {"limit": 100, "offset": offset})
        total = int(page["MRData"]["total"])
        for race in page["MRData"]["RaceTable"]["Races"]:
            qualifying.setdefault(int(race["round"]), []).extend(race.get("QualifyingResults", []))
        offset += 100

    last_round = max(races) if races else 0
    return [summarize(races[rnd], results.get(rnd, []), last_round, qualifying.get(rnd)) for rnd in sorted(races)]


def summarize(race: dict, results: list[dict], last_round: int, qualifying: list[dict] | None = None) -> dict[str, Any]:
    season, rnd = int(race["season"]), int(race["round"])
    loc = race["Circuit"]["Location"]

    def person(res: dict | None) -> dict | None:
        if not res:
            return None
        d, c = res["Driver"], res["Constructor"]
        return {
            "driver": names.driver_ko(d.get("givenName", ""), d.get("familyName", "")),
            "driver_en": f"{d.get('givenName', '')} {d.get('familyName', '')}".strip(),
            "team": names.constructor_ko(c["name"]),
            "team_en": c["name"],
            "grid": int(res.get("grid") or 0),
        }

    by_pos = {int(r["position"]): r for r in results if str(r.get("position", "")).isdigit()}
    grid_one = next((r for r in results if str(r.get("grid")) == "1"), None)
    fastest = next((r for r in results if r.get("FastestLap", {}).get("rank") == "1"), None)
    retirements = [r for r in results if not _finished(r.get("status", ""))]
    winner = person(by_pos.get(1))
    quali_first = next((q for q in (qualifying or []) if str(q.get("position")) == "1"), None)
    quali_pole = person(quali_first)
    if quali_pole:
        quali_pole.pop("grid", None)

    en_title = _wiki_title(race.get("url", ""))
    return {
        "race_id": f"{season}_{rnd:02d}",
        "season": season,
        "round": rnd,
        "is_season_opener": rnd == 1,
        "is_season_finale": rnd == last_round,
        "name_en": race["raceName"],
        "name_ko": names.gp_ko(race["raceName"]),
        "date": race.get("date", ""),
        "circuit": race["Circuit"]["circuitName"],
        "locality": loc.get("locality", ""),
        "country_en": loc.get("country", ""),
        "country_ko": names.country_ko(loc.get("country", "")),
        "has_sprint": "Sprint" in race,
        "laps": int(by_pos[1]["laps"]) if 1 in by_pos else None,
        "winner": winner,
        "second": person(by_pos.get(2)),
        "third": person(by_pos.get(3)),
        "grid_one": person(grid_one),
        "quali_pole": quali_pole,  # 예선 1위. 예선 결과가 없으면 None
        "fastest_lap": person(fastest),
        "starters": len(results),
        "retirements": len(retirements),
        "pole_to_win": bool(winner and winner["grid"] == 1),  # 1번 그리드에서 출발해 우승
        "source_url": f"{JOLPICA}/{season}/{rnd}/results/",
        "wiki_en_title": en_title,
        "wiki_ko_title": None,
        "wiki_ko_length": 0,
        "item_docs": [],
    }


def _finished(status: str) -> bool:
    return status in FINISHED or status.startswith("+")


def _wiki_title(url: str) -> str:
    if "/wiki/" not in url:
        return ""
    return unquote(url.split("/wiki/", 1)[1]).replace("_", " ")


def resolve_item_docs(races: list[dict], wiki: WikipediaSource, progress: Callable[[str], None] = print) -> None:
    """경기마다 '항목 자체 문서'를 정한다. 한국어 문서가 있으면 한국어, 없거나 짧으면 영어도 함께."""
    en_titles = sorted({r["wiki_en_title"] for r in races if r["wiki_en_title"]})
    progress(f"영어 위키 문서 {len(en_titles)}개의 한국어 연결 확인")
    links = wiki.langlinks(en_titles, "en", "ko")

    for r in races:
        exists, ko = links.get(r["wiki_en_title"], (False, None))
        r["wiki_en_exists"] = exists
        r["wiki_ko_title"] = ko

    # 언어 연결이 빠진 경기는 같은 그랑프리의 다른 해 한국어 제목에서 연도만 바꿔 추정한다
    patterns: dict[str, str] = {}
    for r in races:
        prefix = f"{r['season']}년 "
        if r["wiki_ko_title"] and r["wiki_ko_title"].startswith(prefix):
            patterns.setdefault(r["name_en"], r["wiki_ko_title"][len(prefix) :])
    guessed = set()
    for r in races:
        if not r["wiki_ko_title"] and r["name_en"] in patterns:
            r["wiki_ko_title"] = f"{r['season']}년 {patterns[r['name_en']]}"
            guessed.add(r["race_id"])

    ko_titles = sorted({r["wiki_ko_title"] for r in races if r["wiki_ko_title"]})
    progress(f"한국어 위키 문서 {len(ko_titles)}개의 길이 확인")
    lengths = wiki.lengths(ko_titles, "ko") if ko_titles else {}

    for r in races:
        length = lengths.get(r["wiki_ko_title"], 0) if r["wiki_ko_title"] else 0
        if length == 0:
            r["wiki_ko_title"] = None
        r["wiki_ko_length"] = length
        r["wiki_ko_guessed"] = r["race_id"] in guessed and length > 0
        docs = []
        if r["wiki_ko_title"]:
            docs.append({"lang": "ko", "title": r["wiki_ko_title"]})
        # 영어 경기 문서는 항상 함께 쓴다. 한국어 문서는 결과표만 있고 경기 중 사건(세이프티카·페널티) 서술이
        # 없는 경우가 많은데, 위키 원문 길이에는 표도 포함돼 '충분히 긴 문서'로 잘못 판단되기 때문이다.
        if r.get("wiki_en_exists"):
            docs.append({"lang": "en", "title": r["wiki_en_title"]})
        r["item_docs"] = docs
        r["coverage"] = coverage_status(r)


def coverage_status(r: dict) -> str:
    if r.get("wiki_ko_length", 0) >= KO_STUB_BYTES:
        return "한국어 충분"
    if r.get("wiki_ko_length", 0) > 0:
        return "한국어 짧음"
    if r.get("wiki_en_exists"):
        return "영어만"
    return "문서 없음"


# ---------------------------------------------------------------- 저장·로드


def save_races(races: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(races, ensure_ascii=False, indent=1), encoding="utf-8")


def load_races(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_coverage_csv(races: list[dict], path: Path) -> None:
    fields = ["race_id", "season", "round", "name_ko", "coverage", "wiki_ko_title", "wiki_ko_length", "wiki_en_title"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # 엑셀에서 한글이 깨지지 않게 BOM 포함
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: r.get(k) for k in fields} for r in races)


# ---------------------------------------------------------------- 항목 텍스트 (F2001)


def label(r: dict) -> str:
    return f"{r['season']} R{r['round']:02d} {r['name_ko']}"


def _p(x: dict | None) -> str:
    return f"{x['driver']}({x['team']})" if x else "정보 없음"


def build_item_text(r: dict) -> str:
    """경기 1건을 검색 텍스트 하나로 묶는다.

    사용자가 경기를 찾는 단서(연도, 그랑프리 이름, 서킷, 나라, 우승자·포디움·폴, 개막전/최종전,
    스프린트, 리타이어 수)를 모두 넣는다. 빠진 속성으로는 검색이 걸리지 않기 때문이다.
    한국어 질문과 영어 질문 모두에 걸리도록 한국어·영어 이름을 함께 쓴다.
    """
    flags = []
    if r.get("is_season_opener"):
        flags.append("시즌 개막전")
    if r.get("is_season_finale"):
        flags.append("시즌 최종전")
    if r.get("has_sprint"):
        flags.append("스프린트 개최 주말")
    winner, grid_one, quali = r.get("winner"), r.get("grid_one"), r.get("quali_pole")
    lines = [
        f"{r['season']} 시즌 {r['round']}라운드 {r['name_ko']} ({r['season']} {r['name_en']}).",
        f"날짜: {r['date']}. 서킷: {r['circuit']}, {r['locality']}, {r['country_ko']} ({r['country_en']}).",
    ]
    if flags:
        lines.append("특징: " + ", ".join(flags) + ".")
    lines.append(f"결과: 우승 {_p(winner)}, 2위 {_p(r.get('second'))}, 3위 {_p(r.get('third'))}.")
    if winner:
        lines.append(f"우승자 영문명: {winner['driver_en']} ({winner['team_en']}).")
    if quali:
        lines.append(f"예선 1위: {_p(quali)}.")
    lines.append(f"1번 그리드 출발: {_p(grid_one)}.")
    if quali and grid_one and quali["driver_en"] != grid_one["driver_en"]:
        lines.append(
            f"예선 1위와 1번 그리드 출발 드라이버가 다름: 예선 1위 {quali['driver']}가 아닌 {grid_one['driver']}가 "
            "1번 그리드에서 출발(그리드 페널티나 스프린트 결과 등)."
        )
    if winner:
        lines.append(
            "1번 그리드에서 출발해 우승: " + ("예" if r.get("pole_to_win") else f"아니오, 우승자는 {winner['grid']}번 그리드 출발")
            + "."
        )
        if quali:
            won = quali["driver_en"] == winner["driver_en"]
            lines.append("예선 1위 드라이버의 우승(폴 투 윈): " + ("예" if won else "아니오") + ".")
    if r.get("fastest_lap"):
        lines.append(f"패스티스트 랩: {_p(r['fastest_lap'])}.")
    lines.append(f"출전 {r.get('starters', 0)}대, 완주하지 못한 차 {r.get('retirements', 0)}대, 우승자 기준 {r.get('laps') or '-'}랩.")
    return "\n".join(lines)


def item_metadata(r: dict) -> dict[str, Any]:
    """메타데이터 청크에 함께 저장할 필드 (2.3). Chroma 메타데이터는 None을 허용하지 않아 빈 값은 뺀다."""
    winner, grid_one = r.get("winner") or {}, r.get("grid_one") or {}
    quali = r.get("quali_pole") or {}
    meta = {
        "race_id": r["race_id"],
        "season": r["season"],
        "round": r["round"],
        "title": f"{r['season']} {r['name_ko']}",
        "name_en": r["name_en"],
        "date": r["date"],
        "circuit": r["circuit"],
        "country": r["country_ko"],
        "winner": winner.get("driver", ""),
        "pole": quali.get("driver", ""),          # 예선 1위 (예선 결과가 없으면 빈 값)
        "grid_one": grid_one.get("driver", ""),  # 1번 그리드 출발
        "source_type": "race_meta",
        "url": r["source_url"],
        "wiki_url": page_url("ko", r["wiki_ko_title"]) if r.get("wiki_ko_title") else (
            page_url("en", r["wiki_en_title"]) if r.get("wiki_en_title") else ""
        ),
    }
    return {k: v for k, v in meta.items() if v is not None}
