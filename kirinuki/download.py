"""yt-dlp を使った YouTube からの取得処理。"""

from __future__ import annotations

from pathlib import Path

from .ffmpeg import ffmpeg_path


def _base_opts(quiet: bool = True) -> dict:
    opts = {
        "quiet": quiet, "no_warnings": quiet, "noprogress": quiet,
        # YouTube の取得には JavaScript 実行環境が要る。入っているものを使う
        "js_runtimes": {"deno": {}, "node": {}},
    }
    try:
        opts["ffmpeg_location"] = ffmpeg_path()
    except Exception:
        pass
    return opts


def _ydl(opts: dict):
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


def fetch_info(url: str) -> dict:
    """動画情報(長さ・タイトル・「最も再生された部分」のheatmap等)を取得する。"""
    with _ydl(_base_opts()) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def has_live_chat(info: dict) -> bool:
    return "live_chat" in (info.get("subtitles") or {})


def download_live_chat(url: str, workdir: Path) -> Path | None:
    """ライブ配信アーカイブのチャットリプレイを保存する。無ければ None。"""
    opts = {
        **_base_opts(),
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
        "outtmpl": str(workdir / "chat.%(ext)s"),
    }
    with _ydl(opts) as ydl:
        ydl.download([url])
    found = sorted(workdir.glob("chat*.live_chat.json"))
    return found[0] if found else None


def download_audio(url: str, workdir: Path) -> Path:
    """解析用に音声だけをダウンロードする(映像より圧倒的に軽い)。"""
    opts = {
        **_base_opts(),
        "format": "ba/b",
        "outtmpl": str(workdir / "audio.%(ext)s"),
    }
    with _ydl(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def _video_format(max_height: int) -> str:
    return f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"


def download_video(url: str, workdir: Path, max_height: int = 1080) -> Path:
    """動画全体をダウンロードする。"""
    opts = {
        **_base_opts(quiet=False),
        "format": _video_format(max_height),
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "video.%(ext)s"),
    }
    with _ydl(opts) as ydl:
        ydl.download([url])
    found = sorted(workdir.glob("video.*"))
    if not found:
        raise RuntimeError("動画のダウンロードに失敗しました")
    return found[0]


def download_section(
    url: str, start: float, end: float, out_stem: Path, max_height: int = 1080
) -> Path:
    """指定区間だけをダウンロードする(長時間配信向け)。"""
    from yt_dlp.utils import download_range_func

    opts = {
        **_base_opts(),
        "format": _video_format(max_height),
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "outtmpl": str(out_stem) + ".%(ext)s",
    }
    with _ydl(opts) as ydl:
        ydl.download([url])
    found = sorted(out_stem.parent.glob(out_stem.name + ".*"))
    if not found:
        raise RuntimeError(f"区間 {start}-{end} のダウンロードに失敗しました")
    return found[0]
