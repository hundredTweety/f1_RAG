"""scripts/*.py 를 `python scripts/xxx.py` 로 바로 실행할 수 있게 프로젝트 루트를 import 경로에 넣는다.

Windows(한국어) 콘솔이나 출력 리디렉션은 cp949라서 '›', '–' 같은 문자를 출력하다 멈출 수 있다.
출력을 UTF-8로 바꾸고, 표현할 수 없는 문자는 대체한다.
"""

import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
