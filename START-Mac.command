#!/bin/bash
# 맥에서 더블클릭으로 실행. 처음 한 번은 터미널에서
#   chmod +x 모의고사도우미.command
# 을 실행해 주어야 한다.
cd "$(dirname "$0")" || exit 1

echo
echo "  모의고사 도우미를 시작합니다."
echo

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
    echo "  [오류] 파이썬이 설치되어 있지 않습니다."
    echo "  https://www.python.org/downloads/ 에서 내려받아 주세요."
    read -r -p "  엔터를 누르면 닫힙니다."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "  처음 실행이라 준비 작업을 합니다. 1~2분 걸립니다..."
    "$PY" -m venv .venv || { echo "  [오류] 준비 실패"; read -r; exit 1; }
    ./.venv/bin/python -m pip install --upgrade pip -q
    ./.venv/bin/python -m pip install -q -r requirements.txt || {
        echo "  [오류] 준비 실패. 인터넷 연결을 확인해 주세요."; read -r; exit 1; }
    echo "  준비가 끝났습니다."
    echo
fi

echo "  브라우저가 저절로 열립니다. 이 창은 끄지 마세요."
echo "  끝내려면 Ctrl+C 를 누르십시오."
echo

./.venv/bin/python -m mogosa.app
