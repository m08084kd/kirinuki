@echo off
rem ダブルクリックで切り抜きアプリを起動します (Windows)
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python が見つかりません。https://www.python.org/downloads/ からインストールしてください。
  echo インストール時に "Add python.exe to PATH" にチェックを入れてください。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\kirinuki-app.exe" (
  echo 初回セットアップ中です ^(数分かかります^)...
  python -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install ".[ffmpeg]" || goto :fail
)

rem yt-dlp は YouTube の仕様変更に合わせて頻繁に更新されるので、起動時に最新化する
".venv\Scripts\python.exe" -m pip install --upgrade --quiet yt-dlp >nul 2>nul

".venv\Scripts\kirinuki-app.exe" %*
exit /b 0

:fail
echo セットアップに失敗しました。
pause
exit /b 1
