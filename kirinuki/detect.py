"""複数のシグナルを合成して盛り上がりスコアを作り、切り抜き区間を決める。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# シグナルごとの重み。取得できたシグナルだけで正規化し直す。
DEFAULT_WEIGHTS = {"heatmap": 0.4, "chat": 0.35, "audio": 0.25}


@dataclass
class Clip:
    start: int
    end: int
    peak: int
    score: float

    @property
    def length(self) -> int:
        return self.end - self.start


def moving_average(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or x.size == 0:
        return x.astype(np.float64)
    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def local_baseline(x: np.ndarray, block: int = 60, span: int = 5) -> np.ndarray:
    """ゆっくり変化する「普段のレベル」を推定する(ブロックごとの中央値を平滑化)。"""
    if x.size == 0:
        return x
    n_blocks = int(np.ceil(x.size / block))
    medians = np.array([np.median(x[i * block:(i + 1) * block]) for i in range(n_blocks)])
    # 前後 span ブロックの中央値で更に均し、盛り上がり自体が基準を押し上げないようにする
    smoothed = np.array([
        np.median(medians[max(0, i - span):i + span + 1]) for i in range(n_blocks)
    ])
    centers = np.arange(n_blocks) * block + block / 2
    return np.interp(np.arange(x.size), centers, smoothed)


def normalize(x: np.ndarray) -> np.ndarray:
    """0〜1 に正規化(上位1%で頭打ち)。ほぼ平坦なシグナルは全て0にする。"""
    x = np.clip(x, 0, None)
    top = np.percentile(x, 99) if x.size else 0.0
    if top <= 1e-9:
        return np.zeros_like(x)
    return np.clip(x / top, 0, 1)


def _fit(x: np.ndarray, duration: int) -> np.ndarray:
    if x.size >= duration:
        return x[:duration]
    return np.pad(x, (0, duration - x.size))


def build_score(
    duration: int,
    audio_db: np.ndarray | None = None,
    chat: np.ndarray | None = None,
    heatmap: np.ndarray | None = None,
    weights: dict[str, float] | None = None,
    smooth: int = 5,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """各シグナルを正規化して重み付き合成する。戻り値は (スコア, 正規化済みシグナル)。"""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    parts: dict[str, np.ndarray] = {}

    if audio_db is not None and audio_db.size:
        a = _fit(audio_db, duration)
        # 普段の声量より大きい部分だけを見る(元々うるさい配信でも効くように)
        parts["audio"] = normalize(moving_average(a - local_baseline(a), 3))
    if chat is not None and chat.size and chat.sum() > 0:
        c = moving_average(_fit(chat, duration), 10)
        parts["chat"] = normalize(c - local_baseline(c))
    if heatmap is not None and heatmap.size and heatmap.max() > 0:
        h = _fit(heatmap, duration)
        h = h - np.percentile(h, 50)
        parts["heatmap"] = normalize(h)

    parts = {k: v for k, v in parts.items() if v.max() > 0 and weights.get(k, 0) > 0}
    if not parts:
        return np.zeros(duration), parts
    total = sum(weights[k] for k in parts)
    score = sum(weights[k] / total * v for k, v in parts.items())
    return moving_average(score, smooth), parts


def _snap(t: int, audio_db: np.ndarray | None, lo: int, hi: int, radius: int = 3) -> int:
    """区切りを近くの静かな秒に寄せ、セリフの途中で切れにくくする。"""
    if audio_db is None or audio_db.size == 0:
        return t
    a, b = max(lo, t - radius), min(hi, t + radius)
    a, b = max(a, 0), min(b, audio_db.size - 1)
    if a > b:
        return t
    return a + int(np.argmin(audio_db[a:b + 1]))


def pick_clips(
    score: np.ndarray,
    count: int = 3,
    min_len: int = 30,
    max_len: int = 60,
    lead_in: int = 10,
    tail: int = 5,
    threshold: float = 0.5,
    audio_db: np.ndarray | None = None,
    min_relative_score: float = 0.2,
) -> list[Clip]:
    """スコアの高い順に、重ならない切り抜き区間を最大 count 個選ぶ。

    ピーク周辺でスコアが threshold*ピーク 以上の範囲を「山場」とし、
    その前に lead_in 秒(前振り)、後に tail 秒(余韻)を付けて min_len〜max_len 秒に収める。
    最高スコアの min_relative_score 倍に満たないピークは盛り上がりとみなさない。
    """
    duration = score.size
    if duration == 0 or min_len > max_len:
        return []
    if duration <= min_len:
        return [Clip(0, duration, int(np.argmax(score)), float(score.max()))]

    available = score.astype(np.float64).copy()
    floor = float(score.max()) * min_relative_score
    clips: list[Clip] = []

    for _ in range(count * 20):
        if len(clips) >= count:
            break
        peak = int(np.argmax(available))
        peak_value = score[peak]
        if not np.isfinite(available[peak]) or peak_value <= 0 or peak_value < floor:
            break

        # 山場の範囲
        cut = peak_value * threshold
        left = peak
        while left > 0 and score[left - 1] >= cut and peak - left < max_len:
            left -= 1
        right = peak
        while right < duration - 1 and score[right + 1] >= cut and right - left < max_len:
            right += 1

        start = left - lead_in
        end = right + 1 + tail
        if end - start < min_len:
            extra = min_len - (end - start)
            start -= int(round(extra * 0.6))
            end = start + min_len if end - start < min_len else end
        if end - start > max_len:
            # 長すぎる場合はピークが前寄り1/3あたりに来るように切る(前振り重視)
            start = max(start, peak - int(max_len * 0.6))
            end = start + max_len

        # 動画の端に合わせてずらす
        if start < 0:
            end, start = end - start, 0
        if end > duration:
            start, end = max(0, start - (end - duration)), duration

        # 無音寄せ(長さの範囲は守る)
        start = _snap(start, audio_db, max(0, end - max_len), min(peak, end - min_len))
        end = _snap(end, audio_db, start + min_len, min(duration, start + max_len))

        # 既存クリップとの重なりを削る
        for other in clips:
            if start < other.end and other.start < end:
                if other.start <= peak < other.end:
                    start = end = peak
                    break
                if other.end <= peak:
                    start = max(start, other.end)
                else:
                    end = min(end, other.start)

        # 採用・不採用に関わらず、このピーク周辺は二度と選ばない
        block_start, block_end = max(0, min(start, left)), min(duration, max(end, right + 1))
        available[block_start:block_end] = -np.inf
        available[max(0, peak - min_len // 2):peak + min_len // 2 + 1] = -np.inf

        if end - start >= min_len:
            clips.append(Clip(int(start), int(end), peak, float(peak_value)))

    return clips
