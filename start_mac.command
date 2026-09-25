#!/bin/bash
# ダブルクリックで切り抜きアプリを起動します (macOS / Linux)
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 が見つかりません。https://www.python.org/downloads/ からインストールしてください。"
  read -r -p "Enter で閉じます"
  exit 1
fi

if [ ! -x .venv/bin/kirinuki-app ]; then
  echo "初回セットアップ中です (数分かかります)..."
  python3 -m venv .venv || exit 1
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install ".[ffmpeg]" || { read -r -p "セットアップに失敗しました。Enter で閉じます"; exit 1; }
fi

# yt-dlp は YouTube の仕様変更に合わせて頻繁に更新されるので、起動時に最新化する
.venv/bin/pip install --upgrade --quiet yt-dlp >/dev/null 2>&1

.venv/bin/kirinuki-app "$@"
