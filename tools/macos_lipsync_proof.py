"""口型同步的视觉证据：在播放过程中按口型值截取「张嘴 / 闭嘴」两帧。

不是随机截图：每一帧都记录了当时的 window.__songroomLipSync 和模型参数
ParamMouthOpenY 的实际值，可以直接对照画面验证嘴巴张合。

用法：
    python3 tools/macos_lipsync_proof.py --out /tmp/lipsync
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

STAGE_URL = "http://127.0.0.1:12345/Live2D/stage-landscape.html"
HUB = "http://127.0.0.1:8888"


def inject(event):
    raw = json.dumps(event, ensure_ascii=False).encode()
    request = urllib.request.Request(f"{HUB}/inject", data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def mouth_region(png_bytes):
    """把角色嘴部区域裁出来，返回 (crop_bytes, 宽度, 高度)。

    角色在画面中间，嘴大约在角色高度的 22%~30% 处。
    按横屏 1920x1080 布局：#model-surface 在 left=510,top=-5,w=900,h=1040，
    嘴大约在 y≈300~400、x≈870~1050 这一带。
    """
    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    crop = image.crop((820, 240, 1100, 440))
    # 放大 2 倍看得清楚
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue(), crop.width, crop.height


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/lipsync")
    parser.add_argument("--text", default="一二三四五六七八九十，大肥鱼现在正在说话")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    frames = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(STAGE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        inject({"type": "chat", "nickname": "口型测试", "content": args.text})

        # 等音频开始
        for _ in range(600):
            state = page.evaluate("""() => {
                const s = window.__stageAudio;
                return s && s.channels && s.channels[0] ? s.channels[0].state : null;
            }""")
            if state == "playing":
                break
            page.wait_for_timeout(100)

        # 播放期间密集采样，保留口型最高和最低的两帧
        best = None
        worst = None
        started = time.monotonic()
        while time.monotonic() - started < 12:
            state = page.evaluate("""() => {
                const s = window.__stageAudio;
                return {
                    lip: window.__songroomLipSync || 0,
                    applied: window.__songroomMouthApplied || null,
                    channel: s && s.channels && s.channels[0] ? s.channels[0].state : null,
                };
            }""")
            if state["channel"] != "playing":
                break
            lip = float(state["lip"])
            if best is None or lip > best["lip"]:
                png = page.screenshot(type="png")
                best = {"lip": lip, "applied": state["applied"], "png": png}
            # 闭嘴帧取「整段里口型最小」的那一帧，而不是死等 lip<0.02：
            # 语音是连续的，两个音节之间未必会掉到 0，等 0 会一帧都采不到。
            if worst is None or lip < worst["lip"]:
                png = page.screenshot(type="png")
                worst = {"lip": lip, "applied": state["applied"], "png": png}
            page.wait_for_timeout(40)

        # 播完之后再采一帧真正静止的：此时 lip 必然归零，是最干净的对照
        idle = None
        for _ in range(120):
            state = page.evaluate("""() => ({
                lip: window.__songroomLipSync || 0,
                applied: window.__songroomMouthApplied || null,
                channel: (window.__stageAudio && window.__stageAudio.channels && window.__stageAudio.channels[0])
                    ? window.__stageAudio.channels[0].state : null,
            })""")
            if state["channel"] not in ("playing", "starting"):
                page.wait_for_timeout(700)  # 等口型平滑回落到 0
                state = page.evaluate("""() => ({
                    lip: window.__songroomLipSync || 0,
                    applied: window.__songroomMouthApplied || null,
                })""")
                png = page.screenshot(type="png")
                idle = {"lip": float(state["lip"]), "applied": state["applied"], "png": png}
                break
            page.wait_for_timeout(60)

        browser.close()

    if not best:
        print("没有采到播放中的画面")
        return 1

    # 闭嘴帧优先用「播放结束后的静止帧」，那才是真正的 lip=0；
    # 拿不到时退回播放期间口型最小的那一帧。
    closed_frame = idle if idle is not None else (worst or best)
    for label, frame in (("mouth_open", best), ("mouth_closed", closed_frame)):
        crop, width, height = mouth_region(frame["png"])
        path = os.path.join(args.out, f"{label}.png")
        with open(path, "wb") as handle:
            handle.write(crop)
        full_path = os.path.join(args.out, f"{label}_full.png")
        with open(full_path, "wb") as handle:
            handle.write(frame["png"])
        applied = frame["applied"] or {}
        print(
            f"{label}: lip={frame['lip']:.3f} "
            f"ParamMouthOpenY={applied.get('actual', 'n/a')} "
            f"-> {path} (crop {width}x{height})"
        )
        frames.append({
            "label": label,
            "lip": frame["lip"],
            "paramMouthOpenY": applied.get("actual"),
            "crop": path,
            "full": full_path,
        })

    with open(os.path.join(args.out, "frames.json"), "w", encoding="utf-8") as handle:
        json.dump(frames, handle, ensure_ascii=False, indent=2)

    delta = abs(frames[0]["lip"] - frames[1]["lip"])
    print(f"\n口型参数差值 = {delta:.3f}（>0.05 说明参数确实在变）")

    # 再补一个与参数无关的证据：直接量「嘴张开的高度」。
    # 角色会自然摆头，两帧整体位置会差几个像素，直接算全图差值不准。
    # 改成在脸部区域里找「口腔像素」（偏暗的红/棕），量它的竖直跨度：
    # 张嘴时口腔像素多且竖跨大，闭嘴时几乎只剩一条唇线。
    def mouth_metrics(png_path):
        from PIL import Image

        image = Image.open(png_path).convert("RGB")
        width, height = image.size
        pixels = image.load()
        # 嘴巴所在的大致区域（在放大 2 倍的 560x400 裁图里）
        x0, x1, y0, y1 = 150, 410, 30, 150
        rows = {}
        total = 0
        for y in range(y0, min(y1, height)):
            for x in range(x0, min(x1, width)):
                r, g, b = pixels[x, y]
                # 口腔/嘴唇：红色占优且不够亮（皮肤是 r,g,b 都高且接近）
                if r > 90 and r - g > 35 and r - b > 30 and g < 150:
                    rows[y] = rows.get(y, 0) + 1
                    total += 1
        if not rows:
            return {"pixels": 0, "spanY": 0, "maxRow": 0}
        return {
            "pixels": total,
            "spanY": max(rows) - min(rows) + 1,
            "maxRow": max(rows.values()),
        }

    metrics_open = mouth_metrics(frames[0]["crop"])
    metrics_closed = mouth_metrics(frames[1]["crop"])
    print(f"张嘴帧口腔像素 = {metrics_open['pixels']}，竖直跨度 = {metrics_open['spanY']}px")
    print(f"闭嘴帧口腔像素 = {metrics_closed['pixels']}，竖直跨度 = {metrics_closed['spanY']}px")

    pixel_grew = metrics_open["pixels"] > metrics_closed["pixels"] * 1.15
    span_grew = metrics_open["spanY"] > metrics_closed["spanY"]
    param_moved = delta > 0.05

    print()
    if param_moved and (pixel_grew or span_grew):
        print("结论：口型参数与画面同时变化 —— 嘴型确实跟随音频")
    elif param_moved:
        print("结论：口型参数在变，但画面像素差异不明显（可能采样帧相位接近）")
    else:
        print("结论：口型参数没有变化，需要检查音频桥与 lipsync 链路")

    try:
        from PIL import Image, ImageChops

        open_image = Image.open(frames[0]["crop"]).convert("RGB")
        closed_image = Image.open(frames[1]["crop"]).convert("RGB")
        ImageChops.difference(open_image, closed_image).save(os.path.join(args.out, "mouth_diff.png"))
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
