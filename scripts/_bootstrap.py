"""CLI 스크립트가 ``src`` 패키지를 찾을 수 있게 프로젝트 루트를 sys.path에 넣는다.

설치(pip install -e .) 없이 ``python scripts/update.py`` 로 바로 돌리기 위한 것.
각 스크립트 맨 위에서 ``import _bootstrap`` 한 줄만 쓰면 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
