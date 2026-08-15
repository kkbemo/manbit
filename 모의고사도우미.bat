@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 모의고사 도우미

echo.
echo   모의고사 도우미를 시작합니다.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [오류] 파이썬이 설치되어 있지 않습니다.
    echo.
    echo   https://www.python.org/downloads/ 에서 내려받아 설치해 주세요.
    echo   설치할 때 "Add Python to PATH" 를 반드시 체크하십시오.
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo   처음 실행이라 준비 작업을 합니다. 1~2분 걸립니다...
    python -m venv .venv
    if errorlevel 1 goto :setup_failed
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    if errorlevel 1 goto :setup_failed
    echo   준비가 끝났습니다.
    echo.
)

echo   브라우저가 저절로 열립니다. 이 창은 끄지 마세요.
echo   프로그램을 끝내려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으십시오.
echo.

".venv\Scripts\python.exe" -m mogosa.app
pause
exit /b 0

:setup_failed
echo.
echo   [오류] 준비 작업에 실패했습니다. 인터넷 연결을 확인해 주세요.
echo.
pause
exit /b 1
