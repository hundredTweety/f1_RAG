"""우승 차 사진 미리 찾기·확인 (화면용).

races.json 의 우승 팀·시즌 조합마다 위키백과에서 그 시즌 차 문서와 자유 라이선스 사진을 찾아
data/cache/car_images.json 에 저장하고, 결과를 표로 보여 준다. 사진을 못 찾은 조합은 화면에서 사진 칸이 숨겨진다.

사용법
  python scripts/check_car_images.py
"""

import _bootstrap  # noqa: F401

from f1rag import config
from f1rag.domain.races import load_races
from f1rag.runtime import build_runtime


def main() -> None:
    rt = build_runtime()
    finder = rt.car_images
    if finder is None:
        print("사진 찾기가 꺼져 있습니다: .env 의 WIKI_CONTACT 를 채우고, 가짜 모델·오프라인 설정이 아닌지 확인하세요.")
        return
    pairs = sorted({(r["season"], r["winner"]["team_en"], r["winner"]["team"]) for r in load_races(rt.settings.path("races_json")) if r.get("winner")})
    print(f"{'시즌':<6}{'팀':<14}{'차 문서':<36}라이선스 / 작성자")
    for season, team_en, team_ko in pairs:
        try:
            res = finder.find(season, team_en)
        except Exception as exc:  # 네트워크 오류는 캐시하지 않으므로 다시 실행하면 된다
            print(f"{season:<6}{team_ko:<14}(오류: {type(exc).__name__}, 다시 실행하세요)")
            continue
        if res.get("found"):
            note = f"  (사진은 {res['photo_of']})" if res.get("photo_of") else ""
            print(f"{season:<6}{team_ko:<14}{res['car']:<36}{res['license']} / {res['artist']}{note}")
        else:
            print(f"{season:<6}{team_ko:<14}(사진 없음)")
    print(f"\n저장: {config.ROOT / rt.settings.get('paths.cache_dir')}/car_images.json")


if __name__ == "__main__":
    main()
