@echo off
cd /d "%~dp0"
title 모의고사 도우미

echo.
echo   모의고사 도우미를 시작합니다.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [오류] 파이썬이 설치되어 있지 않습니다.
    echo.
    echo   python.org/downloads 에서 내려받아 설치해 주세요.
    echo   설치 첫 화면 맨 아래 "Add python.exe to PATH" 를
    echo   반드시 체크하셔야 합니다.
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo   처음 실행이라 준비 작업을 합니다. 1~2분 걸립니다.
    echo   글자가 올라가는 동안 그냥 기다려 주세요.
    echo.
    python -m venv .venv
    if errorlevel 1 goto :setup_failed
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    if errorlevel 1 goto :setup_failed
    echo   준비가 끝났습니다.
    echo.
)

echo   잠시 뒤 인터넷 창이 저절로 열립니다.
echo.
echo   *** 이 검은 창은 끄지 마세요. 프로그램 본체입니다. ***
echo   다 쓰신 뒤에 이 창을 닫으면 프로그램이 꺼집니다.
echo.

".venv\Scripts\python.exe" -m mogosa.app
pause
exit /b 0

:setup_failed
echo.
echo   [오류] 준비 작업에 실패했습니다.
echo   인터넷 연결을 확인하고 다시 실행해 주세요.
echo.
pause
exit /b 1
