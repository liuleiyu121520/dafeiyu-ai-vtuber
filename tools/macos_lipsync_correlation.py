"""口型同步的定量证据：口型参数 vs 画面嘴部开合 的相关性分析。

为什么要做相关性
----------------
单帧对比会被「角色自然摆头」干扰：两帧整体位置差几个像素，任何像素差都会很大，
所以「像素差 57%」这种数字证明不了嘴在动，只能说明画面变了。

做法
----
1. 播放期间高频采样，每帧同时记录：
     - window.__songroomLipSync（音频振幅驱动值）
     - 模型参数 ParamMouthOpenY 的实际值
     - 画面截图
2. 在每帧截图里**先定位脸部**（肤色包围盒），再按脸部尺寸归一化地裁出嘴部，
   这样摆头不会影响测量。
3. 量嘴部「口腔像素」的高度（张嘴时口腔区域变高）。
4. 计算 lip 值与嘴部开合高度的皮尔逊相关系数。

判定：
  r > 0.5  强正相关 -> 嘴型确实跟随音频
  r > 0.25 中等相关 -> 有跟随但不稳定
  否则     -> 未证明

用法：
    python3 tools/macos_lipsync_correlation.py --frames 40 --out /tmp/lipcorr
"""
import argparse
import io
import json
import os
import statistics
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


