import subprocess

import pytest

from kirinuki import ffmpeg


@pytest.fixture(scope="session")
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
