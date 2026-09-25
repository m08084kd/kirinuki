"""合成した動画(途中で大きな音が鳴る)を使って、検出〜書き出しまで通す。"""

import json
import subprocess

import pytest

from kirinuki import cli, ffmpeg


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
