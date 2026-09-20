"""화면 파일을 pfkfks 호스팅 폴더로 복사한다.

왜 복사인가
-----------
pfkfks.org는 Firebase Hosting으로 서비스되고, **Hosting 배포는 사이트 전체를
교체한다.** 그래서 별도 저장소에서 배포하면 /work, /study 같은 나머지가
전부 날아간다. 화면 파일은 반드시 ``pfkfks-main/public/fm24/`` 안에 있어야
하고, 배포는 pfkfks-main의 기존 GitHub Actions가 한다.

원본은 언제나 이 저장소의 ``web/static/`` 이다. 이 스크립트는 그걸
그대로 복사하고, 저쪽에만 남아 있는 파일은 지운다.

사용::

    python scripts/deploy_web.py                 # 기본 경로로 복사
    python scripts/deploy_web.py --dry-run
    python scripts/deploy_web.py --target ../pfkfks-main/public/fm24
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import utils

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "web" / "static"
DEFAULT_TARGET = PROJECT_ROOT.parent / "pfkfks-main" / "public" / "fm24"

#: 복사할 확장자. 다른 게 섞여 들어가면 조용히 배포되므로 화이트리스트로 둔다.
ALLOWED_SUFFIXES = {".html", ".js", ".css", ".svg", ".ico", ".png", ".webp"}


def plan(source: Path, target: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """복사/갱신/삭제 목록을 만든다.

    Returns:
        ``(새로 추가, 내용이 바뀜, 지울 것)`` — 전부 source 기준 상대 경로.
    """
    wanted = {
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and path.suffix in ALLOWED_SUFFIXES
    }
    existing = {
        path.relative_to(target)
        for path in target.rglob("*")
        if path.is_file()
    } if target.is_dir() else set()

    added = sorted(wanted - existing)
    removed = sorted(existing - wanted)
    changed = sorted(
        name for name in (wanted & existing)
        if not filecmp.cmp(source / name, target / name, shallow=False)
    )
    return added, changed, removed


def sync(source: Path, target: Path, dry_run: bool = False) -> tuple[int, int, int]:
    """source의 화면 파일을 target에 반영한다."""
    added, changed, removed = plan(source, target)

    for name in added + changed:
        destination = target / name
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
        print(f"  {'추가' if name in added else '갱신'}  {name}")

    for name in removed:
        if not dry_run:
            (target / name).unlink()
        print(f"  삭제  {name}")

    if not (added or changed or removed):
        print("  변경 없음 — 이미 최신입니다.")
    return len(added), len(changed), len(removed)


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    utils.enable_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="web/static 을 pfkfks-main/public/fm24 로 복사합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="복사할 위치")
    parser.add_argument("--dry-run", action="store_true", help="복사하지 않고 목록만 출력")
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()
    if not SOURCE.is_dir():
        print(f"[오류] 원본이 없습니다: {SOURCE}", file=sys.stderr)
        return 1
    if not target.parent.is_dir():
        print(f"[오류] 대상 폴더의 상위가 없습니다: {target.parent}", file=sys.stderr)
        print("       pfkfks-main 저장소가 옆에 있는지 확인하세요.", file=sys.stderr)
        return 1

    print(f"원본: {SOURCE}")
    print(f"대상: {target}" + ("  (dry-run)" if args.dry_run else ""))
    print()
    added, changed, removed = sync(SOURCE, target, args.dry_run)
    print()
    print(f"추가 {added} · 갱신 {changed} · 삭제 {removed}")
    if not args.dry_run and (added or changed or removed):
        print()
        print("이제 pfkfks-main 에서 커밋하고 push 하면 배포됩니다:")
        print("  git add public/fm24 && git commit -m \"...\" && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
