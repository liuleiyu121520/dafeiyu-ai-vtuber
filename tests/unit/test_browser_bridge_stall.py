"""浏览器音频桥的「卡死」保护测试。

背景
----
浏览器（OBS 的 CEF）在切到后台/被最小化时会暂停定时器与 Web Audio 回调，
通道状态就永远停在 playing。如果 is_busy() 只认这个状态，
播放队列（BGM、弹幕回复）会全部永久卡住。

做法：用「最后一次回报距今多久」判断。正常播放时浏览器每 120ms 回报一次，
超过阈值就认为这一段已经不在播了。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from utils.audio_handle.browser_bridge import BrowserAudioBridge


@pytest.fixture()
def stalled_stage():
    """模拟「浏览器在播但不再回报」：channels 永远 playing，lastReport 可控。"""
    state = {"report_time": time.time()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({
                "channels": {"0": {"state": "playing"}},
                "client": {"connected": True, "lastReport": state["report_time"]},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state, "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def test_stalled_browser_releases_the_queue(stalled_stage, tmp_path):
    """浏览器长时间没回报时必须判定播完，否则 BGM/播放队列会永久卡住。"""
    state, url = stalled_stage
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    bridge = BrowserAudioBridge(url)
    bridge._stall_timeout = 0.5

    bridge.play(0, str(audio))
    bridge._flush()
    assert bridge.is_busy(0) is True, "刚 play 完应该是忙的"

    # 把「最后回报时间」推到很久以前，模拟 CEF 被暂停
    state["report_time"] = time.time() - 100
    bridge._flush()

    # 先确认 age 真的被读进来了（这是上一次真正出 bug 的地方：
    # 曾经把 epoch 和 monotonic 混用，算出负数，导致保护永远不触发）
    assert bridge.snapshot()["client"]["report_age"] > 50, "回报年龄没有被正确换算"

    assert bridge.is_busy(0) is False, "卡死时必须返回不在播，否则队列卡住"
    assert bridge.snapshot()["channels"]["0"]["state"] == "stalled"


def test_fresh_reports_keep_channel_busy(stalled_stage, tmp_path):
    """持续有回报时不能误判成卡死。"""
    state, url = stalled_stage
    audio = tmp_path / "b.wav"
    audio.write_bytes(b"RIFF")

    bridge = BrowserAudioBridge(url)
    bridge._stall_timeout = 5.0

    bridge.play(0, str(audio))
    for _ in range(3):
        state["report_time"] = time.time()  # 每次都是新鲜的
        bridge._flush()
        assert bridge.is_busy(0) is True
        time.sleep(0.05)
