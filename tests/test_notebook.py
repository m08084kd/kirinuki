from kirinuki import notebook


def test_clip_and_save(sample_video, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(notebook, "OUTPUT_ROOT", tmp_path)
    result = notebook.clip(str(sample_video), count=1, show=False)
    assert result is not None and len(result.clips) == 1
    assert list(notebook._last_dir.glob("*.mp4"))
    notebook.save("ZIP")  # Colab 以外では保存先を表示するだけ
    assert str(notebook._last_dir.resolve()) in capsys.readouterr().out


def test_empty_url(capsys):
    assert notebook.clip("  ") is None
    assert "URL" in capsys.readouterr().out


def test_error_hints():
    assert "ランタイム" in notebook._hint_for("Sign in to confirm you're not a bot")
    assert "非公開" in notebook._hint_for("This video is private")
    assert notebook._hint_for("something else") is None
