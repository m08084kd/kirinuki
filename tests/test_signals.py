import json

from kirinuki import signals


def _line(ms, item):
    return json.dumps({"replayChatItemAction": {
        "videoOffsetTimeMsec": str(ms),
        "actions": [{"addChatItemAction": {"item": item}}],
    }})


def test_parse_live_chat(tmp_path):
    path = tmp_path / "chat.live_chat.json"
    path.write_text("\n".join([
        _line(10_000, {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": "こんにちは"}]}}}),
        _line(12_500, {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": "wwwww"}]}}}),
        _line(13_000, {"liveChatPaidMessageRenderer": {"message": {"runs": [{"text": "ナイス!!"}]}}}),
        _line(-500, {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": "待機"}]}}}),
        "not json",
    ]), encoding="utf-8")
    comments = signals.parse_live_chat(path)
    assert [c[1] for c in comments] == ["こんにちは", "wwwww", "ナイス!!"]
    assert comments[0][2] == 1.0
    assert comments[1][2] > 1.0
    assert comments[2][2] > comments[1][2]


def test_chat_activity_applies_delay():
    series = signals.chat_activity([(20.0, "草", 1.5), (20.5, "a", 1.0)], 30, delay=5)
    assert series[15] == 2.5
    assert series.sum() == 2.5


def test_comment_weight():
    assert signals.comment_weight("こんにちは") == 1.0
    assert signals.comment_weight("草") > 1.0
    assert signals.comment_weight("ｗｗｗ神!!") >= 2.5


def test_heatmap_series():
    heat = [{"start_time": 0, "end_time": 5.5, "value": 0.2},
            {"start_time": 5.5, "end_time": 10, "value": 1.0}]
    series = signals.heatmap_series(heat, 10)
    assert series[0] == 0.2
    assert series[9] == 1.0
