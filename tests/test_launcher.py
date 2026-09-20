"""더블클릭 실행 경로 테스트.

여기서 검증하는 것은 "눌렀을 때 뜨는가"의 앞단계들이다:

* ``.bat`` 파일이 ASCII로만 돼 있는가 — cmd는 .bat을 OEM 코드페이지(한국어
  Windows에서 949)로 읽으므로, UTF-8 한글을 넣으면 깨져서 실행이 실패한다.
  이 테스트가 없으면 나중에 무심코 한글 주석을 넣고 깨질 수 있다.
* 런처가 참조하는 파일이 실제로 있는가
* 바로가기 생성기가 PowerShell에 넘길 문자열을 제대로 escape하는가
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import make_shortcut  # noqa: E402

LAUNCHER = PROJECT_ROOT / "FM24 트래커.bat"
SHORTCUT_BAT = PROJECT_ROOT / "바탕화면에 바로가기 만들기.bat"
FINDER = PROJECT_ROOT / "scripts" / "_findpython.bat"
ALL_BATS = (LAUNCHER, SHORTCUT_BAT, FINDER)


class TestBatchFiles(unittest.TestCase):
    def test_all_launchers_exist(self) -> None:
        for path in ALL_BATS:
            with self.subTest(name=path.name):
                self.assertTrue(path.is_file(), f"없는 파일: {path}")

    def test_batch_contents_are_ascii_only(self) -> None:
        # 한글을 넣으면 cmd가 949로 읽어서 깨진다. 파일 '이름'의 한글은
        # 괜찮지만 '내용'은 반드시 ASCII여야 한다.
        for path in ALL_BATS:
            with self.subTest(name=path.name):
                raw = path.read_bytes()
                non_ascii = [b for b in raw if b > 0x7F]
                self.assertEqual(
                    non_ascii, [], f"{path.name} 에 ASCII 밖 바이트가 있습니다"
                )

    def test_launcher_runs_the_server_script(self) -> None:
        text = LAUNCHER.read_text(encoding="ascii")
        self.assertIn(r"scripts\serve.py", text)
        self.assertIn("_findpython.bat", text)
        self.assertTrue((PROJECT_ROOT / "scripts" / "serve.py").is_file())

    def test_shortcut_bat_runs_the_python_helper(self) -> None:
        text = SHORTCUT_BAT.read_text(encoding="ascii")
        self.assertIn(r"scripts\make_shortcut.py", text)

    def test_launcher_changes_to_its_own_directory(self) -> None:
        # 바로가기로 실행하면 작업 디렉터리가 다를 수 있다. db/fm24.db 같은
        # 상대 경로가 깨지지 않으려면 cd /d "%~dp0" 가 반드시 있어야 한다.
        for path in (LAUNCHER, SHORTCUT_BAT):
            with self.subTest(name=path.name):
                self.assertIn('cd /d "%~dp0"', path.read_text(encoding="ascii"))

    def test_finder_probes_before_trusting_python(self) -> None:
        # PATH의 python은 대개 Store 스텁이라, 실행해보지 않고 믿으면 안 된다.
        text = FINDER.read_text(encoding="ascii")
        self.assertIn('-c "import sys"', text)
        self.assertIn(":probe", text)


class TestPowerShellQuoting(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(make_shortcut._ps_quote("abc"), "'abc'")

    def test_single_quote_is_doubled(self) -> None:
        # PowerShell 작은따옴표 문자열 안에서 ' 는 '' 로 이스케이프한다.
        self.assertEqual(make_shortcut._ps_quote("it's"), "'it''s'")

    def test_korean_and_spaces_survive(self) -> None:
        quoted = make_shortcut._ps_quote(r"C:\경로 with space\FM24 트래커.bat")
        self.assertTrue(quoted.startswith("'") and quoted.endswith("'"))
        self.assertIn("트래커", quoted)

    def test_path_object_is_accepted(self) -> None:
        self.assertIn("football", make_shortcut._ps_quote(Path("football/x.bat")))


class TestShortcutConfig(unittest.TestCase):
    def test_launcher_name_matches_the_real_file(self) -> None:
        self.assertTrue((make_shortcut.PROJECT_ROOT / make_shortcut.LAUNCHER_NAME).is_file())

    def test_encoded_command_round_trips(self) -> None:
        import base64

        script = "Write-Output '한글 테스트'"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        self.assertEqual(base64.b64decode(encoded).decode("utf-16-le"), script)


if __name__ == "__main__":
    unittest.main()
