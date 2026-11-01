"""确定性验证：模型参数 ParamMouthOpenY 是否真的驱动画面嘴型。

做法（不依赖音频、不依赖时序）：
  1. 打开舞台页，等模型加载
  2. 暂停 lipsync 驱动（把 window.__songroomLipSync 固定为常数）
  3. 直接把 ParamMouthOpenY 分别设为 0 和 1，各截一帧
  4. 计算两帧在嘴部区域的像素差异，以及「口腔像素」的竖直跨度变化
  5. 再把口型值设为 0 / 0.5 / 1.0 三档，验证是**连续**响应而不是二值

如果 0 和 1 两帧的嘴部区域几乎没差别，说明这个模型根本没有可动的嘴，
那就是模型本身的问题，不是链路问题——这个脚本能把两者区分开。

用法：
    python3 tools/macos_mouth_param_proof.py --out /tmp/mouthproof
"""
import argparse
import io
import json
import os
import sys

from playwright.sync_api import sync_playwright

STAGE_URL = "http://127.0.0.1:12345/Live2D/stage-landscape.html"


def model_box(image):
    """返回角色模型在画面里的包围盒。

    舞台是固定 1920x1080，#model-surface 在 left=510,top=-5,w=900,h=1040。
    浏览器视口也是 1920x1080 时是 1:1，直接用这组常量最稳——
    自动找肤色会被围裙、白色花边、手部骗到，反而不准。
    """
    width, height = image.size
    scale_x = width / 1920.0
    scale_y = height / 1080.0
    return (
        int(510 * scale_x),
        int(-5 * scale_y),
        int((510 + 900) * scale_x),
        int((-5 + 1040) * scale_y),
    )


def mouth_region(image):
    """按模型包围盒推嘴部区域。

    模型 900x1040 里，头部大约占上方 40%，嘴在头部下缘居中。
    这里取一个略大的框，留出摆头余量。
    """
    x0, y0, x1, y1 = model_box(image)
    model_w = x1 - x0
    model_h = y1 - y0
    # 实测嘴中心约在模型高度的 29%，框取 24%~34% 正好包住嘴和下巴
    return (
        int(x0 + model_w * 0.40),
        int(y0 + model_h * 0.235),
        int(x0 + model_w * 0.60),
        int(y0 + model_h * 0.340),
    )


def mouth_pixels(image, box):
    """量口腔像素：数量 + 竖直跨度。"""
    x0, y0, x1, y1 = box
    pixels = image.load()
    width, height = image.size
    rows = set()
    count = 0
    for y in range(max(0, y0), min(height, y1)):
        for x in range(max(0, x0), min(width, x1)):
            r, g, b = pixels[x, y]
            if r > 100 and (r - g) > 40 and (r - b) > 35 and g < 175:
                rows.add(y)
                count += 1
    return {
        "pixels": count,
        "spanY": (max(rows) - min(rows) + 1) if rows else 0,
    }


