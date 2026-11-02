"""抖音采集器的离线单测：不联网，只验证 protobuf 解码与 Hub 事件契约。

关键契约是 decode_events() 产出的每个事件都能被
adapter/events.normalize() 接受，否则转发到 Hub 会被 400 拒掉。
"""
import gzip
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from integrations.douyin import collector  # noqa: E402
from integrations.douyin.adapter import events as ev  # noqa: E402
from protobuf.douyin import (  # noqa: E402
    ChatMessage,
    Common,
    ControlMessage,
    EmojiChatMessage,
    GiftMessage,
    GiftStruct,
    LikeMessage,
    MemberMessage,
    Message,
    PushFrame,
    Response,
    RoomUserSeqMessage,
    SocialMessage,
    User,
)


def frame(messages: list[Message], **kwargs) -> bytes:
    """构造一帧 Response 的 protobuf 字节。"""
    return bytes(Response(messages_list=messages, **kwargs))


def msg(method: str, payload_model) -> Message:
    return Message(method=method, payload=bytes(payload_model))


class DecodeEventsTest(unittest.TestCase):
    def test_chat_decodes(self):
        payload = frame([msg("WebcastChatMessage",
                             ChatMessage(user=User(id=7, nick_name="甲"),
                                         content="你好大肥鱼"))])
        out = collector.decode_events(payload)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "chat")
        self.assertEqual(out[0]["nickname"], "甲")
        self.assertEqual(out[0]["content"], "你好大肥鱼")

    def test_gift_uses_combo_count(self):
        payload = frame([msg("WebcastGiftMessage",
                             GiftMessage(user=User(nick_name="乙"),
                                         gift=GiftStruct(name="小心心"),
                                         combo_count=5, repeat_count=5))])
        out = collector.decode_events(payload)
        self.assertEqual(out[0]["gift_name"], "小心心")
        self.assertEqual(out[0]["gift_count"], 5)
        self.assertEqual(out[0]["repeat_count"], 5)

    def test_stats_gets_placeholder_nickname(self):
        """normalize() 要求 nickname 非空，统计消息没有用户，必须填占位值。"""
        payload = frame([msg("WebcastRoomUserSeqMessage",
                             RoomUserSeqMessage(total=50, total_user=22003))])
        out = collector.decode_events(payload)
        self.assertEqual(out[0]["type"], "stats")
        self.assertEqual(out[0]["online_count"], 50)
        self.assertEqual(out[0]["total_count"], 22003)
        # 这一行就是契约：不抛 ValueError 才算通过
        normalized = ev.normalize(out[0])
        self.assertEqual(normalized["nickname"], collector.STATS_NICKNAME)

    def test_every_event_type_satisfies_hub_contract(self):
        """所有会转发的事件类型都必须能被 normalize() 接受。"""
        payload = frame([
            msg("WebcastChatMessage", ChatMessage(user=User(nick_name="a"), content="c")),
            msg("WebcastMemberMessage", MemberMessage(user=User(nick_name="b"))),
            msg("WebcastSocialMessage", SocialMessage(user=User(nick_name="d"))),
            msg("WebcastLikeMessage", LikeMessage(user=User(nick_name="e"), count=3)),
            msg("WebcastGiftMessage", GiftMessage(user=User(nick_name="f"),
                                                  gift=GiftStruct(name="玫瑰"),
                                                  combo_count=2, repeat_count=2)),
            msg("WebcastRoomUserSeqMessage", RoomUserSeqMessage(total=1, total_user=2)),
            msg("WebcastEmojiChatMessage", EmojiChatMessage(user=User(nick_name="g"),
                                                            default_content="[表情]")),
        ])
        out = collector.decode_events(payload)
        self.assertEqual(len(out), 7)
        for event in out:
            normalized = ev.normalize(event)          # 抛异常即失败
            frame_dict = ev.to_wss_frame(normalized)
            self.assertIn(frame_dict["Type"], ev.EVENT_TO_TYPE.values())

    def test_to_wss_frame_matches_dy_py_field_paths(self):
        """dy.py 读的是 User.Nickname / Content / GiftName / GiftCount / RepeatCount。"""
        payload = frame([msg("WebcastGiftMessage",
                             GiftMessage(user=User(nick_name="送花人"),
                                         gift=GiftStruct(name="棒棒糖"),
                                         combo_count=3, repeat_count=3))])
        data = json.loads(ev.to_wss_frame(collector.decode_events(payload)[0])["Data"])
        self.assertEqual(data["User"]["Nickname"], "送花人")
        self.assertEqual(data["GiftName"], "棒棒糖")
        self.assertEqual(data["GiftCount"], 3)
        self.assertEqual(data["RepeatCount"], 3)

    def test_unknown_method_is_skipped_not_fatal(self):
        payload = frame([
            msg("WebcastWhateverNewMessage", ChatMessage(content="x")),
            msg("WebcastChatMessage", ChatMessage(user=User(nick_name="ok"), content="y")),
        ])
        out = collector.decode_events(payload)
        self.assertEqual([e["nickname"] for e in out], ["ok"])

    def test_missing_user_does_not_emit_empty_nickname(self):
        """没有 user 的消息不能产出 nickname='' ，否则 Hub 会 400。"""
        payload = frame([msg("WebcastChatMessage", ChatMessage(content="匿名"))])
        self.assertEqual(collector.decode_events(payload), [])


