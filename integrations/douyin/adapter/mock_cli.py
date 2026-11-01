"""模拟弹幕注入工具（Mock 模式）。

两种用法：
  1. 直接推本机 Hub（默认）—— 走 `ws://127.0.0.1:8888` 那条真实链路，
     和真实弹幕**完全共用**同一套业务入口，不是假的旁路。
  2. `--direct` 直接 POST 主程序 `/send` —— 不经过 Hub，用来单独验证主程序。

用法示例：
    python -m integrations.douyin.adapter.mock_cli --text "你好大肥鱼"
    python -m integrations.douyin.adapter.mock_cli --scenario burst5
    python -m integrations.douyin.adapter.mock_cli --scenario burst20 --interval 0.05
    python -m integrations.douyin.adapter.mock_cli --scenario gift
    python -m integrations.douyin.adapter.mock_cli --scenario follow
    python -m integrations.douyin.adapter.mock_cli --scenario enter
    python -m integrations.douyin.adapter.mock_cli --scenario like
    python -m integrations.douyin.adapter.mock_cli --scenario duplicate
    python -m integrations.douyin.adapter.mock_cli --scenario long
    python -m integrations.douyin.adapter.mock_cli --scenario mix
    python -m integrations.douyin.adapter.mock_cli --scenario offline   # 模拟采集器断线
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from integrations.douyin.adapter import events as ev  # noqa: E402

NICKNAMES = [
    "海底小虾米", "深海带鱼", "珊瑚姐姐", "小丑鱼阿丁", "珍珠贝",
    "水母飘飘", "章鱼哥", "鲸落无声", "海星小七", "气泡糖",
]

SENTENCES = [
    "你好，大肥鱼",
    "今天心情怎么样",
    "海底冷不冷啊",
    "给我讲个笑话吧",
    "你最喜欢吃什么",
    "能唱首歌吗",
    "点歌 起风了",
    "主播好可爱",
    "今天加班好累",
    "推荐一部电影呗",
]

LONG_TEXT = "大肥鱼你好，我是从很远的地方游过来看你的观众，今天想跟你聊聊海底的生活，也想问问你平时都在做什么，" * 3

GIFTS = ["小心心", "玫瑰", "棒棒糖", "能量石", "嘉年华"]


def post(url, payload, timeout=5):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def make_event(event_type, **kwargs):
    event = {
        "source": "douyin",
        "type": event_type,
        "nickname": kwargs.pop("nickname", random.choice(NICKNAMES)),
        "user_id": str(random.randint(10 ** 8, 10 ** 9)),
        "room_id": kwargs.pop("room_id", "278333"),
        "timestamp": int(time.time() * 1000),
        "raw": {"mock": True},
    }
    event.update(kwargs)
    return ev.normalize(event)


def send_event(event, hub_url, direct_url, use_direct):
    """推一条事件。返回是否成功。"""
    if use_direct:
        body = ev.to_main_program(event)
        if body is None:
            print(f"  [skip] {event['type']} 不进入主程序")
            return True
        try:
            post(direct_url, body)
            print(f"  [direct] {event['type']:<7} {event['nickname']}: {event['content'][:30]}")
            return True
        except Exception as error:
            print(f"  [FAIL] {error}")
            return False

    try:
        result = post(f"{hub_url}/inject", event)
        clients = result.get("clients", 0)
        note = "" if clients else "  ⚠ 没有消费者连上来（主程序启动了吗？）"
        print(f"  [hub] {event['type']:<7} {event['nickname']}: {event['content'][:30]}{note}")
        return True
    except Exception as error:
        print(f"  [FAIL] {error}")
        return False


def build_scenario(name):
    """返回 [(event, delay_before_seconds), ...]"""
    if name == "single":
        return [(make_event("chat", content=random.choice(SENTENCES)), 0)]
    if name == "burst5":
        return [(make_event("chat", content=random.choice(SENTENCES)), 0.4) for _ in range(5)]
    if name == "burst20":
        return [(make_event("chat", content=random.choice(SENTENCES)), 0.05) for _ in range(20)]
    if name == "duplicate":
        event = make_event("chat", content="重复弹幕测试", event_id="dup-001")
        return [(event, 0), (dict(event), 0.3), (dict(event), 0.3)]
    if name == "long":
        return [(make_event("chat", content=LONG_TEXT), 0)]
    if name == "gift":
        return [(make_event("gift", gift_name=random.choice(GIFTS), gift_count=3, repeat_count=3), 0)]
    if name == "follow":
        return [(make_event("follow"), 0)]
    if name == "enter":
        return [(make_event("enter"), 0)]
    if name == "like":
        return [(make_event("like", count=12), 0)]
    if name == "mix":
        return [
            (make_event("enter"), 0),
            (make_event("chat", content="你好，大肥鱼"), 0.5),
            (make_event("like", count=5), 0.3),
            (make_event("gift", gift_name="小心心", gift_count=1, repeat_count=1), 0.5),
            (make_event("chat", content="点歌 起风了"), 0.8),
            (make_event("follow"), 0.5),
        ]
    if name == "offline":
        return []
    raise ValueError(f"未知场景：{name}")


def main():
    parser = argparse.ArgumentParser(description="模拟弹幕注入")
    parser.add_argument("--text", help="直接发一条指定内容的弹幕")
    parser.add_argument("--nickname", default=None)
    parser.add_argument("--scenario", default=None, help="single/burst5/burst20/duplicate/long/gift/follow/enter/like/mix/offline")
    parser.add_argument("--interval", type=float, default=None, help="覆盖场景里的间隔（秒）")
    parser.add_argument("--hub", default=os.environ.get("BARRAGE_HUB_URL", "http://127.0.0.1:8888"))
    parser.add_argument("--direct", action="store_true", help="绕过 Hub，直接 POST 主程序 /send")
    parser.add_argument("--direct-url", default=os.environ.get("DAFEIYU_API_URL", "http://127.0.0.1:8082/send"))
    parser.add_argument("--repeat", type=int, default=1, help="整个场景重复几遍")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if args.text:
        plan = [(make_event("chat", content=args.text, nickname=args.nickname or random.choice(NICKNAMES)), 0)]
    elif args.scenario:
        if args.scenario == "offline":
            # 模拟采集器断线：Hub 端口被防火墙/进程退出时客户端看到的现象
            print("模拟弹幕采集器断线：")
            print(f"  1) 在容器里执行  pkill -f integrations.douyin.adapter.hub")
            print(f"  2) 主程序日志应出现 'WebSocket connection closed'，但不退出")
            print(f"  3) 重启 Hub 后主程序应自动重连（见 utils/platforms/dy.py 的 on_close）")
            return 0
        plan = build_scenario(args.scenario)
    else:
        parser.print_help()
        return 1

    total = 0
    failed = 0
    for round_index in range(max(1, args.repeat)):
        for event, delay in plan:
            wait = args.interval if args.interval is not None else delay
            if wait:
                time.sleep(wait)
            if send_event(event, args.hub, args.direct_url, args.direct):
                total += 1
            else:
                failed += 1

    print(f"\n共注入 {total} 条，失败 {failed} 条")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
