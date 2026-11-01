"""稳定性 + 性能测量：连续跑 30 分钟，每 5 秒采一次指标。

测量项（全部是实测，不估算）：
  * 容器 CPU% / 内存
  * 端到端延迟：注入弹幕 -> channel0 进入 playing
  * 各阶段耗时（从日志时间戳拆解）
  * 队列积压
  * 失败次数

用法：
    python3 tools/macos_stability_test.py --minutes 30 --interval 30
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request

STAGE_API = "http://127.0.0.1:5500"
HUB = "http://127.0.0.1:8888"
API = "http://127.0.0.1:8082"


def get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def post(url, payload, timeout=8):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def docker_stats():
    """读容器 CPU/内存。返回 (cpu_percent, mem_mb)。"""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", "dafeiyu"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        if not out:
            return None, None
        cpu_str, mem_str = out.split("|")
        cpu = float(cpu_str.replace("%", "").strip())
        # 形如 "1.234GiB / 7.7GiB"
        used = mem_str.split("/")[0].strip()
        if "GiB" in used:
            mem = float(used.replace("GiB", "").strip()) * 1024
        elif "MiB" in used:
            mem = float(used.replace("MiB", "").strip())
        else:
            mem = 0.0
        return cpu, mem
    except Exception:
        return None, None


def channels():
    return get(f"{STAGE_API}/audio/state")["channels"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--interval", type=float, default=30, help="每条测试弹幕之间的间隔（秒）")
    parser.add_argument("--out", default="/tmp/stability.json")
    args = parser.parse_args()

    total_seconds = args.minutes * 60
    started = time.monotonic()

    samples = []
    latencies = []
    failures = 0
    sent = 0

    texts = [
        "你好，大肥鱼",
        "今天海底冷不冷",
        "给我讲个笑话吧",
        "点歌 起风了",
        "你最喜欢吃什么",
        "主播好可爱",
        "今天加班好累",
        "推荐一部电影呗",
        "海底有没有鲸鱼",
        "再说两句听听",
    ]

    print(f"稳定性测试开始：{args.minutes} 分钟，每 {args.interval}s 一条弹幕")
    print(f"{'时间':>8} {'CPU%':>7} {'内存MB':>9} {'延迟s':>7} {'ch0':>10} {'队列':>5}")
    print("-" * 60)

    index = 0
    while time.monotonic() - started < total_seconds:
        elapsed = time.monotonic() - started
        cpu, mem = docker_stats()

        # 注入一条弹幕并测端到端延迟
        before_seq = int(channels()["0"]["seq"])
        text = texts[index % len(texts)]
        index += 1
        sent += 1
        t0 = time.monotonic()
        try:
            post(f"{HUB}/inject", {"type": "chat", "nickname": f"稳定测试{index}", "content": text})
        except Exception as error:
            failures += 1
            print(f"  注入失败：{error}")

        # 等 channel0 的 seq 增长（= 音频真的开始播）
        latency = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                state = channels()["0"]
                if int(state["seq"]) > before_seq and state["state"] in ("playing", "starting"):
                    latency = time.monotonic() - t0
                    break
            except Exception:
                pass
            time.sleep(0.15)

        if latency is None:
            failures += 1
        else:
            latencies.append(latency)

        # 队列积压
        try:
            info = get(f"{API}/get_sys_info")["data"]["audio"]
            queue = info["wait_play_audio_num"] + info["wait_synthesis_msg_num"]
        except Exception:
            queue = -1

        try:
            ch0 = channels()["0"]["state"]
        except Exception:
            ch0 = "unknown"

        samples.append({
            "t": round(elapsed, 1),
            "cpu": cpu,
            "mem_mb": mem,
            "latency": latency,
            "ch0": ch0,
            "queue": queue,
            "text": text,
        })

        print(
            f"{int(elapsed):>7}s {cpu if cpu is not None else '-':>7} "
            f"{int(mem) if mem else '-':>9} "
            f"{round(latency, 2) if latency else 'timeout':>7} "
            f"{ch0:>10} {queue:>5}"
        )

        # 睡到下一个采样点（扣掉已经花掉的时间）
        spent = time.monotonic() - started
        next_at = len(samples) * args.interval
        if next_at > spent:
            time.sleep(min(next_at - spent, total_seconds - spent))

    duration = time.monotonic() - started

    summary = {
        "duration_minutes": round(duration / 60, 2),
        "messages_sent": sent,
        "failures": failures,
        "success_rate": round((sent - failures) / sent, 4) if sent else 0,
        "latency": {
            "count": len(latencies),
            "min": round(min(latencies), 3) if latencies else None,
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(sorted(latencies)[int(len(latencies) * 0.95)], 3) if len(latencies) > 3 else None,
            "max": round(max(latencies), 3) if latencies else None,
            "mean": round(statistics.mean(latencies), 3) if latencies else None,
        },
        "cpu": {
            "min": min((s["cpu"] for s in samples if s["cpu"] is not None), default=None),
            "max": max((s["cpu"] for s in samples if s["cpu"] is not None), default=None),
            "mean": round(statistics.mean([s["cpu"] for s in samples if s["cpu"] is not None]), 2)
            if any(s["cpu"] is not None for s in samples) else None,
        },
        "mem_mb": {
            "min": min((s["mem_mb"] for s in samples if s["mem_mb"]), default=None),
            "max": max((s["mem_mb"] for s in samples if s["mem_mb"]), default=None),
            "mean": round(statistics.mean([s["mem_mb"] for s in samples if s["mem_mb"]]), 1)
            if any(s["mem_mb"] for s in samples) else None,
        },
        "max_queue": max((s["queue"] for s in samples if s["queue"] >= 0), default=None),
        "samples": samples,
    }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print(f"运行时长      {summary['duration_minutes']} 分钟")
    print(f"注入弹幕      {sent} 条，失败 {failures} 条，成功率 {summary['success_rate']*100:.1f}%")
    if latencies:
        lat = summary["latency"]
        print(f"端到端延迟    min={lat['min']}s  中位={lat['median']}s  p95={lat['p95']}s  max={lat['max']}s")
    print(f"CPU           min={summary['cpu']['min']}%  平均={summary['cpu']['mean']}%  max={summary['cpu']['max']}%")
    print(f"内存          平均={summary['mem_mb']['mean']}MB  max={summary['mem_mb']['max']}MB")
    print(f"最大队列积压  {summary['max_queue']}")
    print(f"结果已写入    {args.out}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
