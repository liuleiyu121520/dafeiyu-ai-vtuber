"""播放流工厂与浏览器播放实现的单元测试。"""
import threading
import time

import pytest

from utils.audio_handle import playback_stream
from utils.audio_handle.playback_stream import (
    BasePlaybackStream,
    BrowserPlaybackStream,
    PlaybackStream,
    make_playback_stream,
)


class FakeConfig:
    """只实现 get(*keys) 的最小配置对象，和 utils.config.Config 同接口。"""

    def __init__(self, data):
        self.data = data

    def get(self, *keys):
        node = self.data
        for key in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node


def test_factory_returns_pygame_for_pygame_config():
    stream = make_playback_stream(0, FakeConfig({"play_audio": {"player": "pygame"}}))
    assert isinstance(stream, PlaybackStream)


@pytest.mark.parametrize("player", ["browser", "browser_bridge"])
def test_factory_returns_browser_for_browser_config(player):
    stream = make_playback_stream(0, FakeConfig({"play_audio": {"player": player}}))
    assert isinstance(stream, BrowserPlaybackStream)


def test_factory_defaults_to_pygame_for_unknown_player():
    """audio_player / audio_player_v2 由 Audio 的旧分支处理，工厂只管这两种。"""
    stream = make_playback_stream(0, FakeConfig({"play_audio": {"player": "audio_player"}}))
    assert isinstance(stream, PlaybackStream)


def test_both_implementations_share_the_interface():
    for cls in (PlaybackStream, BrowserPlaybackStream):
        for name in (
            "init", "quit", "load", "play", "stop", "pause", "unpause",
            "get_busy", "fadeout", "set_volume", "now_ms", "tick",
        ):
            assert hasattr(cls, name), f"{cls.__name__} 缺少 {name}"
        assert issubclass(cls, BasePlaybackStream)


def test_now_ms_is_monotonic():
    first = BasePlaybackStream.now_ms()
    time.sleep(0.01)
    second = BasePlaybackStream.now_ms()
    assert second > first
    assert isinstance(first, int)


def test_tick_sleeps_at_least_one_frame():
    stream = BrowserPlaybackStream(0, "http://127.0.0.1:9")
    started = time.monotonic()
    stream.tick(20)  # 50ms
    elapsed = time.monotonic() - started
    assert elapsed >= 0.04


def test_music_facade_exposes_pygame_style_api():
    stream = BrowserPlaybackStream(0, "http://127.0.0.1:9")
    for name in ("load", "play", "stop", "pause", "unpause", "get_busy", "fadeout", "set_volume"):
        assert hasattr(stream.music, name)


def test_browser_stream_get_busy_false_without_play():
    stream = BrowserPlaybackStream(0, "http://127.0.0.1:9")
    assert stream.get_busy() is False


def test_browser_stream_set_volume_clamps():
    stream = BrowserPlaybackStream(0, "http://127.0.0.1:9")
    stream.set_volume(5.0)
    assert stream._volume == 1.0
    stream.set_volume(-2.0)
    assert stream._volume == 0.0


def test_channel_kind_mapping():
    expected = {0: "comment", 1: "copywriting", 2: "bgm", 3: "song"}
    for channel, kind in expected.items():
        stream = BrowserPlaybackStream(channel, "http://127.0.0.1:9")
        assert stream._kind() == kind
