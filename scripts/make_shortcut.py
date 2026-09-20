"""바탕화면에 FM24 트래커 바로가기(.lnk)를 만든다.

왜 파이썬으로 만드는가
----------------------
바로가기 이름과 대상 파일명에 한글이 들어간다. ``.bat`` 은 cmd가 OEM
코드페이지(한국어 Windows에서는 949)로 읽기 때문에 UTF-8 한글을 넣으면
깨지고, ``.ps1`` 은 PowerShell 5.1이 BOM 없는 UTF-8을 ANSI로 오해한다.

파이썬은 소스를 UTF-8로 읽으므로 한글이 안전하고, PowerShell에는
``-EncodedCommand`` (UTF-16LE + base64)로 넘기므로 코드페이지를 아예
거치지 않는다.

사용::

    python scripts/make_shortcut.py
    python scripts/make_shortcut.py --name "FM" --remove
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import utils

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_NAME = "FM24 트래커.bat"
DEFAULT_SHORTCUT_NAME = "FM24 트래커"

#: 바로가기 아이콘. imageres.dll 174번은 꺾은선 그래프 모양이다.
ICON = r"%SystemRoot%\system32\imageres.dll,174"


def run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    """PowerShell 스크립트를 코드페이지 문제 없이 실행한다.

    ``-EncodedCommand`` 는 UTF-16LE base64를 받으므로, 명령줄 인코딩이나
    따옴표 이스케이프를 신경 쓸 필요가 없다.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def desktop_dir() -> Path:
    """바탕화면 경로. OneDrive로 옮겨간 경우도 PowerShell이 알아서 찾아준다."""
    result = run_powershell("[Environment]::GetFolderPath('Desktop')")
    path = result.stdout.strip()
    if not path:
        raise RuntimeError("바탕화면 경로를 찾지 못했습니다.")
    return Path(path)


def _ps_quote(text: str) -> str:
    """PowerShell 작은따옴표 문자열로 감싼다."""
    return "'" + str(text).replace("'", "''") + "'"


def create_shortcut(name: str = DEFAULT_SHORTCUT_NAME) -> Path:
    """바탕화면에 바로가기를 만들고 그 경로를 돌려준다.

    Raises:
        FileNotFoundError: 런처 .bat이 없을 때.
        RuntimeError: PowerShell이 실패했을 때.
    """
    launcher = PROJECT_ROOT / LAUNCHER_NAME
    if not launcher.is_file():
        raise FileNotFoundError(f"런처를 찾지 못했습니다: {launcher}")

    link = desktop_dir() / f"{name}.lnk"
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut({_ps_quote(link)})
$s.TargetPath = {_ps_quote(launcher)}
$s.WorkingDirectory = {_ps_quote(PROJECT_ROOT)}
$s.Description = 'FM24 선수 성장/주전 추적기'
$s.IconLocation = {_ps_quote(ICON)}
$s.Save()
"""
    result = run_powershell(script)
    if result.returncode != 0 or not link.exists():
        raise RuntimeError(result.stderr.strip() or "바로가기를 만들지 못했습니다.")
    return link


def remove_shortcut(name: str = DEFAULT_SHORTCUT_NAME) -> bool:
    """바로가기를 지운다. 없었으면 False."""
    link = desktop_dir() / f"{name}.lnk"
    if not link.exists():
        return False
    link.unlink()
    return True


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    utils.enable_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="바탕화면에 FM24 트래커 바로가기를 만듭니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--name", default=DEFAULT_SHORTCUT_NAME, help="바로가기 이름")
    parser.add_argument("--remove", action="store_true", help="만들지 않고 지웁니다")
    args = parser.parse_args(argv)

    try:
        if args.remove:
            if remove_shortcut(args.name):
                print(f"바로가기를 지웠습니다: {args.name}")
            else:
                print(f"바탕화면에 '{args.name}' 바로가기가 없습니다.")
            return 0

        link = create_shortcut(args.name)
        print("바탕화면에 바로가기를 만들었습니다.")
        print(f"  {link}")
        print()
        print("이제 바탕화면의 아이콘을 두 번 누르면 트래커가 열립니다.")
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
