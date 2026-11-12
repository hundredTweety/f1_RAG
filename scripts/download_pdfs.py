"""data/raw/manifest.yaml 의 FIA PDF를 내려받는다.

이미 받은 파일은 건너뛴다. 재시도·호출 간격·User-Agent 규칙은 다른 외부 호출과 같다.

사용법
  python scripts/download_pdfs.py
"""

import _bootstrap  # noqa: F401

from f1rag import config
from f1rag.domain.regulations import load_manifest
from f1rag.sources.http import PoliteSession


def main() -> None:
    settings = config.load_settings()
    manifest = load_manifest(settings.path("pdf_manifest"))
    http = PoliteSession.from_settings(settings, config.wiki_contact(), min_interval_sec=2.0)
    targets = [(settings.path("regulations_dir") / e["file"], e["url"]) for e in manifest["regulations"]]
    targets += [(settings.path("decisions_dir") / e["file"], e["url"]) for e in manifest["decisions"]]
    for path, url in targets:
        if path.exists() and path.stat().st_size > 0:
            print(f"있음: {path.name}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = http.get(url)
        if "pdf" not in resp.headers.get("Content-Type", "").lower() and not resp.content.startswith(b"%PDF"):
            print(f"PDF가 아님, 건너뜀: {url}")
            continue
        path.write_bytes(resp.content)
        print(f"저장: {path} ({len(resp.content) // 1024} KB)")


if __name__ == "__main__":
    main()