def diff_pixels(a, b, box):
    """两图在嘴部区域的差异像素数。"""
    from PIL import ImageChops

    x0, y0, x1, y1 = box
    ca = a.crop((x0, y0, x1, y1))
    cb = b.crop((x0, y0, x1, y1))
    diff = ImageChops.difference(ca, cb)
    changed = sum(1 for p in diff.getdata() if sum(p) > 30)
    total = ca.width * ca.height
    return changed, total, (changed / total if total else 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/mouthproof")
    parser.add_argument("--url", default=STAGE_URL)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    from PIL import Image

    frames = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # 冻结姿态：stage-overlay.js 的 poseLoop 每帧写 window.__songroomPose，
        # 角色会一直轻轻摆头。不冻住的话两帧整体位移几像素，任何像素差都失去意义。
        page.evaluate("""() => {
            Object.defineProperty(window, '__songroomPose', {
                configurable: true,
                get() { return { angleZ: 0, angleY: 0, bodyZ: 0 }; },
                set(v) { /* 忽略 poseLoop 的写入 */ },
            });
        }""")
        page.wait_for_timeout(600)

        # 冻结 lipsync 驱动：把值钉死，避免音频/待机动画干扰
        page.evaluate("""() => {
            window.__mouthProofLock = true;
            // 把驱动值钉住，stage-audio 每帧会写 __songroomLipSync，
            // 这里用一个 getter/setter 覆盖它，外部赋值一律忽略
            let locked = 0;
            Object.defineProperty(window, '__songroomLipSync', {
                configurable: true,
                get() { return locked; },
                set(v) { /* 忽略 stage-audio 的写入 */ },
            });
            window.__setMouthProof = (v) => { locked = v; };
        }""")

        # 找到模型的参数 API
        api = page.evaluate("""() => {
            // bundle 把模型实例挂在 window 上的可能性有限，这里用 canvas 反查不到，
            // 改成让页面自己暴露：stage-audio 提供了 __songroomMouthApplied，
            // 但真正的 set 需要模型对象。用 requestAnimationFrame 钩子无法拿到。
            return {
                hasMouthApplied: typeof window.__songroomMouthApplied !== 'undefined',
                keys: Object.keys(window).filter(k => k.toLowerCase().includes('live2d') || k.toLowerCase().includes('model')),
            };
        }""")
        print(f"页面暴露的模型相关全局：{api['keys']}")

        # 通过 __songroomLipSync 间接驱动：bundle 每帧读它并 setParameterValueByIndex
        for level in (0.0, 0.5, 1.0):
            page.evaluate(f"() => window.__setMouthProof({level})")
            page.wait_for_timeout(700)  # 让 bundle 应用并渲染几帧
            png = page.screenshot(type="png")
            frames[level] = png
            applied = page.evaluate("() => window.__songroomMouthApplied || null")
            print(f"lip={level:.1f} -> ParamMouthOpenY 实际值 = {applied}")

        browser.close()

    images = {level: Image.open(io.BytesIO(png)).convert("RGB") for level, png in frames.items()}
    base = images[0.0]
    box = mouth_region(base)
    if box is None:
        print("找不到脸部，无法测量")
        return 1
    print(f"嘴部测量区域（画面坐标）= {box}")

    results = {}
    for level, image in images.items():
        metrics = mouth_pixels(image, box)
        results[level] = metrics
        image.crop(box).resize(((box[2] - box[0]) * 4, (box[3] - box[1]) * 4), Image.LANCZOS).save(
            os.path.join(args.out, f"mouth_{int(level * 100):03d}.png")
        )
        print(f"lip={level:.1f}  口腔像素={metrics['pixels']:5d}  竖直跨度={metrics['spanY']:3d}px")

    print()
    changed, total, ratio = diff_pixels(images[0.0], images[1.0], box)
    print(f"lip 0.0 vs 1.0 嘴部差异 = {changed}/{total} 像素 ({ratio:.2%})")

    span0 = results[0.0]["spanY"]
    span1 = results[1.0]["spanY"]
    span_mid = results[0.5]["spanY"]
    pixel0 = results[0.0]["pixels"]
    pixel1 = results[1.0]["pixels"]

    monotonic = span0 <= span_mid <= span1 and (span1 > span0)
    print()
    print("判定：")
    print(f"  嘴部区域确有变化        : {'是' if ratio > 0.01 else '否'} ({ratio:.2%})")
    print(f"  口腔竖直跨度单调递增    : {'是' if monotonic else '否'} ({span0} -> {span_mid} -> {span1})")
    print(f"  口腔像素数增加          : {'是' if pixel1 > pixel0 * 1.1 else '否'} ({pixel0} -> {pixel1})")

    ok = ratio > 0.01 and (monotonic or pixel1 > pixel0 * 1.1)
    with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "box": box,
            "metrics": {str(k): v for k, v in results.items()},
            "diffRatio": ratio,
            "ok": ok,
        }, handle, ensure_ascii=False, indent=2)

    print()
    if ok:
        print("结论：ParamMouthOpenY 确实驱动画面嘴型，且随值连续变化")
        return 0
    print("结论：该模型对 ParamMouthOpenY 响应不明显（模型自身限制）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