class RoomEndedTest(unittest.TestCase):
    def test_status_three_is_end(self):
        self.assertTrue(collector.room_ended(
            frame([msg("WebcastControlMessage", ControlMessage(status=3))])))

    def test_status_two_is_live(self):
        self.assertFalse(collector.room_ended(
            frame([msg("WebcastControlMessage", ControlMessage(status=2))])))


class AckFrameTest(unittest.TestCase):
    def test_ack_roundtrip(self):
        """need_ack 时必须能构造出服务端认的 ack 帧。"""
        original = PushFrame(log_id=999, payload_type="pb", payload=b"inner")
        parsed = PushFrame().parse(bytes(original))
        ack = PushFrame(log_id=parsed.log_id, payload_type="ack",
                        payload=b"ext-from-response")
        back = PushFrame().parse(bytes(ack))
        self.assertEqual(back.log_id, 999)
        self.assertEqual(back.payload_type, "ack")


class GzipPayloadTest(unittest.TestCase):
    def test_gzip_decompress_path(self):
        """payload_encoding=gzip 时先解压再解析（与 _run_once 里的处理一致）。"""
        inner = frame([msg("WebcastChatMessage",
                           ChatMessage(user=User(nick_name="压缩观众"), content="hi"))])
        package = PushFrame(payload_encoding="gzip", payload=gzip.compress(inner))
        parsed = PushFrame().parse(bytes(package))
        self.assertEqual(parsed.payload_encoding, "gzip")
        decoded = gzip.decompress(parsed.payload)
        self.assertEqual(collector.decode_events(decoded)[0]["nickname"], "压缩观众")


class WssUrlTest(unittest.TestCase):
    def test_room_id_substituted(self):
        url = collector.build_wss_url("123456789")
        self.assertIn("room_id=123456789", url)
        self.assertIn("wss_push_room_id:123456789", url)
        self.assertTrue(url.startswith("wss://"))

    def test_env_override_wins(self):
        old = os.environ.get("DY_WSS_URL")
        os.environ["DY_WSS_URL"] = "wss://custom.example/room/{room_id}"
        try:
            self.assertEqual(collector.build_wss_url("42"),
                             "wss://custom.example/room/42")
        finally:
            if old is None:
                del os.environ["DY_WSS_URL"]
            else:
                os.environ["DY_WSS_URL"] = old


class ForwarderTest(unittest.TestCase):
    def test_post_builds_inject_request(self):
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"code": 200, "clients": 1,
                                   "event_id": "x"}).encode()

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["ctype"] = req.get_header("Content-type")
            return FakeResp()

        original = collector.urllib.request.urlopen
        collector.urllib.request.urlopen = fake_urlopen
        try:
            f = collector.HubForwarder("http://127.0.0.1:8888/")
            ok = f.post({"type": "chat", "nickname": "n", "content": "c"})
        finally:
            collector.urllib.request.urlopen = original

        self.assertTrue(ok)
        self.assertEqual(f.sent, 1)
        self.assertEqual(captured["url"], "http://127.0.0.1:8888/inject")
        self.assertEqual(captured["ctype"], "application/json")
        self.assertEqual(captured["body"]["nickname"], "n")

    def test_network_failure_counts_and_swallows(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        original = collector.urllib.request.urlopen
        collector.urllib.request.urlopen = boom
        try:
            f = collector.HubForwarder("http://127.0.0.1:9")
            self.assertFalse(f.post({"type": "chat", "nickname": "n"}))
        finally:
            collector.urllib.request.urlopen = original
        self.assertEqual(f.failed, 1)
        self.assertEqual(f.sent, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
