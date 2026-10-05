@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Hallez FAMQ Bot

python --version
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and tick "Add Python to PATH".
    pause
    exit /b
)

if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating .venv ...
    python -m venv .venv
)

if not exist ".env" (
    echo [!] .env file not found. Put BOT_TOKEN into .env
    pause
    exit /b
)

rem Install requirements only when requirements.txt changed
set REQHASH=
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile requirements.txt MD5') do if not defined REQHASH set "REQHASH=%%H"
set OLDHASH=
if exist ".venv\req.hash" set /p OLDHASH=<".venv\req.hash"

if "%REQHASH%"=="%OLDHASH%" (
    echo [*] Requirements unchanged, skipping install.
) else (
    echo [*] Installing requirements ... first run may take a few minutes
    .venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [!] pip failed. Most likely git is not installed.
        echo     Install Git from https://git-scm.com or remove the git line from requirements.txt.
        pause
        exit /b
    )
    >".venv\req.hash" echo %REQHASH%
)

echo [*] Starting bot ...
.venv\Scripts\python.exe -u main.py

echo.
echo [*] Bot stopped. See messages above.
pause
