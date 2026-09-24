"""合成した動画(途中で大きな音が鳴る)を使って、検出〜書き出しまで通す。"""

import json
import subprocess

import pytest

from kirinuki import cli, ffmpeg


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    try:
        exe = ffmpeg.ffmpeg_path()
    except ffmpeg.FFmpegNotFound:
        pytest.skip("ffmpeg がありません")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    # 180秒の動画。100〜110秒だけ音が大きい
    subprocess.run([
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=180",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=180",
        "-af", "volume='if(between(t,100,110),1.0,0.05)':eval=frame",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
        str(path),
    ], check=True)
    return path


def _duration(exe, path):
    out = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = out.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@pytest.mark.parametrize("vertical", [False, True])
def test_local_file_end_to_end(sample_video, tmp_path, vertical):
    out = tmp_path / "out"
    argv = [str(sample_video), "-n", "1", "-o", str(out)]
    if vertical:
        argv.append("--vertical")
    assert cli.main(argv) == 0

    report = json.loads((out / "sample_report.json").read_text(encoding="utf-8"))
    (clip,) = report["clips"]
    assert clip["start"] <= 100 and clip["end"] >= 110
    assert 30 <= clip["length"] <= 60

    (mp4,) = out.glob("*.mp4")
    assert abs(_duration(ffmpeg.ffmpeg_path(), mp4) - clip["length"]) < 1.0
    if vertical:
        info = subprocess.run([ffmpeg.ffmpeg_path(), "-i", str(mp4)],
                              capture_output=True, text=True).stderr
        assert "1080x1920" in info
