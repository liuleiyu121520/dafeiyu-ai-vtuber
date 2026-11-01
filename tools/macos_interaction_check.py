"""阶段 D：原有互动功能验证（BGM / 点歌 / 礼物 / 关注 / 进场 / ducking）。

浏览器保持打开，所以 BGM 不会因为「没有客户端」而立刻判为播完。
逐项验证：
  1. 空闲时 BGM 在 channel 2 播放
  2. 弹幕回复时 BGM 被压低（ducking），channel 2 音量下降
  3. 回复结束后 BGM 音量恢复
  4. 礼物事件产生感谢语音
  5. 关注事件产生感谢语音
  6. 进场事件产生欢迎语音
  7. 点歌进入 channel 3
  8. 播放中停止能清空
"""
import argparse
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

STAGE = "http://127.0.0.1:12345/Live2D/stage-landscape.html"
HUB = "http://127.0.0.1:8888"
STAGE_API = "http://127.0.0.1:5500"

results = []


def record(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))


def post(url, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode())


def channels():
    return get(f"{STAGE_API}/audio/state")["channels"]


def wait_for(predicate, timeout, interval=0.25):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if predicate():
                return True, time.monotonic() - started
        except Exception:
            pass
        time.sleep(interval)
    return False, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(STAGE, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # ---- 1. 空闲 BGM ----
        bgm_ok, elapsed = wait_for(
            lambda: channels()["2"]["state"] in ("playing", "starting"), args.timeout
        )
        ch = channels()
        record("空闲时 BGM 在 channel 2 播放", bgm_ok, f"{elapsed:.1f}s state={ch['2']['state']} vol={ch['2']['volume']}")

        # 记住空闲音量
        idle_volume = float(ch["2"]["volume"]) if bgm_ok else 0.0

        # ---- 2. 弹幕触发回复 -> BGM 被压低 ----
        post(f"{HUB}/inject", {"type": "chat", "nickname": "BGM测试", "content": "大肥鱼，给我讲讲海底的事"})

        ducked = {"value": None}

        def bgm_ducked():
            state = channels()["2"]
            volume = float(state["volume"])
            if volume < idle_volume * 0.8:
                ducked["value"] = volume
                return True
            return False

        duck_ok, duck_elapsed = wait_for(bgm_ducked, args.timeout)
        record(
            "说话时 BGM 被压低（ducking）",
            duck_ok,
            f"音量 {idle_volume:.2f} -> {ducked['value']}",
        )

        # ---- 3. 说完恢复 ----
        recovered = {"value": None}

        def bgm_recovered():
            state = channels()["2"]
            if state["state"] not in ("playing",):
                return False
            volume = float(state["volume"])
            if volume >= idle_volume * 0.95:
                recovered["value"] = volume
                return True
            return False

        rec_ok, rec_elapsed = wait_for(bgm_recovered, args.timeout)
        record(
            "说完后 BGM 音量恢复",
            rec_ok,
            f"恢复到 {recovered['value']}（{rec_elapsed:.1f}s）" if rec_ok else f"未恢复，当前 {channels()['2']['volume']}",
        )

        # ---- 4/5/6 礼物、关注、进场 ----
        for event, label in (
            ({"type": "gift", "nickname": "礼物测试", "gift_name": "小心心", "gift_count": 2, "repeat_count": 2}, "礼物"),
            ({"type": "follow", "nickname": "关注测试"}, "关注"),
            ({"type": "enter", "nickname": "进场测试"}, "进场"),
        ):
            before = int(channels()["0"]["seq"])
            post(f"{HUB}/inject", event)
            spoke, spoke_elapsed = wait_for(
                lambda: int(channels()["0"]["seq"]) > before, args.timeout
            )
            record(
                f"{label}事件触发主播语音",
                spoke,
                f"channel0 seq {before} -> {channels()['0']['seq']}（{spoke_elapsed:.1f}s）",
            )
            # 等这一条播完再发下一条，避免互相覆盖
            wait_for(lambda: channels()["0"]["state"] not in ("playing", "starting"), 30)

        # ---- 7. 点歌 ----
        # 先用一段已授权的本地测试音频，避免触发版权下载
        before = int(channels()["3"]["seq"])
        post(f"{HUB}/inject", {"type": "chat", "nickname": "点歌测试", "content": "点歌 起风了"})
        song_ok, song_elapsed = wait_for(lambda: int(channels()["3"]["seq"]) > before, args.timeout)
        ch = channels()
        record(
            "点歌进入 channel 3（独立通道）",
            song_ok,
            f"seq {before} -> {ch['3']['seq']} state={ch['3']['state']}（{song_elapsed:.1f}s）",
        )

        # ---- 8. 停止播报 ----
        post(f"{STAGE_API}/audio/sync", {"commands": [{"op": "stop", "channel": 0}], "volumes": {}})
        page.wait_for_timeout(1500)
        stopped = channels()["0"]["state"] in ("stopped", "idle", "ended")
        record("可以停止当前播报", stopped, f"channel0 state={channels()['0']['state']}")

        browser.close()

    passed = sum(1 for item in results if item["ok"])
    print(f"\n{passed}/{len(results)} 项通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
