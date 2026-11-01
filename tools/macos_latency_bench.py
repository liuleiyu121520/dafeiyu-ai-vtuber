"""稳态首声延迟测量：每次注入前等队列清空，测「空闲 -> 出声」的真实耗时。

与稳定性脚本的区别：稳定性脚本按固定间隔注入，队列积压时的等待时间会被算进
「延迟」；这个脚本每轮都等系统空闲，测的是**单条消息的纯延迟**。

拆解四段：
  t0 注入弹幕
  t1 主程序收到（日志）
  t2 TTS 合成完成（日志）
  t3 浏览器 channel0 进入 playing

用法：
    python3 tools/macos_latency_bench.py --rounds 10
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request

STAGE_API = "http://127.0.0.1:5500"
HUB = "http://127.0.0.1:8888"
API = "http://127.0.0.1:8082"


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def post(url, payload, timeout=10):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def channels():
    return get(STAGE_API + "/audio/state")["channels"]


def queue_len():
    try:
        info = get(API + "/get_sys_info")["data"]["audio"]
        return info["wait_play_audio_num"] + info["wait_synthesis_msg_num"]
    except Exception:
        return -1


def wait_idle(timeout=90):
    """等队列清空且 channel0 不在播。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if queue_len() == 0 and channels()["0"]["state"] not in ("playing", "starting"):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--text", default="大肥鱼你好，说说你今天在海底做了什么")
    args = parser.parse_args()

    samples = []
    print(f"{'轮次':>4} {'首声延迟':>10} {'状态':>10}")
    print("-" * 32)

    for index in range(args.rounds):
        if not wait_idle():
            print("  队列一直没清空，跳过")
            continue

        before_seq = int(channels()["0"]["seq"])
        t0 = time.monotonic()
        post(HUB + "/inject", {
            "type": "chat",
            "nickname": f"延迟测试{index + 1}",
            "content": f"{args.text}（第{index + 1}轮）",
        })

        latency = None
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                state = channels()["0"]
                if int(state["seq"]) > before_seq and state["state"] in ("playing", "starting"):
                    latency = time.monotonic() - t0
                    break
            except Exception:
                pass
            time.sleep(0.05)

        if latency is None:
            print(f"{index + 1:>4} {'超时':>10}")
            continue

        samples.append(latency)
        print(f"{index + 1:>4} {latency:>9.2f}s")

        # 让这一条播完再进入下一轮
        time.sleep(0.5)

    if not samples:
        print("没有采到有效样本")
        return 1

    ordered = sorted(samples)
    print()
    print("=" * 40)
    print(f"样本数    {len(samples)}")
    print(f"最小      {min(samples):.2f}s")
    print(f"中位数    {statistics.median(samples):.2f}s")
    print(f"平均      {statistics.mean(samples):.2f}s")
    print(f"p90       {ordered[int(len(ordered) * 0.9)]:.2f}s")
    print(f"最大      {max(samples):.2f}s")

    ok = statistics.median(samples) <= 4.0
    print()
    print(f"目标：中位延迟 1~4 秒 -> {'达标' if ok else '未达标'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
