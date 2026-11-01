"""抖音事件映射的单元测试（不联网、不依赖容器）。"""
import json

import pytest

from integrations.douyin.adapter import events as ev


def test_normalize_chat_fills_defaults():
    event = ev.normalize({"type": "chat", "nickname": "小虾", "content": "你好"})
    assert event["source"] == "douyin"
    assert event["type"] == "chat"
    assert event["nickname"] == "小虾"
    assert event["content"] == "你好"
    assert event["event_id"]  # 自动生成
    assert event["timestamp"] > 0


def test_normalize_rejects_unknown_type():
    with pytest.raises(ValueError):
        ev.normalize({"type": "unknown", "nickname": "小虾"})


def test_normalize_rejects_empty_nickname():
    with pytest.raises(ValueError):
        ev.normalize({"type": "chat", "nickname": "   "})


def test_normalize_accepts_user_name_alias():
    event = ev.normalize({"type": "chat", "user_name": "别名用户", "content": "hi"})
    assert event["nickname"] == "别名用户"


@pytest.mark.parametrize(
    "event,expected_type",
    [
        ({"type": "chat", "nickname": "a", "content": "hi"}, 1),
        ({"type": "like", "nickname": "a", "count": 5}, 2),
        ({"type": "enter", "nickname": "a"}, 3),
        ({"type": "follow", "nickname": "a"}, 4),
        ({"type": "gift", "nickname": "a", "gift_name": "玫瑰"}, 5),
        ({"type": "stats", "nickname": "a", "online_count": 10, "total_count": 99}, 6),
    ],
)
def test_to_wss_frame_type_mapping(event, expected_type):
    frame = ev.to_wss_frame(event)
    assert frame["Type"] == expected_type
    assert isinstance(frame["Data"], str)
    # Data 必须是能解析的 JSON 字符串（原协议就是这么传的）
    json.loads(frame["Data"])


def test_to_wss_frame_chat_shape_matches_original_protocol():
    """原 WssBarrageServer 的弹幕帧结构必须完全一致，否则 dy.py 解析不到。"""
    frame = ev.to_wss_frame({"type": "chat", "nickname": "小虾", "content": "你好"})
    data = json.loads(frame["Data"])
    assert data["User"]["Nickname"] == "小虾"
    assert data["Content"] == "你好"


def test_to_wss_frame_gift_shape():
    frame = ev.to_wss_frame({
        "type": "gift", "nickname": "小虾", "gift_name": "玫瑰",
        "gift_count": 3, "repeat_count": 3,
    })
    data = json.loads(frame["Data"])
    assert data["GiftName"] == "玫瑰"
    assert data["GiftCount"] == 3
    assert data["RepeatCount"] == 3


def test_roundtrip_preserves_type_and_nickname():
    for event_type in ("chat", "like", "enter", "follow", "gift"):
        payload = {"type": event_type, "nickname": "往返用户"}
        if event_type == "chat":
            payload["content"] = "内容"
        if event_type == "gift":
            payload["gift_name"] = "礼物"
        frame = ev.to_wss_frame(payload)
        back = ev.from_wss_frame(frame)
        assert back["type"] == event_type
        assert back["nickname"] == "往返用户"


def test_to_main_program_maps_to_original_api():
    chat = ev.to_main_program({"type": "chat", "nickname": "a", "content": "hi"})
    assert chat["type"] == "comment"
    assert chat["data"]["username"] == "a"
    assert chat["data"]["content"] == "hi"

    entrance = ev.to_main_program({"type": "enter", "nickname": "a"})
    assert entrance["type"] == "entrance"

    follow = ev.to_main_program({"type": "follow", "nickname": "a"})
    assert follow["type"] == "follow"

    gift = ev.to_main_program({"type": "gift", "nickname": "a", "gift_name": "玫瑰"})
    assert gift["type"] == "gift"
    assert gift["data"]["gift_name"] == "玫瑰"


def test_like_and_stats_do_not_enter_main_program():
    """点赞和统计不该触发语音，避免刷屏。"""
    assert ev.to_main_program({"type": "like", "nickname": "a", "count": 1}) is None
    assert ev.to_main_program({
        "type": "stats", "nickname": "a", "online_count": 1, "total_count": 2,
    }) is None


def test_from_wss_frame_handles_dict_data():
    """Data 也可能是已经解析好的 dict（有些采集器这么发）。"""
    event = ev.from_wss_frame({"Type": 1, "Data": {"User": {"Nickname": "字典用户"}, "Content": "hi"}})
    assert event["nickname"] == "字典用户"
    assert event["content"] == "hi"


def test_from_wss_frame_rejects_bad_frame():
    with pytest.raises(ValueError):
        ev.from_wss_frame({"no_type": 1})
    with pytest.raises(ValueError):
        ev.from_wss_frame({"Type": 999, "Data": "{}"})


def test_event_id_is_preserved_for_dedupe():
    event = ev.normalize({"type": "chat", "nickname": "a", "content": "x", "event_id": "fixed-1"})
    assert event["event_id"] == "fixed-1"
