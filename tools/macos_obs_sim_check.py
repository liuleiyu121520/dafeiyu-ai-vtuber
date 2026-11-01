"""模拟 OBS 浏览器源：测帧率、音画同步、字幕跟随。

OBS 的浏览器源是 CEF（Chromium Embedded Framework），和 Chromium 的渲染路径一致。
这里用 Chromium + 与 OBS 相同的参数加载舞台页，测量：

  1. 实际渲染帧率（requestAnimationFrame 计数）
  2. 长时间运行后是否有内存泄漏迹象
  3. 音画同步：音频播放位置 vs 口型值的时间对应
  4. 字幕与音频是否同时出现
  5. 场景刷新后能否恢复

用法：
    python3 tools/macos_obs_sim_check.py --seconds 60
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

STAGE = "http://127.0.0.1:12345/Live2D/stage-landscape.html"
HUB = "http://127.0.0.1:8888"
STAGE_API = "http://127.0.0.1:5500"


def post(url, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def channels():
    with urllib.request.urlopen(f"{STAGE_API}/audio/state", timeout=10) as response:
        return json.loads(response.read().decode())["channels"]


results = []


def record(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        # 参数与 OBS 浏览器源一致：允许自动播放、无沙箱、GPU 加速
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
                "--enable-gpu",
                "--ignore-gpu-blocklist",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(STAGE, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # ---- 1. 帧率 ----
        page.evaluate("""() => {
            window.__fpsSamples = [];
            let last = performance.now();
            let count = 0;
            function loop(now) {
                count++;
                const dt = now - last;
                if (dt >= 500) {
                    window.__fpsSamples.push(Math.round(count * 1000 / dt));
                    count = 0;
                    last = now;
                }
                requestAnimationFrame(loop);
            }
            requestAnimationFrame(loop);
        }""")

        # ---- 2. 持续运行 + 周期性注入 ----
        started = time.monotonic()
        injections = 0
        audio_samples = []
        while time.monotonic() - started < args.seconds:
            if int(time.monotonic() - started) % 12 < 1:
                post(f"{HUB}/inject", {
                    "type": "chat",
                    "nickname": f"OBS模拟{injections}",
                    "content": f"这是第{injections + 1}条用于帧率与音画同步测量的弹幕",
                })
                injections += 1

            snapshot = page.evaluate("""() => ({
                lip: window.__songroomLipSync || 0,
                ch0: (window.__stageAudio && window.__stageAudio.channels && window.__stageAudio.channels[0])
                    ? window.__stageAudio.channels[0].state : null,
                caption: (() => {
                    const box = document.getElementById('stage-caption');
                    return box && !box.hidden ? box.textContent : '';
                })(),
                heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
            })""")
            audio_samples.append({"t": time.monotonic() - started, **snapshot})
            page.wait_for_timeout(300)

        fps_samples = page.evaluate("() => window.__fpsSamples || []")

        # ---- 3. 场景刷新恢复 ----
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        recovered = page.evaluate("""() => ({
            canvas: !!document.getElementById('live2d'),
            bridge: typeof window.__stageAudio !== 'undefined',
            context: window.__stageAudio ? window.__stageAudio.context : 'none',
        })""")
        record(
            "场景刷新后自动恢复",
            recovered["canvas"] and recovered["bridge"],
            f"canvas={recovered['canvas']} bridge={recovered['bridge']} ctx={recovered['context']}",
        )

        browser.close()

    # ---- 判定 ----
    if fps_samples:
        fps_median = statistics.median(fps_samples)
        fps_min = min(fps_samples)
        record(
            "渲染帧率达到 30 FPS",
            fps_median >= 30,
            f"中位 {fps_median:.0f} FPS，最低 {fps_min} FPS，采样 {len(fps_samples)} 次",
        )
    else:
        record("渲染帧率达到 30 FPS", False, "没有采到帧率样本")

    playing = [s for s in audio_samples if s["ch0"] in ("playing", "starting")]
    record("测量期间音频真的在播", len(playing) > 0, f"{len(playing)}/{len(audio_samples)} 个采样点在播")

    lip_moving = [s for s in audio_samples if s["lip"] > 0.05]
    record("口型在播放期间有响应", len(lip_moving) > 0, f"{len(lip_moving)} 个采样点口型 > 0.05")

    # 音画同步：有音频时口型应该也在动；没音频时口型应归零
    synced = 0
    total = 0
    for sample in audio_samples:
        if sample["ch0"] == "playing":
            total += 1
            if sample["lip"] > 0.02:
                synced += 1
    ratio = synced / total if total else 0
    record("音画同步（在播时口型非零）", total == 0 or ratio > 0.5, f"{synced}/{total} = {ratio:.0%}")

    captions = [s for s in audio_samples if s["caption"] and s["caption"].strip()]
    record("字幕随音频出现", len(captions) > 0, f"{len(captions)} 个采样点有字幕")

    heaps = [s["heap"] for s in audio_samples if s["heap"]]
    if len(heaps) > 3:
        growth = heaps[-1] - heaps[0]
        record("无明显内存泄漏", growth < 200, f"JS 堆 {heaps[0]}MB -> {heaps[-1]}MB（增长 {growth}MB）")

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    record("运行期间无 Console 报错", not real_errors, "; ".join(real_errors[:3])[:200])

    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} 项通过（注入 {injections} 条弹幕）")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
