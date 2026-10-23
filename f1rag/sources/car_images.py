"""우승 차 사진 찾기 (화면용, RAG 근거와는 무관).

위키백과에는 시즌마다 팀의 차 문서가 있고(예: "Red Bull Racing RB16B"), 문서 대표 사진은
대부분 위키미디어 공용의 자유 라이선스 사진이다. 순서:

1. 그 시즌 챔피언십 문서("2021 Formula One World Championship")가 링크한 문서 중
   짧은 설명(shortdesc)이 'Formula One ... car'인 문서를 모으고, 제목이 팀 이름으로 시작하는 차를 고른다.
   같은 팀 차가 여럿이면(예: RB16과 RB16B) 첫 문장에 그 시즌 연도가 나오는 차를 고른다.
2. 1에서 못 찾으면 "{팀} {시즌} Formula One car" 검색 결과에서 같은 기준으로 고른다.
3. 그 문서의 대표 사진을 자유 라이선스 사진만(pilicense=free) 받는다.
4. 사진 파일의 작성자·라이선스를 받아 화면에 출처로 표시한다. 작성자나 라이선스를 알 수 없으면 쓰지 않는다.

처음에는 검색만 썼는데 실제 실행에서 2021 레드불에 전년도 차(RB16), 2024 메르세데스에 다음 해 차(W16)가
잡히고, 2022 레드불(RB18) 등 7개 조합은 검색 상위에 차 문서가 없어 사진을 못 찾았다. 그래서 1을 먼저 쓴다.

개량형 차(RB16B, MCL35M)는 위키백과에서 기본형 문서(RB16, MCL35)로 넘겨주기(redirect)만 되어 있다.
그래서 챔피언십 문서가 링크한 이름이 넘겨주기면 그 이름(RB16B)을 차 이름으로 쓰고, 문서 안 사진 중
파일 이름에 그 형식(RB16B)이 들어간 자유 라이선스 사진을 먼저 찾는다. 없으면 문서 대표 사진을 쓰고 그렇게 표시한다.

결과(못 찾은 경우 포함)는 data/cache/car_images.json 에 저장해 같은 팀·시즌은 다시 묻지 않는다.
네트워크 오류는 캐시하지 않는다.
"""

from __future__ import annotations

import html
import logging
import re
import threading
from pathlib import Path
from typing import Any

from f1rag.sources.wiki import API, page_url
from f1rag.store.jsonfile import read_json, write_json

log = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")
NOT_FOUND = {"found": False}


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", value or ""))).strip()


CACHE_VERSION = "v4"  # 찾는 방법이 바뀌면 올린다 (이전 결과를 다시 쓰지 않도록)
_CHAMPIONSHIP_YEAR_RE = re.compile(r"(20\d\d) (?:FIA )?Formula One World Championship")
_TEAM_SUFFIX_RE = re.compile(r"\s+(F1 Team|Racing|Team)$", re.IGNORECASE)


def is_f1_car(shortdesc: str) -> bool:
    d = (shortdesc or "").lower()
    return "formula one" in d and "car" in d


def is_car_article(shortdesc: str, extract: str, season: int) -> bool:
    """검색 결과용: F1 차 문서이고, 첫 문단에서 처음 나오는 챔피언십 연도가 그 시즌인지.
    (전년도 차 문서도 '2021년에 RB16B로 개량' 처럼 다음 해를 언급하기 때문에 단순 포함으로는 부족하다)"""
    if not is_f1_car(shortdesc):
        return False
    years = _CHAMPIONSHIP_YEAR_RE.findall(extract or "")
    return years[0] == str(season) if years else str(season) in (extract or "")


def team_key(team_en: str) -> str:
    return _TEAM_SUFFIX_RE.sub("", (team_en or "").strip()).lower()


def title_matches_team(title: str, team_en: str) -> bool:
    key = team_key(team_en)
    return bool(key) and re.match(rf"{re.escape(key)}([\s-]|$)", title.lower()) is not None


