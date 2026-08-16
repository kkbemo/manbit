"""모의고사 도우미 실행 진입점.

PyInstaller로 실행 파일(.exe)을 만들 때 이 파일을 쓴다.
평소에는 `python -m mogosa.app` 과 똑같다.
"""

from mogosa.app import _cli

if __name__ == "__main__":
    _cli()
