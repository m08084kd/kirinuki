"""ffmpeg で切り抜き動画を書き出す。"""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg

# 縦型(ショート向け): ぼかした背景の上に元映像を中央配置する
_VERTICAL_FILTER = (
    "[0:v]split[bg][fg];"
    "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
    "[fg]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
)


def cut(
    src: str | Path,
    dst: str | Path,
    start: float,
    duration: float,
    vertical: bool = False,
    fade: float = 0.3,
) -> Path:
    """src の start 秒から duration 秒を切り出して dst に保存する(再エンコードで正確に切る)。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fade_out = max(0.0, duration - fade)
    args = ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
    if vertical:
        args += ["-filter_complex", _VERTICAL_FILTER, "-map", "[v]", "-map", "0:a?"]
    else:
        args += ["-map", "0:v?", "-map", "0:a?", "-vf", "format=yuv420p"]
    args += [
        "-af", f"afade=t=in:d={fade},afade=t=out:st={fade_out:.3f}:d={fade}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ]
    ffmpeg.run(args)
    return dst
