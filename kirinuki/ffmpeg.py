"""ffmpeg 実行ファイルの探索と実行ヘルパー。"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


class FFmpegNotFound(RuntimeError):
    pass


@lru_cache(maxsize=None)
def ffmpeg_path() -> str:
    """ffmpeg を探す。環境変数 KIRINUKI_FFMPEG → PATH → imageio-ffmpeg の順。"""
    env = os.environ.get("KIRINUKI_FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise FFmpegNotFound(
        "ffmpeg が見つかりません。ffmpeg をインストールするか "
        "`pip install imageio-ffmpeg` を実行してください。"
    )


def run(args: list[str]) -> None:
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg の実行に失敗しました:\n{result.stderr.strip()}")
