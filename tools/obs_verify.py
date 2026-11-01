"""通过 obs-websocket 5.x 验证 OBS 实际状态。

验证内容（全部来自 OBS 自己的上报，不是推测）：
  1. 场景集合是否加载
  2. 浏览器源是否存在、URL 是否正确
  3. 浏览器源音频轨道是否启用、是否静音、音量
  4. **实际音频电平**（GetInputVolumeMeters）——这是「OBS 真的在采集声音」的硬证据
  5. 视频分辨率与帧率
  6. 虚拟摄像头状态

用法：
    python3 tools/obs_verify.py
    python3 tools/obs_verify.py --watch 30
"""
import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
import urllib.request

try:
    import websocket  # websocket-client，项目已依赖
except ImportError:
    print("需要 websocket-client：pip install websocket-client")
    sys.exit(2)

HUB = "http://127.0.0.1:8888"

results = []


def record(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))


def load_password():
    path = os.path.expanduser(
        "~/Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
    )
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get("server_password") or ""
    except Exception:
        return ""


class Obs:
    def __init__(self, url="ws://127.0.0.1:4455"):
        self.ws = websocket.create_connection(url, timeout=15)
        hello = json.loads(self.ws.recv())
        self.version = hello["d"].get("obsWebSocketVersion")
        payload = {"op": 1, "d": {"rpcVersion": 1}}
        auth = hello["d"].get("authentication")
        if auth:
            secret = base64.b64encode(
                hashlib.sha256((load_password() + auth["salt"]).encode()).digest()
            ).decode()
            payload["d"]["authentication"] = base64.b64encode(
                hashlib.sha256((secret + auth["challenge"]).encode()).digest()
            ).decode()
        self.ws.send(json.dumps(payload))
        identified = json.loads(self.ws.recv())
        self.identified = identified.get("op") == 2
        self._counter = 0

        if self.identified:
            # 必须显式订阅 InputVolumeMeters。
            # obs-websocket 5.x 的 EventSubscription::All = 0x7FF **不包含**
            # 高频事件（InputVolumeMeters = 1<<16、InputActiveStateChanged = 1<<17、
            # SceneItemTransformChanged = 1<<18），这是刻意的性能设计。
            # 不订阅就一条电平都收不到，看起来像「OBS 没声音」。
            # 协议里 Reidentify 是 op=3（op=5 是 Event，发错会被直接断开连接）。
            self.ws.send(json.dumps({
                "op": 3,
                "d": {"eventSubscriptions": 0x7FF | (1 << 16)},
            }))
            # Reidentify 的 ack 是 op=2 Identified，可能和事件混在一起，短超时 drain
            self.ws.settimeout(1.0)
            try:
                while True:
                    message = json.loads(self.ws.recv())
                    if message.get("op") == 2:
                        break
            except Exception:
                pass
            finally:
                self.ws.settimeout(15)

    def request(self, request_type, data=None):
        self._counter += 1
        request_id = "r%d" % self._counter
        payload = {"op": 6, "d": {"requestType": request_type, "requestId": request_id}}
        if data:
            payload["d"]["requestData"] = data
        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except Exception:
                continue
            # 订阅事件后流里会混入 op=5 的事件帧，直接跳过
            if message.get("op") != 7:
                continue
            if message["d"].get("requestId") != request_id:
                continue
            if not message["d"]["requestStatus"]["result"]:
                raise RuntimeError(message["d"]["requestStatus"])
            return message["d"].get("responseData") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def inject(text):
    raw = json.dumps(
        {"type": "chat", "nickname": "OBS电平测试", "content": text}, ensure_ascii=False
    ).encode()
    request = urllib.request.Request(
        HUB + "/inject", data=raw, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def meter_peak(obs, source_name, seconds=3.0):
    """读 OBS 的音频电平。

    注意：obs-websocket 5.x 里音量表**不是** request/response 接口，
    而是 InputVolumeMeters 事件（op=5）持续推送，每秒约 50 帧。
    用 request 去问会得到 code 204 'request type is not valid'。
    """
    peak = -100.0
    deadline = time.time() + seconds
    obs.ws.settimeout(0.5)
    try:
        while time.time() < deadline:
            try:
                raw = obs.ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if message.get("op") != 5:
                continue
            if message["d"].get("eventType") != "InputVolumeMeters":
                continue
            for entry in message["d"].get("eventData", {}).get("inputs") or []:
                if entry.get("inputName") != source_name:
                    continue
                for channel in entry.get("inputLevelsMul") or []:
                    for value in channel or []:
                        if value is None:
                            continue
                        try:
                            peak = max(peak, 20 * math.log10(max(1e-6, float(value))))
                        except Exception:
                            pass
    finally:
        obs.ws.settimeout(15)
    return peak


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=float, default=12, help="观察电平的秒数")
    args = parser.parse_args()

    try:
        obs = Obs()
    except Exception as error:
        print("连不上 OBS WebSocket：%s" % error)
        print("请确认 OBS 已启动；工具 -> WebSocket 服务器设置 -> 启用")
        return 2

    print("OBS WebSocket %s，已认证=%s" % (obs.version, obs.identified))
    if not obs.identified:
        print("认证失败，检查 obs-websocket 密码")
        return 2

    scenes = obs.request("GetSceneList")
    names = [s["sceneName"] for s in scenes.get("scenes") or []]
    current = scenes.get("currentProgramSceneName")
    record("OBS 场景已加载", bool(names), "场景=%s 当前=%s" % (names, current))
    record("当前节目场景正确", current == "大肥鱼", "current=%s" % current)

    items = obs.request("GetSceneItemList", {"sceneName": current}).get("sceneItems") or []
    pairs = [(i.get("sourceName"), i.get("inputKind") or "") for i in items]
    browser = [n for n, k in pairs if "browser" in k.lower()]
    record("场景里有浏览器源", bool(browser), "来源=%s" % pairs)

    if browser:
        name = browser[0]
        settings = obs.request("GetInputSettings", {"inputName": name}).get("inputSettings") or {}
        url = settings.get("url", "")
        record("浏览器源 URL 指向舞台页", "12345" in url and "stage-" in url, "url=%s" % url)
        record(
            "浏览器源尺寸 1920x1080",
            int(settings.get("width") or 0) == 1920 and int(settings.get("height") or 0) == 1080,
            "%sx%s" % (settings.get("width"), settings.get("height")),
        )

        tracks = obs.request("GetInputAudioTracks", {"inputName": name}).get("inputAudioTracks") or {}
        record("浏览器源音频轨道已启用", any(tracks.values()), "tracks=%s" % tracks)

        muted = obs.request("GetInputMute", {"inputName": name}).get("inputMuted")
        record("浏览器源未静音", not muted, "muted=%s" % muted)

        volume = obs.request("GetInputVolume", {"inputName": name}).get("inputVolumeMul")
        record("浏览器源音量 > 0", float(volume or 0) > 0, "volumeMul=%s" % volume)

        # 实际电平：先注入一条，保证有声音
        inject("这是一条用于验证 OBS 音频电平的测试弹幕")
        # 注入几条弹幕，保证观察窗口里一直有声音
        for index in range(3):
            inject("这是第 %d 条用于验证 OBS 音频电平的测试弹幕，说长一点让电平持续" % (index + 1))
            time.sleep(0.5)
        peak = meter_peak(obs, name, seconds=args.watch)
        record("OBS 实际采到音频电平（> -60dB）", peak > -60, "峰值 %.1f dB" % peak)

    video = obs.request("GetVideoSettings")
    record(
        "视频分辨率 1920x1080",
        video.get("baseWidth") == 1920 and video.get("baseHeight") == 1080,
        "%sx%s 输出 %sx%s"
        % (
            video.get("baseWidth"),
            video.get("baseHeight"),
            video.get("outputWidth"),
            video.get("outputHeight"),
        ),
    )
    fps_num, fps_den = video.get("fpsNumerator") or 0, video.get("fpsDenominator") or 1
    fps = fps_num / fps_den if fps_den else 0
    record("帧率 >= 30", fps >= 30, "%.0f FPS" % fps)

    try:
        vcam = obs.request("GetVirtualCamStatus")
        record("虚拟摄像头状态可查", "outputActive" in vcam, "active=%s" % vcam.get("outputActive"))
    except Exception as error:
        record("虚拟摄像头状态可查", False, str(error)[:80])

    obs.close()

    passed = sum(1 for r in results if r["ok"])
    print("\n%d/%d 项通过" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
