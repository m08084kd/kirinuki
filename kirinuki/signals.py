"""盛り上がり検知に使う各種シグナルを「1秒ごとの数値列」として取り出す。

- audio:   音量 (RMS, dB)。叫び声・笑い声・BGMの盛り上がりに反応する。
- chat:    ライブチャットのリプレイ。コメント数と「草」「w」「!!」などの盛り上がりワード。
- heatmap: YouTube の「最も再生された部分」グラフ。視聴者が何度も見返した箇所。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .ffmpeg import ffmpeg_path

AUDIO_SAMPLE_RATE = 8000


def audio_loudness(media_path: str | Path) -> np.ndarray:
    """メディアの音声を1秒ごとの音量(dB)に変換する。

    長時間の配信でもメモリを食わないよう、ffmpeg の出力を1秒ずつ読む。
    """
    cmd = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error",
        "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    chunk_bytes = AUDIO_SAMPLE_RATE * 2
    levels: list[float] = []
    while True:
        buf = proc.stdout.read(chunk_bytes)
        if not buf:
            break
        samples = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float64)
        if samples.size == 0:
            break
        rms = np.sqrt(np.mean(samples**2)) / 32768.0
        levels.append(20 * np.log10(max(rms, 1e-5)))
    stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"音声の解析に失敗しました:\n{stderr.strip()}")
    return np.array(levels, dtype=np.float64)


# 「盛り上がり」を示すコメントのパターン。マッチするとそのコメントの重みが増える。
_HYPE_PATTERNS = [
    re.compile(r"[wｗＷ]{2,}"),          # www
    re.compile(r"草|くさ|kusa", re.I),
    re.compile(r"笑|lol|lmao|haha", re.I),
    re.compile(r"[!！?？]{2,}"),
    re.compile(r"8{3,}|８{3,}|パチパチ|拍手"),
    re.compile(r"神|うま|すご|やば|えぐ|最高|かわいい|可愛い|nice|gg|pog|clip", re.I),
    re.compile(r"きた|キタ|来た|おめでと|kita", re.I),
]


def comment_weight(text: str) -> float:
    """1コメントの盛り上がり度。基本1、盛り上がりワードごとに+0.5。"""
    weight = 1.0
    for pattern in _HYPE_PATTERNS:
        if pattern.search(text):
            weight += 0.5
    return weight


def _runs_text(message: dict) -> str:
    parts = []
    for run in message.get("runs", []):
        if "text" in run:
            parts.append(run["text"])
        elif "emoji" in run:
            shortcuts = run["emoji"].get("shortcuts") or [""]
            parts.append(shortcuts[0])
    return "".join(parts)


def parse_live_chat(path: str | Path) -> list[tuple[float, str, float]]:
    """yt-dlp が保存する *.live_chat.json (JSON Lines) を読む。

    戻り値: (動画内の秒数, コメント本文, 重み) のリスト。スパチャ・メンバー加入は重め。
    """
    comments: list[tuple[float, str, float]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            replay = data.get("replayChatItemAction", {})
            offset_ms = replay.get("videoOffsetTimeMsec")
            if offset_ms is None:
                continue
            t = int(offset_ms) / 1000.0
            if t < 0:
                continue
            for action in replay.get("actions", []):
                item = action.get("addChatItemAction", {}).get("item", {})
                if "liveChatTextMessageRenderer" in item:
                    text = _runs_text(item["liveChatTextMessageRenderer"].get("message", {}))
                    comments.append((t, text, comment_weight(text)))
                elif "liveChatPaidMessageRenderer" in item:
                    text = _runs_text(item["liveChatPaidMessageRenderer"].get("message", {}))
                    comments.append((t, text, comment_weight(text) + 3.0))
                elif "liveChatMembershipItemRenderer" in item:
                    comments.append((t, "", 2.0))
    return comments


def chat_activity(
    comments: list[tuple[float, str, float]], duration: int, delay: float = 5.0
) -> np.ndarray:
    """コメントを1秒ごとの盛り上がり量に集計する。

    視聴者のコメントは出来事から数秒遅れるので、delay 秒だけ前にずらす。
    """
    series = np.zeros(duration, dtype=np.float64)
    for t, _text, weight in comments:
        idx = int(t - delay)
        if 0 <= idx < duration:
            series[idx] += weight
    return series


def heatmap_series(heatmap: list[dict], duration: int) -> np.ndarray:
    """yt-dlp の info['heatmap'] (「最も再生された部分」) を1秒ごとに展開する。"""
    series = np.zeros(duration, dtype=np.float64)
    for seg in heatmap or []:
        start = max(0, int(seg.get("start_time", 0)))
        end = min(duration, int(np.ceil(seg.get("end_time", start + 1))))
        series[start:end] = np.maximum(series[start:end], float(seg.get("value", 0.0)))
    return series
