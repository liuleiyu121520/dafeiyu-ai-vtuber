"""浏览器音频桥：把本机声卡播放换成「浏览器 Web Audio 播放」。

背景
----
项目里所有本地音频出口都是 `PlaybackStream`（pygame `mixer.Channel`）：
弹幕回复、文案、点歌、BGM 四路全部走它。Docker Desktop 的 Linux 容器
**没有声卡**（`/dev/snd` 不暴露），pygame 播不出任何声音。

做法
----
不去改播放逻辑，而是提供**同接口**的 `BrowserPlaybackStream`：

    load / play / stop / pause / unpause / get_busy / fadeout / set_volume
    + now_ms() / tick(ms)   ← 把 pygame 的时间函数也一起抽象掉

音频字节由舞台服务（`tools/caption_printer.py`，:5500）通过 HTTP 提供给
浏览器；OBS 浏览器源用 Web Audio 解码播放，并用 AnalyserNode 取真实振幅
驱动 Live2D 口型。因此：

  * 口型跟随的是**真正在响的那段 PCM**，不是随机数
  * 字幕和声音在同一台机器、同一份时钟上，不会错位
  * 不需要 BlackHole，也不需要往容器里映射声卡

协议（舞台服务 :5500）
----------------------
    POST /audio/sync     {"commands":[...], "volumes":{...}} -> {"channels":{...}, "client":{...}}
    POST /audio/report   {"channel":0, "state":"playing", "positionMs":1234, ...}
    GET  /audio/file/<id>                                     -> 音频字节
    GET  /audio/state                                         -> 只读快照

通道语义与 pygame 版完全一致：
    0 = 弹幕回复   1 = 文案   2 = BGM   3 = 点歌
"""
import json
import os
import threading
import time
import traceback
from urllib.parse import urljoin

import requests

from utils.my_log import logger

# 通道语义（和 pygame 版保持一致）
CHANNEL_NORMAL = 0
CHANNEL_COPYWRITING = 1
CHANNEL_BGM = 2
CHANNEL_SONG = 3

# 浏览器没连上来时，最多等这么久才认定「这段音频没播」
DEFAULT_START_TIMEOUT = 8.0

# 同步线程的节奏：有活在干就快一点，闲着就慢一点
SYNC_INTERVAL_BUSY = 0.05
SYNC_INTERVAL_IDLE = 0.25


class BrowserBridgeError(RuntimeError):
    """舞台服务不可用。调用方按「播放失败」处理，不影响主链路。"""