class CarImageFinder:
    def __init__(self, http, cache_path: Path, lang: str = "en", width: int = 960):
        self.http = http
        self.cache_path = Path(cache_path)
        self.lang = lang
        self.width = width
        self._lock = threading.Lock()
        self._cache: dict[str, dict] | None = None

    # ---------------------------------------------------------------- 캐시
    def _load(self) -> dict[str, dict]:
        if self._cache is None:
            self._cache = read_json(self.cache_path)
        return self._cache

    def _save(self) -> None:
        for k, v in read_json(self.cache_path).items():  # 다른 서버가 쓴 항목을 지우지 않게 합친다
            self._cache.setdefault(k, v)
        write_json(self.cache_path, self._cache)

    # ---------------------------------------------------------------- 조회
    def find(self, season: int, team_en: str) -> dict[str, Any]:
        if not team_en:
            return dict(NOT_FOUND)
        key = f"{CACHE_VERSION}|{season}|{team_en}"
        with self._lock:
            cached = self._load().get(key)
        if cached is not None:
            return cached
        result = self._lookup(season, team_en)  # 네트워크 오류는 여기서 예외로 올라가 캐시되지 않는다
        with self._lock:
            self._load()[key] = result
            self._save()
        return result

    def _query(self, params: dict) -> dict:
        base = {"action": "query", "format": "json", "formatversion": 2}
        return self.http.get_json(API.format(lang=self.lang), {**base, **params})

    def _lookup(self, season: int, team_en: str) -> dict[str, Any]:
        found = self._from_season_page(season, team_en)
        title, variant = found if found else (self._from_search(season, team_en), None)
        if not title:
            return dict(NOT_FOUND)
        credit = self._variant_photo(title, variant) if variant else None
        photo_of = variant if credit else title
        if not credit:
            info = self._query({"titles": title, "prop": "pageimages", "piprop": "name", "pilicense": "free", "redirects": 1})
            pages = info.get("query", {}).get("pages", [])
            image = pages[0].get("pageimage") if pages else None
            credit = self._image_credit(image) if image else None
        if not credit:
            return dict(NOT_FOUND)
        car = variant or title
        return {
            "found": True, "car": car, "page_url": page_url(self.lang, title), **credit,
            # 개량형인데 그 형식 사진이 없어 기본형 사진을 쓴 경우 화면에 알린다
            "photo_of": "" if photo_of == car else photo_of,
        }

    def _variant_photo(self, title: str, variant: str) -> dict[str, Any] | None:
        """기본형 문서 안의 사진 중 파일 이름에 개량형 이름(예: RB16B)이 들어간 자유 라이선스 사진."""
        code = re.sub(r"[\s_\-]", "", variant.split()[-1]).lower()
        data = self._query({"titles": title, "prop": "images", "imlimit": "max", "redirects": 1})
        pages = data.get("query", {}).get("pages", [])
        files = [i.get("title", "") for i in (pages[0].get("images", []) if pages else [])]
        hits = [f for f in files if code in re.sub(r"[\s_\-]", "", f).lower() and not f.lower().endswith((".svg", ".gif"))]
        for f in hits[:5]:
            credit = self._image_credit(f.split(":", 1)[-1])
            if credit:
                return credit
        return None

    def _from_season_page(self, season: int, team_en: str) -> tuple[str, str | None] | None:
        """챔피언십 문서가 링크한 F1 차 문서 중 그 팀 차. (문서 제목, 넘겨주기로 들어온 개량형 이름)"""
        params = {
            "titles": f"{season} Formula One World Championship", "redirects": 1,
            "generator": "links", "gplnamespace": 0, "gpllimit": "max",
            "prop": "pageprops", "ppprop": "wikibase-shortdesc",
        }
        cars: list[str] = []
        redirected: dict[str, list[str]] = {}
        cont: dict = {}
        for _ in range(8):  # 링크가 많으면 여러 번에 나눠 온다
            data = self._query({**params, **cont})
            q = data.get("query", {})
            for r in q.get("redirects", []):
                redirected.setdefault(r.get("to", ""), []).append(r.get("from", ""))
            for p in q.get("pages", []):
                if is_f1_car(p.get("pageprops", {}).get("wikibase-shortdesc", "")) and title_matches_team(p.get("title", ""), team_en):
                    cars.append(p["title"])
            if "continue" not in data:
                break
            cont = data["continue"]
        cars = list(dict.fromkeys(cars))
        def variant(t: str) -> str | None:
            # 넘겨주기 이름 중 다른 형식(RB16 → RB16B)만 개량형으로 본다.
            # 'Mercedes-AMG F1 W12 E Performance' → 'Mercedes W12' 처럼 같은 차의 긴 이름은 개량형이 아니다.
            base = t.split()[-1].lower()
            for f in redirected.get(t, []):
                if title_matches_team(f, team_en) and base not in re.split(r"[\s\-]+", f.lower()):
                    return f
            return None
        if len(cars) <= 1:
            return (cars[0], variant(cars[0])) if cars else None
        # 같은 팀 차가 여럿이면 첫 문장에 그 시즌 챔피언십이 먼저 나오는 차, 없으면 개량형으로 들어온 차
        ext = self._query({"titles": "|".join(cars), "prop": "extracts|pageprops", "ppprop": "wikibase-shortdesc",
                           "exintro": 1, "explaintext": 1, "exsentences": 2, "exlimit": "max"})
        by_title = {p.get("title"): p for p in ext.get("query", {}).get("pages", [])}
        for t in sorted(cars):
            p = by_title.get(t, {})
            if is_car_article(p.get("pageprops", {}).get("wikibase-shortdesc", ""), p.get("extract", ""), season):
                return t, None
        for t in sorted(cars):
            if variant(t):
                return t, variant(t)
        return None

    def _from_search(self, season: int, team_en: str) -> str | None:
        search = self._query({"list": "search", "srsearch": f"{team_en} {season} Formula One car", "srlimit": 10, "srprop": ""})
        titles = [s["title"] for s in search.get("query", {}).get("search", [])]
        if not titles:
            return None
        info = self._query({"titles": "|".join(titles), "prop": "pageprops|extracts", "ppprop": "wikibase-shortdesc",
                            "exintro": 1, "explaintext": 1, "exsentences": 2, "exlimit": "max"})
        pages = {p.get("title"): p for p in info.get("query", {}).get("pages", [])}
        for title in titles:  # 검색 순위대로
            p = pages.get(title) or {}
            if is_car_article(p.get("pageprops", {}).get("wikibase-shortdesc", ""), p.get("extract", ""), season):
                return title
        return None

    def _image_credit(self, file_name: str) -> dict[str, Any] | None:
        data = self._query({
            "titles": f"File:{file_name}",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": self.width,
        })
        pages = data.get("query", {}).get("pages", [])
        ii = (pages[0].get("imageinfo") or [None])[0] if pages else None
        if not ii:
            return None
        meta = ii.get("extmetadata", {})
        license_name = _plain(meta.get("LicenseShortName", {}).get("value", ""))
        artist = _plain(meta.get("Artist", {}).get("value", ""))
        non_free = str(meta.get("NonFree", {}).get("value", "")).lower() == "true"
        if not license_name or not artist or non_free or "fair use" in license_name.lower():
            return None
        return {
            "image": ii.get("thumburl") or ii.get("url"),
            "width": ii.get("thumbwidth") or ii.get("width"),
            "height": ii.get("thumbheight") or ii.get("height"),
            "file_page": ii.get("descriptionurl", ""),
            "artist": artist[:120],
            "license": license_name,
            "license_url": meta.get("LicenseUrl", {}).get("value", ""),
        }
