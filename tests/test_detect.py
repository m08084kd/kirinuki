import numpy as np

from kirinuki import detect


def test_pick_clips_finds_peaks_within_length_bounds():
    rng = np.random.default_rng(0)
    score = rng.random(1200) * 0.1
    score[300:320] = 1.0
    score[800:810] = 0.8
    clips = detect.pick_clips(score, count=2, min_len=30, max_len=60)
    assert len(clips) == 2
    assert 300 <= clips[0].peak < 320
    assert 800 <= clips[1].peak < 810
    for c in clips:
        assert 30 <= c.length <= 60
        assert c.start <= c.peak < c.end
        # 前振りが入っている
        assert c.peak - c.start >= 5


def test_clips_do_not_overlap():
    score = np.zeros(600)
    score[100:110] = 1.0
    score[140:150] = 0.9  # 近すぎるピーク
    score[400:405] = 0.5
    clips = detect.pick_clips(score, count=3, min_len=30, max_len=60)
    spans = sorted((c.start, c.end) for c in clips)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2


def test_long_plateau_is_capped_to_max_len():
    score = np.zeros(1000)
    score[200:500] = 1.0
    (clip,) = detect.pick_clips(score, count=1, min_len=30, max_len=60)
    assert clip.length == 60


def test_clip_at_video_edges_stays_in_range():
    score = np.zeros(200)
    score[0:3] = 1.0
    score[197:200] = 0.9
    clips = detect.pick_clips(score, count=2, min_len=30, max_len=60)
    for c in clips:
        assert 0 <= c.start < c.end <= 200
        assert 30 <= c.length <= 60


def test_short_video_returns_whole_video():
    clips = detect.pick_clips(np.ones(20), count=3, min_len=30, max_len=60)
    assert [(c.start, c.end) for c in clips] == [(0, 20)]


def test_build_score_uses_only_available_signals():
    audio = np.full(600, -30.0)
    audio[250:260] = -5.0
    score, parts = detect.build_score(600, audio_db=audio)
    assert list(parts) == ["audio"]
    assert 245 <= int(np.argmax(score)) <= 265


def test_build_score_combines_signals():
    n = 900
    audio = np.full(n, -30.0)
    chat = np.ones(n)
    chat[600:620] = 20
    heat = np.full(n, 0.2)
    heat[590:640] = 1.0
    score, parts = detect.build_score(n, audio_db=audio, chat=chat, heatmap=heat)
    assert set(parts) == {"chat", "heatmap"}  # 平坦な音量は無視される
    assert 580 <= int(np.argmax(score)) <= 640


def test_weak_peaks_are_not_padded_as_clips():
    score = np.zeros(1000)
    score[300:310] = 1.0
    score[700:705] = 0.05
    clips = detect.pick_clips(score, count=3, min_len=30, max_len=60)
    assert len(clips) == 1
