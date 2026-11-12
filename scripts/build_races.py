"""항목 메타데이터 준비: Jolpica-F1 → data/processed/races.json (데이터 준비).

경기마다 위키백과 '항목 자체 문서'(한국어 우선, 없거나 짧으면 영어)도 함께 정하고,
커버리지 표를 data/processed/wiki_coverage.csv 로 남긴다 (2.1 도메인 확정 근거).

사용법
  python scripts/build_races.py               # 설정의 시즌 전체
  python scripts/build_races.py --seasons 2021 2022
  python scripts/build_races.py --skip-wiki   # 위키 문서 확인 생략
"""

import argparse
from collections import Counter, defaultdict

import _bootstrap  # noqa: F401

from f1rag import config
from f1rag.domain import races as R
from f1rag.errors import MissingContactError
from f1rag.sources.http import PoliteSession
from f1rag.sources.wiki import WikipediaSource


def main() -> None:
    settings = config.load_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", type=int, nargs="*", default=settings.get("domain.seasons"))
    parser.add_argument("--skip-wiki", action="store_true")
    args = parser.parse_args()

    contact = config.wiki_contact()
    if not contact and not args.skip_wiki:
        raise SystemExit(MissingContactError().message)

    http = PoliteSession.from_settings(
        settings, contact, per_host_interval={"api.jolpi.ca": R.JOLPICA_HOST_INTERVAL}
    )

    races = []
    for season in args.seasons:
        season_races = R.fetch_season(http, season)
        races.extend(season_races)
        print(f"{season}: {len(season_races)}경기")
    print(f"합계 {len(races)}경기")

    if not args.skip_wiki:
        R.resolve_item_docs(races, WikipediaSource(http))
        R.save_coverage_csv(races, settings.path("coverage_csv"))
        by_season = defaultdict(Counter)
        for r in races:
            by_season[r["season"]][r["coverage"]] += 1
        print("\n위키 문서 커버리지")
        for season, counts in sorted(by_season.items()):
            print(f"  {season}: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
        no_doc = [R.label(r) for r in races if not r["item_docs"]]
        print(f"  항목 자체 문서가 없는 경기 {len(no_doc)}건" + (f": {', '.join(no_doc)}" if no_doc else ""))
        print(f"커버리지 표 저장: {settings.path('coverage_csv')}")

    R.save_races(races, settings.path("races_json"))
    print(f"저장: {settings.path('races_json')}")


if __name__ == "__main__":
    main()
