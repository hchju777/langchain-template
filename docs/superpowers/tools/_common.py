"""두 스크립트가 함께 쓰는 경로와 Chrome 호출.

경로는 이 파일의 위치에서 유도한다. 저장소를 어디에 두든, 이름을 바꾸든
그대로 동작한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: docs/superpowers/tools/_common.py → 저장소 루트
REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGES = REPO_ROOT / "docs" / "images"
SPECS = REPO_ROOT / "docs" / "superpowers" / "specs"

#: 배포판에 따라 이름이 다르다. CHROME 환경변수로 직접 지정할 수도 있다.
_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
)


def find_chrome() -> str | None:
    """Chrome 실행 파일 경로. 못 찾으면 None.

    CHROME을 지정했는데 그게 틀린 것은 사용자 실수라 조용히 넘기지 않고
    즉시 알린다. 아예 설치가 안 된 경우와는 성격이 다르다.
    """
    explicit = os.environ.get("CHROME")
    if explicit:
        if shutil.which(explicit) or Path(explicit).is_file():
            return explicit
        sys.exit(f"CHROME='{explicit}' 을(를) 찾을 수 없습니다.")

    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def warn_no_chrome(skipped: str) -> None:
    """Chrome이 없어 건너뛴 산출물을 알린다. 나머지는 이미 만들어져 있다."""
    print(
        f"\n[건너뜀] Chrome을 찾을 수 없어 {skipped}을(를) 만들지 못했습니다.\n"
        f"  찾아본 이름: {', '.join(_CANDIDATES)}\n"
        "  설치돼 있는데도 안 잡히면 경로를 지정하세요:\n"
        "      CHROME=/path/to/chrome python3 <스크립트>",
        file=sys.stderr,
    )


def run_chrome(*args: str) -> bool:
    """headless Chrome 호출. Chrome이 없으면 False."""
    exe = find_chrome()
    if exe is None:
        return False
    cmd = [exe, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"Chrome 실행 실패 (코드 {proc.returncode})\n{proc.stderr.strip()}")
    return True


def rel(path: Path) -> str:
    """로그용 짧은 경로."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
