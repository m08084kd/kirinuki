"""解析から書き出しまでの一連の処理。CLI と Web アプリの両方から使う。"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import detect, download, render, signals

Logger = Callable[[str], None]


class KirinukiError(RuntimeError):
    """ユーザーに見せるべきエラー。"""


@dataclass
class Options:
    source: str
    count: int = 3
    min_len: int = 30
    max_len: int = 60
    output: str = "clips"
    vertical: bool = False
    dry_run: bool = False
    full_download: bool = False
    max_height: int = 1080
    chat_file: str | None = None
    chat_delay: float = 5.0
    lead_in: int = 10
    no_audio: bool = False
    no_chat: bool = False
    no_heatmap: bool = False
    keep_temp: bool = False


@dataclass
class ClipResult:
    rank: int
    start: int
    end: int
    peak: int
    score: float
    url: str | None
    signals_at_peak: dict[str, float]
    file: str | None = None

    @property
    def length(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "rank": self.rank, "start": self.start, "end": self.end, "length": self.length,
            "peak": self.peak, "score": round(self.score, 4), "url": self.url,
            "signals_at_peak": self.signals_at_peak, "file": self.file,
        }


@dataclass
class Result:
    source: str
    title: str | None
    video_id: str | None
    duration: int
    signals: list[str]
    clips: list[ClipResult]
    score: np.ndarray = field(repr=False)
    report_path: Path | None = None

    def to_dict(self, score_points: int = 0) -> dict:
        d = {
            "source": self.source, "title": self.title, "video_id": self.video_id,
            "duration": self.duration, "signals": self.signals,
            "clips": [c.to_dict() for c in self.clips],
        }
        if score_points:
            d["score"] = downsample(self.score, score_points)
        return d


def downsample(x: np.ndarray, points: int) -> list[float]:
    """グラフ表示用に、区間ごとの最大値で間引く。"""
    if x.size == 0:
        return []
    if x.size <= points:
        return [round(float(v), 4) for v in x]
    edges = np.linspace(0, x.size, points + 1).astype(int)
    return [round(float(x[a:b].max()), 4) for a, b in zip(edges[:-1], edges[1:])]


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def safe_name(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")
    return text[:limit] or "video"


def _analyze(opts: Options, workdir: Path, log: Logger):
    local = Path(opts.source).exists()
    info: dict = {}
    heat = chat = audio_db = None
    chat_file = opts.chat_file

    if local:
        media = Path(opts.source)
        info = {"title": media.stem}
        log("音量を解析中...")
        audio_db = signals.audio_loudness(media)
        duration = audio_db.size
        if opts.no_audio:
            audio_db = None
    else:
        log("動画情報を取得中...")
        try:
            info = download.fetch_info(opts.source)
        except Exception as e:
            raise KirinukiError(f"動画情報を取得できませんでした: {e}") from e
        if info.get("is_live"):
            raise KirinukiError("配信中のライブには対応していません。アーカイブになってから実行してください。")
        duration = int(info.get("duration") or 0)
        log(f"  {info.get('title')} ({fmt_time(duration)})")

        if not opts.no_heatmap:
            if info.get("heatmap"):
                heat = signals.heatmap_series(info["heatmap"], duration)
                log("  「最も再生された部分」データ: あり")
            else:
                log("  「最も再生された部分」データ: なし")

        if not opts.no_chat and download.has_live_chat(info):
            log("チャットリプレイを取得中...")
            try:
                chat_file = str(download.download_live_chat(opts.source, workdir) or "") or None
            except Exception as e:  # チャットが取れなくても他のシグナルで続行
                log(f"  チャットの取得に失敗しました: {e}")

        if not opts.no_audio:
            log("音声をダウンロードして解析中...")
            audio_path = download.download_audio(opts.source, workdir)
            audio_db = signals.audio_loudness(audio_path)
            if not duration:
                duration = audio_db.size

    if chat_file and not opts.no_chat:
        comments = signals.parse_live_chat(chat_file)
        log(f"  コメント数: {len(comments)}")
        chat = signals.chat_activity(comments, duration, delay=opts.chat_delay)

    if duration <= 0:
        raise KirinukiError("動画の長さを取得できませんでした。")
    score, parts = detect.build_score(duration, audio_db=audio_db, chat=chat, heatmap=heat)
    if not parts:
        raise KirinukiError("盛り上がりを判定できるデータがありませんでした。")
    log(f"使用したシグナル: {', '.join(parts)}")
    return info, duration, score, parts, audio_db


def run(opts: Options, log: Logger = print) -> Result:
    """URL/ファイルを解析して切り抜きを作る。opts.dry_run なら動画は書き出さない。"""
    import json

    if opts.min_len > opts.max_len:
        raise KirinukiError("最短秒数は最長秒数以下にしてください。")

    workdir = Path(tempfile.mkdtemp(prefix="kirinuki_"))
    try:
        info, duration, score, parts, audio_db = _analyze(opts, workdir, log)
        picked = detect.pick_clips(
            score, count=opts.count, min_len=opts.min_len, max_len=opts.max_len,
            lead_in=opts.lead_in, audio_db=audio_db,
        )
        if not picked:
            raise KirinukiError("切り抜き候補が見つかりませんでした。")

        video_id = info.get("id")
        clips = [
            ClipResult(
                rank=i, start=c.start, end=c.end, peak=c.peak, score=c.score,
                url=f"https://youtu.be/{video_id}?t={c.start}" if video_id else None,
                signals_at_peak={k: round(float(v[c.peak]), 4) for k, v in parts.items()},
            )
            for i, c in enumerate(picked, 1)
        ]
        result = Result(opts.source, info.get("title"), video_id, duration,
                        list(parts), clips, score)
        log(f"盛り上がり候補: {len(clips)}件")

        out_dir = Path(opts.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = safe_name(info.get("title") or "video")

        if not opts.dry_run:
            _render_all(opts, clips, out_dir, base, workdir, log)

        result.report_path = out_dir / f"{base}_report.json"
        result.report_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        if opts.keep_temp:
            log(f"作業フォルダ: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _render_all(opts: Options, clips: list[ClipResult], out_dir: Path, base: str,
                workdir: Path, log: Logger) -> None:
    local = Path(opts.source).exists()
    full_video: Path | None = Path(opts.source) if local else None
    for c in clips:
        dst = out_dir / f"{base}_{c.rank:02d}_{fmt_time(c.start).replace(':', '-')}.mp4"
        log(f"書き出し中 {c.rank}/{len(clips)}: {fmt_time(c.start)} - {fmt_time(c.end)}")
        c.file = dst.name
        if full_video is None and not opts.full_download:
            try:
                section = download.download_section(
                    opts.source, c.start, c.end, workdir / f"section_{c.rank:02d}",
                    max_height=opts.max_height)
                render.cut(section, dst, 0, c.length, vertical=opts.vertical)
                continue
            except Exception as e:
                log(f"  区間ダウンロードに失敗したため動画全体をダウンロードします: {e}")
        if full_video is None:
            log("動画全体をダウンロード中...")
            full_video = download.download_video(opts.source, workdir, opts.max_height)
        render.cut(full_video, dst, c.start, c.length, vertical=opts.vertical)
