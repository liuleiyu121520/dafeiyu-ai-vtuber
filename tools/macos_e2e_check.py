"""端到端验证：真实 Chromium 打开舞台页，播放音频，读真实口型与字幕。

这是「阶段 C」的核心验收脚本。它不做任何模拟：
  * 浏览器真的解码并播放音频（Web Audio）
  * 口型来自 AnalyserNode 对真实 PCM 的分析
  * 字幕来自 5500 的字幕服务
  * 通过 Chrome 的自动播放豁免让音频真的能响

用法（在宿主机）：
    python3 tools/macos_e2e_check.py --url http://127.0.0.1:12345/Live2D/stage-landscape.html
    python3 tools/macos_e2e_check.py --inject "你好，大肥鱼"
    python3 tools/macos_e2e_check.py --scenario burst5

输出：逐项 PASS/FAIL + 关键时间戳，退出码 0 表示全通。
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("需要 playwright：pip install playwright && playwright install chromium")
    sys.exit(2)

STAGE_URL_DEFAULT = "http://127.0.0.1:12345/Live2D/stage-landscape.html"
CAPTION_BASE = "http://127.0.0.1:5500"
HUB_BASE = "http://127.0.0.1:8888"


def post_json(url, payload, timeout=10):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def inject(event):
    return post_json(f"{HUB_BASE}/inject", event)


def analyze_png(png_bytes):
    """粗分析截图：判断画面上有没有「角色」。

    思路：海底场景是蓝绿色系，角色皮肤是暖色（R 明显大于 B）。
    统计暖色像素数量，超过阈值就认为角色真的画出来了。
    不依赖任何图像库——PNG 先解码成 RGB。
    """
    try:
        from PIL import Image
        import io

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return {"ok": False, "width": 0, "height": 0, "nonBg": 0, "skin": 0, "reason": "decode failed"}

    width, height = image.size
    pixels = image.load()
    # 只看中间 2/3 区域（角色在这里），步进采样够快
    x0, x1 = width // 6, width * 5 // 6
    y0, y1 = height // 6, height * 5 // 6
    skin = 0
    non_bg = 0
    total = 0
    for y in range(y0, y1, 7):
        for x in range(x0, x1, 7):
            r, g, b = pixels[x, y]
            total += 1
            # 暖色皮肤：红显著高于蓝，且整体够亮
            if r > 120 and r - b > 18 and g > 80:
                skin += 1
            # 与典型海底背景（偏蓝绿）差异大的像素
            if abs(r - 40) + abs(g - 90) + abs(b - 130) > 160:
                non_bg += 1
    return {
        "ok": skin > 60,
        "width": width,
        "height": height,
        "nonBg": non_bg,
        "skin": skin,
        "total": total,
    }


def wait_for(predicate, timeout, interval=0.2, label=""):
    """轮询直到 predicate 为真。返回 (ok, elapsed)。"""
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
    parser.add_argument("--url", default=STAGE_URL_DEFAULT)
    parser.add_argument("--inject", default=None, help="要注入的一条弹幕内容")
    parser.add_argument("--scenario", default=None, help="single/burst5/gift/follow/enter/mix")
    parser.add_argument("--nickname", default="E2E测试观众")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--screenshot", default=None)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    results = []

    def record(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))
        return ok

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed,
            args=[
                # 让 Web Audio 在没有用户手势时也能 start
                "--autoplay-policy=no-user-gesture-required",
                # 容器/无头环境没有音频设备，用假设备保证 AudioContext 能 running
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--mute-audio" if False else "--no-sandbox",
            ],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(f"pageerror: {err}"))

        print(f"打开舞台页：{args.url}")
        page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

        # ---- 1. 模型是否真的加载 ----
        model_state = page.evaluate("""() => {
            const canvas = document.getElementById('live2d');
            return {
                canvas: !!canvas,
                width: canvas ? canvas.width : 0,
                height: canvas ? canvas.height : 0,
                modelName: window.model_name || null,
                core: typeof window.Live2DCubismCore !== 'undefined',
            };
        }""")
        record(
            "Live2D canvas 存在",
            model_state["canvas"] and model_state["width"] > 0,
            f"{model_state['width']}x{model_state['height']} model={model_state['modelName']}",
        )

        # 模型真的画出来了才算加载成功。
        # 不能用 gl.readPixels：WebGL 默认 preserveDrawingBuffer=false，
        # 帧提交后缓冲区被清空，无论模型画得多好都只能读到全 0（假失败）。
        # 改用 Playwright 截图后分析像素——这正是 OBS 看到的画面。
        page.wait_for_timeout(600)
        shot = page.screenshot(type="png")
        drawn = analyze_png(shot)
        record(
            "模型已渲染（截图中存在角色像素）",
            drawn["ok"],
            f"size={drawn['width']}x{drawn['height']} 非背景像素={drawn['nonBg']} 肤色像素={drawn['skin']}",
        )

        # ---- 2. 音频桥就绪 ----
        audio_ready = page.evaluate("""() => ({
            bridge: typeof window.__stageAudio !== 'undefined',
            ready: window.__stageAudioReady === true,
            context: (window.__stageAudio && window.__stageAudio.context) || 'none',
        })""")
        record(
            "浏览器音频桥已加载",
            audio_ready["bridge"],
            f"context={audio_ready['context']} ready={audio_ready['ready']}",
        )

        # 音频上下文必须真的 running，否则一点声音都不会有
        ctx_ok, ctx_elapsed = wait_for(
            lambda: page.evaluate("window.__stageAudio && window.__stageAudio.context === 'running'"),
            timeout=15,
        )
        record("AudioContext 处于 running（能真正发声）", ctx_ok, f"{ctx_elapsed:.2f}s")

        # ---- 3. 注入弹幕，等音频真的开始播 ----
        if args.scenario:
            plan = {
                "single": [{"type": "chat", "content": "你好，大肥鱼"}],
                "burst5": [{"type": "chat", "content": f"第{i+1}条测试弹幕"} for i in range(5)],
                "gift": [{"type": "gift", "gift_name": "小心心", "gift_count": 1, "repeat_count": 1}],
                "follow": [{"type": "follow"}],
                "enter": [{"type": "enter"}],
                "mix": [
                    {"type": "enter"},
                    {"type": "chat", "content": "你好，大肥鱼"},
                    {"type": "gift", "gift_name": "玫瑰", "gift_count": 2, "repeat_count": 2},
                ],
            }[args.scenario]
            for index, event in enumerate(plan):
                event.setdefault("nickname", f"{args.nickname}{index}")
                inject(event)
                print(f"  注入 {event['type']} #{index + 1}")
                page.wait_for_timeout(800)
        elif args.inject:
            inject({"type": "chat", "nickname": args.nickname, "content": args.inject})
            print(f"  注入 chat：{args.inject}")

        # 等 channel 0 真的进入 playing。
        # 记录「曾经进入过 playing」而不是「此刻是 playing」——
        # 短音频可能在两个轮询之间就播完了，只看当前状态会漏判。
        play_seen = {"playing": False, "seq": 0}

        def channel_playing():
            info = page.evaluate("""() => {
                const s = window.__stageAudio;
                if (!s || !s.channels) return null;
                const c = s.channels[0];
                return c ? {state: c.state, seq: c.seq} : null;
            }""")
            if not info:
                return False
            if info.get("state") == "playing" and info.get("seq", 0) > 0:
                play_seen["playing"] = True
                play_seen["seq"] = info["seq"]
                return True
            return play_seen["playing"]

        played, play_elapsed = wait_for(channel_playing, timeout=args.timeout, interval=0.08)
        record("弹幕音频在浏览器里真的开始播放", played, f"{play_elapsed:.2f}s seq={play_seen['seq']}")

        # ---- 4. 口型跟随真实振幅 ----
        lip_samples = []
        if played:
            # 音频只有 1~2 秒，60ms 采一次能拿到几十个点，足够判断有没有起伏
            for _ in range(50):
                lip = page.evaluate("() => window.__songroomLipSync || 0")
                lip_samples.append(float(lip))
                page.wait_for_timeout(50)

        lip_max = max(lip_samples) if lip_samples else 0.0
        lip_min = min(lip_samples) if lip_samples else 0.0
        record(
            "口型随音频变化（max 明显大于 0 且有起伏）",
            lip_max > 0.05 and (lip_max - lip_min) > 0.03,
            f"max={lip_max:.3f} min={lip_min:.3f} samples={len(lip_samples)}",
        )

        # 口型是参数驱动的，不是 CSS 动画：读模型参数
        mouth_param = page.evaluate("""() => {
            try {
                return window.__songroomMouthApplied || null;
            } catch (e) { return null; }
        }""")
        record(
            "口型写入模型参数 ParamMouthOpenY",
            mouth_param is not None and "index" in (mouth_param or {}),
            json.dumps(mouth_param, ensure_ascii=False)[:120] if mouth_param else "no param info",
        )

        # ---- 5. 字幕 ----
        caption_text = page.evaluate("""() => {
            const box = document.getElementById('stage-caption');
            return box ? {hidden: box.hidden, text: box.textContent} : null;
        }""")
        record(
            "字幕已显示（stage-caption 有文本）",
            caption_text is not None and bool(caption_text.get("text", "").strip()),
            (caption_text or {}).get("text", "")[:40],
        )

        # ---- 6. 播放结束后口型归零 ----
        def not_playing():
            state = page.evaluate("""() => {
                const s = window.__stageAudio;
                return s && s.channels && s.channels[0] ? s.channels[0].state : null;
            }""")
            return state not in ("playing",)

        ended, end_elapsed = wait_for(not_playing, timeout=args.timeout)
        page.wait_for_timeout(800)
        lip_after = float(page.evaluate("() => window.__songroomLipSync || 0"))
        record("播放结束", ended, f"{end_elapsed:.2f}s")
        record("播放结束后口型归零", lip_after < 0.05, f"lip={lip_after:.3f}")

        # ---- 7. 无 console 报错 ----
        real_errors = [e for e in console_errors if "favicon" not in e.lower() and "404" not in e]
        record("浏览器 Console 无报错", not real_errors, "; ".join(real_errors[:3])[:200])

        if args.screenshot:
            page.screenshot(path=args.screenshot)
            print(f"截图：{args.screenshot}")

        browser.close()

    passed = sum(1 for item in results if item["ok"])
    total = len(results)
    print(f"\n{passed}/{total} 项通过")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"results": results, "passed": passed, "total": total}, handle, ensure_ascii=False, indent=2)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
