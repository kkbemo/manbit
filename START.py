"""모의고사 도우미 — 이 파일을 두 번 누르면 실행됩니다.

윈도우가 .bat 파일을 위험한 형식으로 보고 막는 일이 잦아서, 실행을 파이썬
파일 하나로 옮겼다. 이 파일은 필요한 것을 알아서 챙긴 뒤 프로그램을 켠다.

    1. 파이썬 버전을 확인한다
    2. 없는 부품이 있으면 인터넷에서 받아 온다 (처음 한 번만)
    3. 프로그램을 켜고 인터넷 창을 연다

두 번 눌러서 실행했을 때 오류가 나도 창이 바로 닫히지 않게 해 두었다.
그래야 무엇이 잘못됐는지 읽을 수 있다.
"""

import os
import subprocess
import sys
from pathlib import Path

NEEDED = [
    ("flask", "flask>=3.0"),
    ("hwpx", "python-hwpx>=6.0"),
    ("pdfplumber", "pdfplumber>=0.11"),
]

HERE = Path(__file__).resolve().parent


def banner() -> None:
    print()
    print("=" * 52)
    print("   모의고사 도우미")
    print("=" * 52)
    print()


def check_python() -> None:
    if sys.version_info < (3, 9):
        raise SystemExit(
            f"파이썬이 너무 오래된 판입니다 (지금 {sys.version.split()[0]}).\n"
            "python.org/downloads 에서 최신판을 설치해 주세요."
        )


def missing_parts() -> list[str]:
    missing = []
    for module, requirement in NEEDED:
        try:
            __import__(module)
        except ImportError:
            missing.append(requirement)
    return missing


def install(requirements: list[str]) -> None:
    print("  처음 실행이라 필요한 부품을 받아 옵니다.")
    print("  1~3분 걸립니다. 글자가 올라가는 동안 그냥 기다려 주세요.")
    print()

    command = [sys.executable, "-m", "pip", "install", "--quiet", *requirements]
    result = subprocess.run(command)

    if result.returncode != 0:
        # 회사·학교 컴퓨터에서 권한 때문에 막히는 일이 있어 한 번 더 시도한다
        print("  다른 방법으로 다시 시도합니다...")
        result = subprocess.run([*command, "--user"])

    if result.returncode != 0:
        raise SystemExit(
            "부품을 받아 오지 못했습니다.\n"
            "인터넷 연결을 확인하고 다시 실행해 주세요.\n"
            "학교 컴퓨터라면 인터넷 차단 정책 때문일 수 있습니다."
        )

    print()
    print("  준비가 끝났습니다.")
    print()


def main() -> None:
    banner()
    check_python()

    os.chdir(HERE)
    sys.path.insert(0, str(HERE))

    missing = missing_parts()
    if missing:
        install(missing)

    try:
        from mogosa.app import main as run_app
    except ImportError as error:
        raise SystemExit(
            f"프로그램 파일을 찾지 못했습니다 ({error}).\n"
            "압축을 풀 때 일부 파일이 빠졌을 수 있습니다.\n"
            "압축 파일을 다시 받아서 풀어 주세요."
        )

    print("  잠시 뒤 인터넷 창이 저절로 열립니다.")
    print()
    print("  *** 이 검은 창은 끄지 마세요. 프로그램 본체입니다. ***")
    print("  다 쓰신 뒤에 이 창을 닫으면 프로그램이 꺼집니다.")
    print()

    run_app()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  프로그램을 끝냅니다.")
    except SystemExit as error:
        if error.code and not isinstance(error.code, int):
            print()
            print("  ── 문제가 생겼습니다 ─────────────────────")
            print(f"  {error.code}")
            print("  ──────────────────────────────────────────")
        print()
        input("  엔터를 누르면 창이 닫힙니다. ")
    except Exception as error:  # 창이 그냥 닫혀 버리면 원인을 알 수 없다
        import traceback

        print()
        print("  ── 예상치 못한 문제가 생겼습니다 ─────────")
        traceback.print_exc()
        print("  ──────────────────────────────────────────")
        print("  위 내용을 사진으로 찍어 물어보시면 됩니다.")
        print()
        input("  엔터를 누르면 창이 닫힙니다. ")
