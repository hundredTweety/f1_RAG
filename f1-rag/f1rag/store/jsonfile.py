"""JSON 캐시 파일 저장 (수집 캐시, 차 사진 캐시).

윈도우에서는 다른 프로그램이 대상 파일을 잠깐 열고 있으면(같이 켜 둔 Streamlit·FastAPI 서버,
백신 검사, 탐색기 미리보기 등) 임시 파일을 대상 파일로 바꾸는 os.replace 가
'PermissionError: [WinError 5] 액세스가 거부되었습니다'로 실패한다. 실제 실행에서 이 오류로 답변 전체가 실패했다.

- 임시 파일 이름에 프로세스 번호를 붙여 두 서버가 같은 임시 파일을 쓰지 않게 한다.
- 바꾸기가 거부되면 잠깐 기다렸다 다시 시도한다.
- 끝내 실패해도 예외를 올리지 않고 로그만 남긴다. 캐시는 다음 저장 때 다시 쓰이므로 답변은 계속된다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: Any, retries: int = 8, wait: float = 0.05) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(wait * (attempt + 1))
        log.warning("캐시 파일을 저장하지 못했습니다(다른 프로그램이 사용 중): %s", path.name)
        return False
    except OSError as exc:
        log.warning("캐시 파일 저장 실패: %s (%s)", path.name, type(exc).__name__)
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
