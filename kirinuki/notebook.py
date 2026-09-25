"""Google Colab / Jupyter 用のかんたん操作 (iPad などパソコン以外から使うため)。

    from kirinuki import notebook
    notebook.clip("https://www.youtube.com/watch?v=...")
    notebook.save("Googleドライブ")
"""

from __future__ import annotations

import base64
import shutil
import time
from pathlib import Path

from .pipeline import KirinukiError, Options, Result, fmt_time, run

OUTPUT_ROOT = Path("/content/kirinuki_clips") if Path("/content").is_dir() else Path("kirinuki_clips")
DRIVE_DIR = "kirinuki"

_last_dir: Path | None = None

_BOT_HINT = (
    "YouTube に一時的にブロックされた可能性があります。\n"
    "上のメニュー「ランタイム」→「ランタイムを接続解除して削除」を押してから、①からやり直してください。\n"
    "別のサーバーに切り替わり、通ることがあります。"
)


def _display(obj) -> None:
    try:
        from IPython.display import display

        display(obj)
    except ImportError:
        pass


def _hint_for(message: str) -> str | None:
    lowered = message.lower()
    if "sign in" in lowered or "bot" in lowered or "429" in lowered or "403" in lowered:
        return _BOT_HINT
    if "private" in lowered or "members" in lowered or "メンバー" in message:
        return "非公開・メンバー限定の動画は切り抜けません。"
    if "unsupported url" in lowered or "is not a valid url" in lowered:
        return "URL が正しいか確認してください (https://www.youtube.com/watch?v=... の形)。"
    return None


def clip(
    url: str,
    count: int = 3,
    min_len: int = 30,
    max_len: int = 60,
    lead_in: int = 10,
    vertical: bool = False,
    max_height: int = 720,
    show: bool = True,
) -> Result | None:
    """URL の動画から盛り上がり箇所を切り抜き、グラフと動画を表示する。"""
    global _last_dir
    url = (url or "").strip()
    if not url:
        print("❌ URL を入力してから ▶ を押してください。")
        return None

    out_dir = OUTPUT_ROOT / time.strftime("%Y%m%d_%H%M%S")
    opts = Options(
        source=url, count=count, min_len=min_len, max_len=max_len, lead_in=lead_in,
        vertical=vertical, max_height=max_height, output=str(out_dir),
    )
    started = time.time()
    try:
        result = run(opts, log=print)
    except Exception as e:
        message = str(e)
        print(f"\n❌ エラー: {message}" if isinstance(e, KirinukiError)
              else f"\n❌ エラー: {type(e).__name__}: {message}")
        hint = _hint_for(message)
        if hint:
            print("\n💡 " + hint)
        return None

    _last_dir = out_dir
    print(f"\n✅ 完了 ({int(time.time() - started)}秒)  切り抜き {len(result.clips)}本")
    if len(result.clips) < count:
        print(f"   ※ 十分に盛り上がった箇所が {len(result.clips)} 件しか見つかりませんでした")
    if show:
        _show_chart(result)
        _show_clips(result, out_dir, vertical)
        print("\n👉 気に入ったら ③ で保存してください。")
    return result


def _show_chart(result: Result) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return
    score = result.score
    minutes = np.arange(score.size) / 60
    fig, ax = plt.subplots(figsize=(10, 2.4))
    ax.fill_between(minutes, score, color="#e8453c", alpha=0.18, linewidth=0)
    ax.plot(minutes, score, color="#e8453c", linewidth=1.2)
    top = float(score.max()) or 1.0
    for c in result.clips:
        ax.axvspan(c.start / 60, c.end / 60, color="black", alpha=0.08)
        ax.text((c.start + c.end) / 120, top * 1.02, f"#{c.rank}",
                ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax.set_xlim(0, max(minutes[-1], 1 / 60))
    ax.set_ylim(0, top * 1.15)
    ax.set_yticks([])
    ax.set_xlabel("minutes")
    ax.set_title("Highlight score", loc="left", fontsize=10)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    plt.show()


def _video_html(path: Path, width: int) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (f'<video controls playsinline preload="metadata" width="{width}" '
            f'style="max-width:100%;border-radius:8px;background:#000" '
            f'src="data:video/mp4;base64,{data}"></video>')


def _show_clips(result: Result, out_dir: Path, vertical: bool) -> None:
    try:
        from IPython.display import HTML
    except ImportError:
        HTML = None
    for c in result.clips:
        print(f"\n#{c.rank}  {fmt_time(c.start)} - {fmt_time(c.end)}  ({c.length}秒)")
        if c.url:
            print(f"   元の動画: {c.url}")
        path = out_dir / c.file if c.file else None
        if path is None or not path.exists():
            continue
        if HTML is not None:
            _display(HTML(_video_html(path, 270 if vertical else 480)))
        else:
            print(f"   {path}")


def save(destination: str = "Googleドライブ") -> None:
    """直前に作った切り抜きを保存する。"Googleドライブ" か "ZIP" (ダウンロード)。"""
    if _last_dir is None or not _last_dir.exists():
        print("❌ まだ切り抜きがありません。先に ② を実行してください。")
        return
    files = sorted(_last_dir.glob("*.mp4"))
    if not files:
        print("❌ 保存する動画がありません。")
        return

    try:
        from google.colab import drive, files as colab_files
    except ImportError:
        print(f"保存先: {_last_dir.resolve()}")
        return

    if "ドライブ" in destination or "drive" in destination.lower():
        drive.mount("/content/drive")
        dst = Path("/content/drive/MyDrive") / DRIVE_DIR / _last_dir.name
        dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dst / f.name)
        print(f"✅ Google ドライブの「マイドライブ/{DRIVE_DIR}/{_last_dir.name}」に {len(files)}本 保存しました。")
        print("📱 iPad の「Google ドライブ」アプリで開き、⋯ →「コピーを送信」→「ビデオを保存」で写真アプリに入れられます。")
    else:
        zip_path = shutil.make_archive(str(_last_dir), "zip", _last_dir)
        print("ダウンロードを開始します。iPad では画面上部のダウンロードボタン (↓) から「ファイル」アプリに保存されます。")
        colab_files.download(zip_path)