def face_box(image):
    """找脸部：肤色像素的包围盒。返回 (x0, y0, x1, y1) 或 None。"""
    width, height = image.size
    pixels = image.load()
    min_x, min_y, max_x, max_y = width, height, 0, 0
    count = 0
    # 只在画面中上部找（角色上半身）
    for y in range(height // 8, height * 5 // 8, 4):
        for x in range(width // 4, width * 3 // 4, 4):
            r, g, b = pixels[x, y]
            # 皮肤：亮且暖，且不像衣服的深蓝
            if r > 200 and g > 175 and b > 160 and r >= b and (r - b) < 60:
                count += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if count < 30:
        return None
    return (min_x, min_y, max_x, max_y)


def mouth_openness(image):
    """量嘴部开合：以脸部包围盒为参照，归一化地取嘴部区域，统计口腔像素的竖直跨度。

    Returns:
        float: 口腔像素的竖直跨度 / 脸高。0 表示量不到。
    """
    box = face_box(image)
    if box is None:
        return 0.0, None
    x0, y0, x1, y1 = box
    face_w = x1 - x0
    face_h = y1 - y0
    if face_w < 20 or face_h < 20:
        return 0.0, box

    pixels = image.load()
    width, height = image.size

    # 嘴在脸的下半部分：脸高的 55%~95%，水平居中 35%~65%
    mx0 = int(x0 + face_w * 0.35)
    mx1 = int(x0 + face_w * 0.65)
    my0 = int(y0 + face_h * 0.55)
    my1 = int(y0 + face_h * 0.98)

    rows = set()
    count = 0
    for y in range(max(0, my0), min(height, my1)):
        for x in range(max(0, mx0), min(width, mx1)):
            r, g, b = pixels[x, y]
            # 口腔/唇：红明显占优、亮度中等（不是高光皮肤，也不是深色衣服）
            if r > 110 and (r - g) > 45 and (r - b) > 40 and g < 170:
                rows.add(y)
                count += 1

    if not rows:
        return 0.0, box
    span = (max(rows) - min(rows) + 1) / max(1, face_h)
    return span, box


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--out", default="/tmp/lipcorr")
    parser.add_argument("--text", default="一二三四五六七八九十，大肥鱼正在说话，嘴型应该跟着声音动")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    from PIL import Image

    samples = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(STAGE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        inject({"type": "chat", "nickname": "口型相关性测试", "content": args.text})

        for _ in range(800):
            state = page.evaluate(
                "() => (window.__stageAudio && window.__stageAudio.channels && window.__stageAudio.channels[0])"
                " ? window.__stageAudio.channels[0].state : null"
            )
            if state == "playing":
                break
            page.wait_for_timeout(80)

        started = time.monotonic()
        while len(samples) < args.frames and time.monotonic() - started < 40:
            data = page.evaluate("""() => ({
                lip: window.__songroomLipSync || 0,
                applied: window.__songroomMouthApplied || null,
                channel: (window.__stageAudio && window.__stageAudio.channels && window.__stageAudio.channels[0])
                    ? window.__stageAudio.channels[0].state : null,
            })""")
            if data["channel"] not in ("playing",):
                break
            applied = data["applied"] or {}
            entry = {
                "lip": float(data["lip"]),
                "param": float(applied.get("actual") or 0),
                "openness": 0.0,
                "faceBox": None,
                "png": None,
                "t": time.monotonic() - started,
            }
            # 先只记录数值，截图放到最后一次性做——
            # 每帧截图要 100ms+，边采边截会让短音频来不及采几帧。
            samples.append(entry)
            page.wait_for_timeout(30)

        # 播放结束后，再对采到的数值序列补齐画面测量：
        # 重新注入一条更长的文本，边播边以较低频率截图（保证帧数够）。
        if len([s for s in samples if s["png"] is not None]) < 5:
            samples = []
            long_text = args.text * 3
            inject({"type": "chat", "nickname": "口型相关性测试2", "content": long_text})
            for _ in range(800):
                state = page.evaluate(
                    "() => (window.__stageAudio && window.__stageAudio.channels && window.__stageAudio.channels[0])"
                    " ? window.__stageAudio.channels[0].state : null"
                )
                if state == "playing":
                    break
                page.wait_for_timeout(80)
            started = time.monotonic()
            while len(samples) < args.frames and time.monotonic() - started < 40:
                data = page.evaluate("""() => ({
                    lip: window.__songroomLipSync || 0,
                    applied: window.__songroomMouthApplied || null,
                    channel: (window.__stageAudio && window.__stageAudio.channels && window.__stageAudio.channels[0])
                        ? window.__stageAudio.channels[0].state : null,
                })""")
                if data["channel"] not in ("playing",):
                    break
                png = page.screenshot(type="png")
                applied = data["applied"] or {}
                samples.append({
                    "lip": float(data["lip"]),
                    "param": float(applied.get("actual") or 0),
                    "openness": 0.0,
                    "faceBox": None,
                    "png": png,
                    "t": time.monotonic() - started,
                })
                page.wait_for_timeout(20)

        browser.close()

    # 统一做画面测量
    for entry in samples:
        if entry.get("png"):
            image = Image.open(io.BytesIO(entry["png"])).convert("RGB")
            openness, box = mouth_openness(image)
            entry["openness"] = openness
            entry["faceBox"] = box
        entry.pop("png", None)

    samples = [s for s in samples if s["openness"] > 0]

    if len(samples) < 5:
        print(f"采样不足（{len(samples)} 帧），无法分析")
        return 1

    lips = [s["lip"] for s in samples]
    params = [s["param"] for s in samples]
    opens = [s["openness"] for s in samples]

    r_lip_open = pearson(lips, opens)
    r_param_open = pearson(params, opens)

    print(f"采样帧数        {len(samples)}")
    print(f"口型值 lip       min={min(lips):.3f} max={max(lips):.3f} 标准差={statistics.pstdev(lips):.3f}")
    print(f"模型参数         min={min(params):.3f} max={max(params):.3f}")
    print(f"嘴部开合度       min={min(opens):.4f} max={max(opens):.4f} 标准差={statistics.pstdev(opens):.4f}")
    print()
    print(f"相关系数 r(lip, 嘴部开合)      = {r_lip_open:+.3f}")
    print(f"相关系数 r(模型参数, 嘴部开合) = {r_param_open:+.3f}")
    print()

    with open(os.path.join(args.out, "samples.json"), "w", encoding="utf-8") as handle:
        json.dump(samples, handle, ensure_ascii=False, indent=2)

    best = max(range(len(samples)), key=lambda i: lips[i])
    worst = min(range(len(samples)), key=lambda i: lips[i])
    for label, index in (("open", best), ("closed", worst)):
        print(f"{label:6s} lip={lips[index]:.3f} 参数={params[index]:.3f} 开合度={opens[index]:.4f}")

    if r_lip_open > 0.5:
        print("\n结论：口型值与画面嘴部开合强正相关 —— 嘴型确实跟随音频")
        return 0
    if r_lip_open > 0.25:
        print("\n结论：口型值与画面嘴部开合中等相关 —— 有跟随但采样噪声较大")
        return 0
    print("\n结论：未观测到明显相关性，需要排查")
    return 1


if __name__ == "__main__":
    sys.exit(main())