class BrowserAudioBridge:
    """与舞台服务通信的客户端（进程内单例）。

    维护一份「各通道状态」的本地缓存，`get_busy()` 直接读缓存，
    避免播放等待循环里每次都发 HTTP。
    """

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls, base_url=None):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(base_url or os.environ.get("DAFEIYU_STAGE_URL", "http://127.0.0.1:5500"))
            elif base_url and cls._instance.base_url != base_url:
                cls._instance.base_url = base_url
            return cls._instance

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/") + "/"
        self._lock = threading.RLock()
        # channel -> {"busy": bool, "state": str, "path": str, "since": float, "error": str}
        self._channels = {}
        # 待发送的命令（play/stop/pause/unpause/fadeout）
        self._pending = []
        # 待发送的音量（同通道多次设置只保留最后一次）
        self._volumes = {}
        # 浏览器上报
        # report_age: 距离浏览器最后一次回报过去了多少秒（0 表示还没回报过）。
        # 用「年龄」而不是时间点，避免 epoch 与 monotonic 混用（踩过这个坑）。
        self._client = {"connected": False, "report_age": 0.0, "channel": None, "state": None}
        self._running = False
        self._thread = None
        self._last_error = ""
        self._start_timeout = DEFAULT_START_TIMEOUT
        # 浏览器多久没有回报就认为「这段已经不在播了」。
        # 正常播放时浏览器每 120ms 回报一次，30 秒是极宽松的阈值，
        # 只用来兜住「OBS 切后台导致 CEF 暂停」这种真实故障。
        self._stall_timeout = float(os.environ.get("DAFEIYU_BRIDGE_STALL_TIMEOUT", "30"))

    # ---------------- 生命周期 ----------------

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._sync_loop, daemon=True, name="audio-bridge-sync")
        self._thread.start()
        logger.info(f"[音频桥] 已启动，舞台服务 {self.base_url}")

    def stop(self):
        with self._lock:
            self._running = False
        logger.info("[音频桥] 已停止")

    # ---------------- 对外接口 ----------------

    def play(self, channel, path, volume=1.0, fade_ms=0, kind=""):
        """让浏览器开始播一段音频。

        Args:
            channel: 通道号（0/1/2/3）
            path: 音频文件路径（容器内路径）
            volume: 初始音量 0~1
            fade_ms: 淡入毫秒
            kind: 业务类型（comment/song/bgm/...），只用于日志与前端提示
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        with self._lock:
            # 先本地置忙，避免播放循环在浏览器还没接上时把 get_busy() 读成 False
            self._channels[channel] = {
                "busy": True,
                "state": "starting",
                "path": path,
                "since": time.monotonic(),
                "error": "",
            }
            self._pending.append({
                "op": "play",
                "channel": channel,
                "path": path,
                "volume": float(volume),
                "fadeMs": int(fade_ms),
                "kind": kind,
            })

        # 立即推一次，别等下一个 tick —— 首声延迟每一毫秒都算
        self._flush()
        logger.debug(f"[音频桥] channel={channel} play {os.path.basename(path)}")

    def stop(self, channel):
        with self._lock:
            state = self._channels.get(channel) or {}
            state.update(busy=False, state="stopped")
            self._channels[channel] = state
            self._pending.append({"op": "stop", "channel": channel})
        self._flush()

    def pause(self, channel):
        with self._lock:
            state = self._channels.get(channel) or {}
            if state.get("busy"):
                state["state"] = "paused"
                self._channels[channel] = state
            self._pending.append({"op": "pause", "channel": channel})
        self._flush()

    def unpause(self, channel):
        with self._lock:
            state = self._channels.get(channel) or {}
            if state.get("busy"):
                state["state"] = "playing"
                self._channels[channel] = state
            self._pending.append({"op": "resume", "channel": channel})
        self._flush()

    def set_volume(self, channel, value):
        """音量合并发送：ducking 的斜坡会在几百毫秒内调十几次，不能每次都发请求。"""
        with self._lock:
            self._volumes[channel] = max(0.0, min(1.0, float(value)))

    def fadeout(self, channel, ms):
        with self._lock:
            state = self._channels.get(channel) or {}
            state.update(busy=False, state="fading")
            self._channels[channel] = state
            self._pending.append({"op": "fadeout", "channel": channel, "ms": int(ms)})
        self._flush()

    def is_busy(self, channel):
        with self._lock:
            state = self._channels.get(channel)
            if not state:
                return False
            if not state.get("busy"):
                return False

            # 浏览器一直没接上：超时后不要再让调用方干等
            if state.get("state") == "starting":
                if time.monotonic() - state.get("since", 0) > self._start_timeout:
                    state.update(busy=False, state="no_client")
                    state["error"] = "浏览器未连接（请在 OBS 里打开舞台页）"
                    self._channels[channel] = state
                    return False

            # 浏览器「在播」但长时间没有任何回报：
            # OBS 被切到后台 / 舞台页被隐藏时，CEF 会暂停定时器与 Web Audio 回调，
            # 状态就永远停在 playing。不兜住的话，播放循环（BGM、弹幕回复）会全部卡死。
            # 用「最后回报时间」判断，而不是靠固定的最大时长——正常播放的回报是持续的。
            report_age = self._client.get("report_age") or 0
            if report_age > self._stall_timeout:
                state.update(busy=False, state="stalled")
                state["error"] = (

                    "浏览器 %d 秒没有回报播放状态（OBS 切到后台或被最小化？）"
                    % int(self._stall_timeout)
                )
                self._channels[channel] = state
                logger.warning(

                    f"[音频桥] channel={channel} 超过 {self._stall_timeout:.0f}s 没有播放回报，"
                    f"按已结束处理，避免阻塞播放队列"
                )
                return False

            return True

    def snapshot(self):
        """给状态接口/诊断脚本用。"""
        with self._lock:
            return {
                "base_url": self.base_url,
                "client": dict(self._client),
                "channels": {str(k): dict(v) for k, v in self._channels.items()},
                "last_error": self._last_error,
            }

    # ---------------- 内部 ----------------

    def _sync_loop(self):
        while True:
            with self._lock:
                if not self._running:
                    return
                busy = any(item.get("busy") for item in self._channels.values()) or self._pending
            self._flush()
            time.sleep(SYNC_INTERVAL_BUSY if busy else SYNC_INTERVAL_IDLE)

    def _flush(self):
        """把待发命令 + 音量一次性推给舞台服务，并回收状态。失败只记录不抛。"""
        with self._lock:
            if not self._pending and not self._volumes:
                payload = {"commands": [], "volumes": {}}
            else:
                payload = {"commands": self._pending, "volumes": dict(self._volumes)}
                self._pending = []
                self._volumes = {}

        try:
            response = requests.post(
                urljoin(self.base_url, "audio/sync"),
                json=payload,
                timeout=(0.6, 2.0),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as error:  # noqa: BLE001 - 桥断了不能影响直播主链路
            message = f"{type(error).__name__}: {error}"[:200]
            if message != self._last_error:
                logger.warning(f"[音频桥] 舞台服务不可用（{message}），音频无法播放")
                self._last_error = message
            with self._lock:
                for state in self._channels.values():
                    if state.get("busy") and state.get("state") == "starting":
                        if time.monotonic() - state.get("since", 0) > self._start_timeout:
                            state.update(busy=False, state="error", error=message)
            return

        self._last_error = ""
        self._apply_server_state(data)

    def _apply_server_state(self, data):
        channels = data.get("channels") or {}
        client = data.get("client") or {}
        with self._lock:
            # 服务端给的 lastReport 是 epoch 秒。这里换算成「距今多少秒」，
            # 存成 age 而不是转成 monotonic 时间点——两者基准不同，
            # 混用会算出负数（曾经踩过，导致卡死保护永远不触发）。
            remote_report = float(client.get("lastReport") or 0)
            age = max(0.0, time.time() - remote_report) if remote_report > 0 else 0.0
            self._client = {
                "connected": bool(client.get("connected")),
                "report_age": age,
                "channel": client.get("channel"),
                "state": client.get("state"),
            }
            for key, remote in channels.items():
                try:
                    channel = int(key)
                except (TypeError, ValueError):
                    continue
                local = self._channels.get(channel) or {}
                # 本地已经判定「播完/停止/卡死」的，不要被服务端的滞后状态拉回忙。
                # 服务端的 state 更新是滞后的（浏览器回报有往返），
                # 一旦本地判了 ended/stalled，就以本地为准。
                if not local.get("busy"):
                    if not local.get("state") or local.get("state") == "idle":
                        local["state"] = remote.get("state") or "idle"
                    self._channels[channel] = local
                    continue
                local["state"] = remote.get("state") or local.get("state")
                if remote.get("state") in ("ended", "stopped", "error"):
                    local["busy"] = False
                    local["error"] = remote.get("error") or ""
                self._channels[channel] = local


def bridge_enabled(config):
    """当前配置是否走浏览器音频桥。"""
    player = config.get("play_audio", "player")
    return player in ("browser", "browser_bridge")
