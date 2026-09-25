"""コマンドライン: kirinuki <YouTube URL> で盛り上がり箇所を切り抜く。"""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

from . import __version__
from .pipeline import KirinukiError, Options, fmt_time, run


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


def main(argv: list[str] | None = None) -> int:
    args = vars(build_parser().parse_args(argv))
    opts = Options(**{f.name: args[f.name] for f in fields(Options)})
    try:
        result = run(opts, log=log)
    except KirinukiError as e:
        log(f"エラー: {e}")
        return 1

    print(f"\n盛り上がり候補 ({len(result.clips)}件)")
    if len(result.clips) < opts.count:
        print(f"  ※ 十分に盛り上がった箇所が {len(result.clips)} 件しか見つかりませんでした")
    for c in result.clips:
        print(f"  #{c.rank}  {fmt_time(c.start)} - {fmt_time(c.end)}  ({c.length}秒)"
              f"  スコア {c.score:.2f}" + (f"  {c.url}" if c.url else ""))
    if opts.dry_run:
        print(f"\nレポート: {result.report_path}")
    else:
        print(f"\n完了: {Path(opts.output).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
