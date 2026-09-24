"""コマンドライン: kirinuki <YouTube URL> で盛り上がり箇所を切り抜く。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__, detect, download, render, signals


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kirinuki",
        description="YouTube動画の盛り上がった箇所を自動検知して切り抜き動画を作ります。",
    )
    p.add_argument("source", help="YouTube の URL またはローカルの動画ファイル")
    p.add_argument("-n", "--count", type=int, default=3, help="作る切り抜きの数 (既定: 3)")
    p.add_argument("--min", dest="min_len", type=int, default=30, help="最短秒数 (既定: 30)")
    p.add_argument("--max", dest="max_len", type=int, default=60, help="最長秒数 (既定: 60)")
    p.add_argument("-o", "--output", default="clips", help="出力フォルダ (既定: ./clips)")
    p.add_argument("--vertical", action="store_true", help="縦型 1080x1920 (ショート向け) で書き出す")
    p.add_argument("--dry-run", action="store_true", help="検出結果だけ表示し、動画は作らない")
    p.add_argument("--full-download", action="store_true",
                   help="区間ごとではなく動画全体をDLしてから切る(区間DLが失敗する場合に)")
    p.add_argument("--max-height", type=int, default=1080, help="DLする映像の最大高さ (既定: 1080)")
    p.add_argument("--chat-file", help="ローカル動画用: yt-dlp の *.live_chat.json")
    p.add_argument("--chat-delay", type=float, default=5.0,
                   help="コメントが出来事から遅れる秒数 (既定: 5)")
    p.add_argument("--lead-in", type=int, default=10, help="山場の前に入れる前振り秒数 (既定: 10)")
    p.add_argument("--no-audio", action="store_true", help="音量を検知に使わない")
    p.add_argument("--no-chat", action="store_true", help="チャットを検知に使わない")
    p.add_argument("--no-heatmap", action="store_true", help="「最も再生された部分」を使わない")
    p.add_argument("--keep-temp", action="store_true", help="作業ファイルを消さない")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _safe_name(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")
    return text[:limit] or "video"


def analyze(args, workdir: Path):
    """シグナルを集めて (info, duration, score, parts, audio_db) を返す。"""
    local = Path(args.source).exists()
    info: dict = {}
    heat = chat = audio_db = None

    if local:
        media = Path(args.source)
        info = {"title": media.stem}
        log("音量を解析中...")
        audio_db = signals.audio_loudness(media)
        duration = audio_db.size
        if args.no_audio:
            audio_db = None
    else:
        log("動画情報を取得中...")
        info = download.fetch_info(args.source)
        if info.get("is_live"):
            raise SystemExit("配信中のライブには対応していません。アーカイブになってから実行してください。")
        duration = int(info.get("duration") or 0)
        log(f"  {info.get('title')} ({fmt_time(duration)})")

        if not args.no_heatmap:
            if info.get("heatmap"):
                heat = signals.heatmap_series(info["heatmap"], duration)
                log("  「最も再生された部分」データ: あり")
            else:
                log("  「最も再生された部分」データ: なし")

        if not args.no_chat and download.has_live_chat(info):
            log("チャットリプレイを取得中...")
            try:
                chat_path = download.download_live_chat(args.source, workdir)
                if chat_path:
                    args.chat_file = str(chat_path)
            except Exception as e:  # チャットが取れなくても他のシグナルで続行
                log(f"  チャットの取得に失敗しました: {e}")

        if not args.no_audio:
            log("音声をダウンロードして解析中...")
            audio_path = download.download_audio(args.source, workdir)
            audio_db = signals.audio_loudness(audio_path)
            if not duration:
                duration = audio_db.size

    if args.chat_file and not args.no_chat:
        comments = signals.parse_live_chat(args.chat_file)
        log(f"  コメント数: {len(comments)}")
        chat = signals.chat_activity(comments, duration, delay=args.chat_delay)

    if duration <= 0:
        raise SystemExit("動画の長さを取得できませんでした。")
    score, parts = detect.build_score(duration, audio_db=audio_db, chat=chat, heatmap=heat)
    if not parts:
        raise SystemExit("盛り上がりを判定できるデータがありませんでした。")
    log(f"使用したシグナル: {', '.join(parts)}")
    return info, duration, score, parts, audio_db


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_len > args.max_len:
        raise SystemExit("--min は --max 以下にしてください。")

    workdir = Path(tempfile.mkdtemp(prefix="kirinuki_"))
    try:
        info, duration, score, parts, audio_db = analyze(args, workdir)
        clips = detect.pick_clips(
            score, count=args.count, min_len=args.min_len, max_len=args.max_len,
            lead_in=args.lead_in, audio_db=audio_db,
        )
        if not clips:
            raise SystemExit("切り抜き候補が見つかりませんでした。")

        video_id = info.get("id")
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = _safe_name(info.get("title") or "video")

        print(f"\n盛り上がり候補 ({len(clips)}件)")
        if len(clips) < args.count:
            print(f"  ※ 十分に盛り上がった箇所が {len(clips)} 件しか見つかりませんでした")
        report = []
        for i, c in enumerate(clips, 1):
            link = f"https://youtu.be/{video_id}?t={c.start}" if video_id else None
            print(f"  #{i}  {fmt_time(c.start)} - {fmt_time(c.end)}  ({c.length}秒)"
                  f"  スコア {c.score:.2f}" + (f"  {link}" if link else ""))
            report.append({
                "rank": i, "start": c.start, "end": c.end, "length": c.length,
                "peak": c.peak, "score": round(c.score, 4), "url": link,
                "signals_at_peak": {k: round(float(v[c.peak]), 4) for k, v in parts.items()},
            })

        report_path = out_dir / f"{base}_report.json"
        report_path.write_text(json.dumps({
            "source": args.source, "title": info.get("title"), "duration": duration,
            "signals": list(parts), "clips": report,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.dry_run:
            print(f"\nレポート: {report_path}")
            return 0

        local = Path(args.source).exists()
        full_video: Path | None = Path(args.source) if local else None
        for i, c in enumerate(clips, 1):
            dst = out_dir / f"{base}_{i:02d}_{fmt_time(c.start).replace(':', '-')}.mp4"
            log(f"書き出し中 #{i} → {dst}")
            if full_video is None and not args.full_download:
                try:
                    section = download.download_section(
                        args.source, c.start, c.end, workdir / f"section_{i:02d}",
                        max_height=args.max_height)
                    render.cut(section, dst, 0, c.length, vertical=args.vertical)
                    continue
                except Exception as e:
                    log(f"  区間ダウンロードに失敗したため動画全体をダウンロードします: {e}")
            if full_video is None:
                log("動画全体をダウンロード中...")
                full_video = download.download_video(args.source, workdir, args.max_height)
            render.cut(full_video, dst, c.start, c.length, vertical=args.vertical)

        print(f"\n完了: {out_dir.resolve()}")
        return 0
    finally:
        if args.keep_temp:
            log(f"作業フォルダ: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
