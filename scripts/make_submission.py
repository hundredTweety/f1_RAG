"""제출용 zip 만들기.

.gitignore 는 git 에만 적용되고, 탐색기에서 폴더를 압축하면 .env(API 키)·.venv·data/chroma 까지 들어간다.
이 스크립트는 .gitignore 와 같은 기준으로 빼고 압축한다.

사용법 (f1-rag 폴더에서)
  python scripts/make_submission.py --name AI코어프로젝트2_F1레이스노트_서울_1반_홍길동
  → 상위 폴더에 AI코어프로젝트2_F1레이스노트_서울_1반_홍길동.zip 생성
"""

import argparse
import fnmatch
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 폴더 이름(어느 위치든) 또는 프로젝트 기준 경로 패턴
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "logs", "node_modules"}
SKIP_PATHS = [
    "data/chroma*", "data/cache*",          # 색인·수집 캐시: 다시 만들 수 있고 용량이 큼
    "data/raw/regulations/*.pdf", "data/raw/decisions/*.pdf", "data/raw/decisions/*/*.pdf",  # FIA PDF: 재배포하지 않음 (05 문서)
    "config/settings_empty.yaml",
]
SKIP_FILES = [".env", ".env.*", "*.pyc"]
KEEP_FILES = {".env.example"}


def skipped(rel: Path) -> bool:
    if any(part in SKIP_DIRS for part in rel.parts):
        return True
    # 파일 자신이나 그 상위 폴더 중 하나라도 패턴에 맞으면 뺀다 (예: data/chroma/... 는 data/chroma* 에 맞음)
    paths = [rel.as_posix()] + [q.as_posix() for q in rel.parents if q.as_posix() != "."]
    if any(fnmatch.fnmatch(path, pat) for path in paths for pat in SKIP_PATHS):
        return True
    if rel.name in KEEP_FILES:
        return False
    return any(fnmatch.fnmatch(rel.name, pat) for pat in SKIP_FILES)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="zip 파일 이름 (확장자 없이)")
    args = parser.parse_args()
    out = ROOT.parent / f"{args.name}.zip"
    files = [p for p in sorted(ROOT.rglob("*")) if p.is_file() and not skipped(p.relative_to(ROOT))]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path(ROOT.name) / p.relative_to(ROOT))
    names = [p.relative_to(ROOT).as_posix() for p in files]
    leaked = [n for n in names if Path(n).name == ".env" or n.startswith(("data/chroma", ".venv")) or n.endswith(".pdf") and n.startswith("data/raw")]
    print(f"저장: {out}")
    print(f"파일 {len(files)}개, {out.stat().st_size / 1_000_000:.1f} MB")
    print("확인: .env·.venv·data/chroma·FIA PDF " + ("없음" if not leaked else f"들어감! {leaked[:5]}"))


if __name__ == "__main__":
    main()
