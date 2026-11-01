"""浏览器音频桥的单元测试（不需要容器，用假服务端）。"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from utils.audio_handle import browser_bridge
from utils.audio_handle.browser_bridge import BrowserAudioBridge


class FakeStage:
    """最小可用的假舞台服务，记录收到的命令。"""

    def __init__(self):
        self.commands = []
        self.volumes = {}
        self.channel_state = {}
        self.lock = threading.Lock()

    def handle_sync(self, payload):
        with self.lock:
            self.commands.extend(payload.get("commands") or [])
            self.volumes.update(payload.get("volumes") or {})
        return {
            "channels": self.channel_state,
            "client": {"connected": True, "lastReport": time.time()},
        }


@pytest.fixture()
def fake_stage():
    stage = FakeStage()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            data = stage.handle_sync(body)
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield stage, f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_play_sends_command(fake_stage, tmp_path):
    stage, url = fake_stage
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000WAVE")

    bridge = BrowserAudioBridge(url)
    bridge.play(0, str(audio), volume=0.5, kind="comment")

    assert stage.commands, "没有发出 play 命令"
    command = stage.commands[-1]
    assert command["op"] == "play"
    assert command["channel"] == 0
    assert command["volume"] == 0.5
    assert command["kind"] == "comment"


def test_play_missing_file_raises(fake_stage):
    _stage, url = fake_stage
    bridge = BrowserAudioBridge(url)
    with pytest.raises(FileNotFoundError):
        bridge.play(0, "/definitely/not/here.wav")


def test_volume_is_coalesced(fake_stage, tmp_path):
    """ducking 的斜坡会在几百毫秒内调十几次音量，必须合并成一次请求。"""
    stage, url = fake_stage
    audio = tmp_path / "b.wav"
    audio.write_bytes(b"RIFF")

    bridge = BrowserAudioBridge(url)
    bridge.play(0, str(audio))
    stage.commands.clear()

    for value in (0.9, 0.8, 0.7, 0.6, 0.5):
        bridge.set_volume(0, value)

    # 手动触发一次同步
    bridge._flush()

    assert stage.volumes.get("0") == 0.5, f"音量没有合并到最后一次: {stage.volumes}"


def test_is_busy_false_when_server_reports_ended(fake_stage, tmp_path):
    stage, url = fake_stage
    audio = tmp_path / "c.wav"
    audio.write_bytes(b"RIFF")

    stage.channel_state = {"0": {"state": "playing"}}
    bridge = BrowserAudioBridge(url)
    bridge.play(0, str(audio))
    assert bridge.is_busy(0) is True

    stage.channel_state = {"0": {"state": "ended"}}
    bridge._flush()
    assert bridge.is_busy(0) is False


def test_is_busy_times_out_when_no_client(fake_stage, tmp_path):
    """浏览器一直没连上时，不能让播放循环无限等待。"""
    stage, url = fake_stage
    stage.channel_state = {"0": {"state": "starting"}}

    audio = tmp_path / "d.wav"
    audio.write_bytes(b"RIFF")

    bridge = BrowserAudioBridge(url)
    bridge._start_timeout = 0.2
    bridge.play(0, str(audio))
    time.sleep(0.35)

    assert bridge.is_busy(0) is False
    assert bridge.snapshot()["channels"]["0"]["state"] == "no_client"


def test_survives_dead_server(tmp_path):
    """舞台服务挂掉时不能抛异常，只能返回「没在播」。"""
    audio = tmp_path / "e.wav"
    audio.write_bytes(b"RIFF")

    # 用一个几乎肯定没在监听的端口
    bridge = BrowserAudioBridge("http://127.0.0.1:9")
    bridge._start_timeout = 0.1
    bridge.play(0, str(audio))  # 不应抛
    time.sleep(0.3)
    assert bridge.is_busy(0) is False


def test_singleton_returns_same_instance():
    first = BrowserAudioBridge.instance("http://127.0.0.1:5500")
    second = BrowserAudioBridge.instance()
    assert first is second
